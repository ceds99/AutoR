"""Proposer and evaluator boundaries. No evaluator is editable by the proposer."""

from collections import Counter
import json
from pathlib import Path
import shutil
import subprocess
import time
import sys
import hashlib

from .config import finite_number
from .process import run_process
from .storage import AutorError, copy_harness, read_json, write_json


PROPOSAL_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["diagnosis", "hypothesis", "edits"],
    "properties": {
        "diagnosis": {"type": "string"},
        "hypothesis": {"type": "string"},
        "edits": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["path", "content"],
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        }},
    },
}


def propose(config, parent, evidence, work, timeout):
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    if config["proposer"]["kind"] == "demo":
        # Scripted fixture, not an LLM and not a benchmark-quality claim.
        text = (Path(parent) / "prompt.md").read_text(encoding="utf-8")
        markers = ["Inspect the environment before acting.", "Verify the result before finishing."]
        marker = markers[(evidence["proposal_number"] - 1) % len(markers)]
        if marker in text:
            marker = next((m for m in markers if m not in text), "Keep task outputs concise.")
        return {"hypothesis": "DEMO fixture: add a scripted workflow instruction.",
                "edits": [{"path": "prompt.md", "content": text + "\n" + marker + "\n"}]}
    if config["proposer"].get("cli_version"):
        version = subprocess.run([config["proposer"].get("executable", "codex"), "--version"],
                                 capture_output=True, text=True, timeout=10)
        if version.returncode or version.stdout.strip() != config["proposer"]["cli_version"]:
            raise AutorError("Outer Codex CLI version changed")
    copy_harness(parent, work / "parent")
    write_json(work / "evidence.json", evidence)
    write_json(work / "schema.json", PROPOSAL_SCHEMA)
    skill = (Path(__file__).parent / "skills/autor-research/SKILL.md").read_text(encoding="utf-8")
    prompt = skill + "\n\nExperiment objective:\n" + config["objective"]
    prompt += "\nEditable files: " + json.dumps(config["editable_files"])
    prompt += "\nRequired text: " + json.dumps(config.get("required_text", {}))
    prompt += "\nRead parent/ and evidence.json. Return one JSON proposal using the output schema."
    if evidence.get("search_directive"):
        prompt += ("\nFollow search_directive in the evidence. failure_analysis: diagnose a concrete recurring failure; "
                   "verification: improve checks and recovery without embedding task answers; simplify: remove wasted "
                   "work or conflicting instructions while preserving successful behavior. For mix, synthesize the "
                   "two supplied parent harnesses into one coherent change, resolve conflicts, and explain which "
                   "idea came from each parent. Secondary parents are provided as editable_contents in evidence.json. "
                   "These are proposal directions, not guarantees of improvement. Do not change frozen resources.")
    (work / "request.txt").write_text(prompt, encoding="utf-8")
    from .permissions import config_args, verify_boundary
    executable = config["proposer"].get("executable", "codex")
    started = time.monotonic()
    verify_boundary(executable, work, min(timeout, 15))
    remaining = timeout - (time.monotonic() - started)
    argv = [executable, "exec", "--ignore-user-config",
            *config_args(work), "--skip-git-repo-check", "--ephemeral", "--json",
            "--model", config["proposer"]["model"],
            "-c", 'model_reasoning_effort=' + json.dumps(config["proposer"].get("reasoning_effort", "low")),
            "-c", 'approval_policy="never"',
            "--output-schema", str(work / "schema.json"),
            "--output-last-message", str(work / "response.json"), "-"]
    run_process(argv, work, work / "logs", remaining, stdin=prompt)
    proposal = read_json(work / "response.json")
    proposal["usage"] = codex_usage(work / "logs/stdout.log")
    return proposal


def codex_usage(path):
    totals = {}
    completed = False
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "turn.completed":
            completed = True
            for key, value in event.get("usage", {}).items():
                if isinstance(value, (int, float)):
                    totals[key] = totals.get(key, 0) + value
    return {"completed": completed, **totals}


def normalize_result(raw):
    """Success means complete measurement, including legitimate zero scores."""
    if not isinstance(raw, dict) or raw.get("status") != "ok":
        raise AutorError("Evaluator did not return status=ok: " + str(raw.get("error") if isinstance(raw, dict) else raw))
    tasks = raw.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise AutorError("Evaluator must return nonempty per-task results")
    keys = set()
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get("task_id"), str) or not task["task_id"]:
            raise AutorError("Each result needs a task_id")
        if type(task.get("attempt", 0)) is not int or task.get("attempt", 0) < 0:
            raise AutorError("Invalid task attempt")
        key = (task["task_id"], task.get("attempt", 0))
        if key in keys:
            raise AutorError("Duplicate task attempt: " + str(key))
        keys.add(key)
        if not finite_number(task.get("score")):
            raise AutorError("Missing or non-finite task score")
    # Never trust an independently supplied aggregate; v1 maximizes the mean.
    result = dict(raw)
    result["score"] = sum(t["score"] for t in tasks) / len(tasks)
    result["task_keys"] = sorted([list(k) for k in keys])
    return result


def harbor_config(settings, harness, work):
    return {
        "job_name": "evaluation", "jobs_dir": str(Path(work) / "jobs"),
        "n_attempts": settings.get("attempts", 1), "n_concurrent_trials": 1,
        "retry": {"max_retries": 0},
        "environment": {"type": "docker"},
        "agents": [{"name": "codex", "model_name": settings["model"],
                    "max_timeout_sec": settings.get("agent_timeout", 120),
                    "kwargs": {"version": settings["agent_version"],
                               "prompt_template_path": str(Path(harness) / settings.get("prompt_file", "prompt.md"))}}],
        "datasets": [{"name": settings["dataset"], "version": settings["dataset_version"],
                      "task_names": settings["task_names"]}],
    }


def parse_harbor(job_dir, settings):
    job_dir = Path(job_dir)
    summary = job_dir / "result.json"
    if not summary.is_file():
        raise AutorError("Harbor did not finish: job result.json is missing")
    job = read_json(summary)
    stats = job.get("stats", {})
    if any(stats.get(k, 0) for k in ("n_errored_trials", "n_running_trials", "n_pending_trials", "n_cancelled_trials")):
        raise AutorError("Harbor job contains incomplete or errored trials; inspect " + str(summary))
    tasks = []
    counts = Counter()
    for path in sorted(job_dir.glob("*/result.json")):
        data = read_json(path)
        if "task_name" not in data:
            continue
        if data.get("exception_info"):
            raise AutorError("Harbor trial failed: " + str(path))
        name = data["task_name"]
        rewards = (data.get("verifier_result") or {}).get("rewards") or {}
        score = rewards.get(settings["metric"])
        if not finite_number(score):
            raise AutorError("Missing numeric Harbor metric {} in {}".format(settings["metric"], path))
        feedback = []
        for filename in ("verifier/test-stdout.txt", "verifier/test-stderr.txt"):
            log = path.parent / filename
            if log.is_file():
                feedback.append(log.read_text(encoding="utf-8", errors="replace")[-6000:])
        tasks.append({"task_id": name, "attempt": counts[name], "score": score,
                      "metrics": rewards, "feedback": "\n".join(feedback),
                      "trial_dir": str(path.parent), "task_checksum": data.get("task_checksum"),
                      "agent_usage": data.get("agent_result")})
        counts[name] += 1
    expected = Counter({name: settings.get("attempts", 1) for name in settings["task_names"]})
    if counts != expected:
        raise AutorError("Harbor task coverage mismatch: expected {}, got {}".format(dict(expected), dict(counts)))
    return normalize_result({"status": "ok", "tasks": tasks,
                             "artifacts": [str(job_dir)], "cost_usd": stats.get("cost_usd"),
                             "backend": "harbor", "backend_version": "0.24.0"})


def evaluate(config, harness, work, timeout):
    settings = config["evaluator"]
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    if settings["kind"] == "demo":
        text = (Path(harness) / "prompt.md").read_text(encoding="utf-8")
        tasks = []
        for name, marker in (("inspect-fixture", "Inspect the environment"),
                             ("verify-fixture", "Verify the result")):
            present = marker in text
            tasks.append({"task_id": name, "attempt": 0, "score": float(present),
                          "feedback": "DEMO string fixture: " + ("marker present" if present else "missing " + marker)})
        return normalize_result({"status": "ok", "tasks": tasks, "backend": "demo", "synthetic": True})
    if settings["kind"] == "command":
        result_path = work / "result.json"
        substitutions = {"{harness}": str(harness), "{result}": str(result_path), "{work}": str(work),
                         "{timeout}": str(timeout)}
        argv = []
        for part in settings["argv"]:
            for old, new in substitutions.items():
                part = part.replace(old, new)
            argv.append(part)
        write_json(work / "request.json", {"harness": str(harness), "result": str(result_path)})
        run_process(argv, work, work / "logs", timeout)
        return normalize_result(read_json(result_path))
    executable = settings.get("executable", "harbor")
    check = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=10)
    if check.returncode or "0.24.0" not in check.stdout.split():
        raise AutorError("This adapter requires Harbor 0.24.0; run autor doctor")
    job_config = harbor_config(settings, harness, work)
    write_json(work / "job.json", job_config)
    run_process([executable, "run", "--config", str(work / "job.json")], work, work / "logs", timeout)
    return parse_harbor(work / "jobs/evaluation", settings)


def doctor(config):
    checks = []
    for kind, settings, default in (("proposer", config["proposer"], "codex"),
                                    ("evaluator", config["evaluator"], "harbor")):
        if settings["kind"] in ("codex", "harbor"):
            exe = settings.get("executable", default)
            checks.append({"check": kind + " executable", "ok": shutil.which(exe) is not None, "detail": exe})
    if config["evaluator"]["kind"] == "harbor":
        checks.append({"check": "Docker executable", "ok": shutil.which("docker") is not None,
                       "detail": "Docker daemon and model credentials also required; no model calls made"})
    if config["evaluator"]["kind"] == "command":
        settings = config["evaluator"]
        executable = settings["argv"][0]
        checks.append({"check": "command executable", "ok": shutil.which(executable) is not None, "detail": executable})
        portable = settings.get("portable", {})
        if portable.get("kind") == "humaneval":
            checks.append({"check": "macOS sandbox", "ok": sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file(),
                           "detail": "Native HumanEval requires macOS sandbox-exec and system Python"})
            dataset = Path(portable["dataset"])
            checks.append({"check": "pinned HumanEval data", "ok": dataset.is_file() and hashlib.sha256(dataset.read_bytes()).hexdigest() == portable["dataset_sha256"],
                           "detail": str(dataset)})
    return checks or [{"check": "demo", "ok": True, "detail": "No external dependencies or model calls"}]
