"""A small real Codex/HumanEval evaluator, invoked through the command protocol.

Uses original HumanEval tests on complete generated source, not official pass@k
aggregation. macOS sandbox-exec is required for executing the generated Python.
"""

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autor.adapters import codex_usage
from autor.process import run_process
from autor.storage import AutorError, digest, read_json, write_json


HUMANEVAL_REVISION = "6d43fb980f9fee3c892a914eda09951f772ad10d"
HUMANEVAL_URL = "https://raw.githubusercontent.com/openai/human-eval/" + HUMANEVAL_REVISION + "/data/HumanEval.jsonl.gz"
SOLUTION_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["solution"],
    "properties": {"solution": {"type": "string"}},
}


def test_python():
    direct = Path("/Library/Developer/CommandLineTools/usr/bin/python3")
    return str(direct) if direct.is_file() else "/usr/bin/python3"


def prepare_dataset(destination, task_ids):
    destination = Path(destination).resolve()
    if destination.exists():
        raise AutorError("Dataset destination already exists")
    request = urllib.request.Request(HUMANEVAL_URL, headers={"User-Agent": "AutoR/0.1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        compressed = response.read()
    rows = [json.loads(line) for line in gzip.decompress(compressed).decode().splitlines() if line]
    index = {row["task_id"]: row for row in rows}
    if not task_ids or len(set(task_ids)) != len(task_ids) or any(t not in index for t in task_ids):
        raise AutorError("HumanEval task IDs must be unique and exist in the pinned dataset")
    selected = [{k: index[t][k] for k in ("task_id", "prompt", "test", "entry_point")} for t in task_ids]
    # Never store or expose canonical solutions in the experiment.
    data = {"source": HUMANEVAL_URL, "revision": HUMANEVAL_REVISION,
            "upstream_sha256": hashlib.sha256(compressed).hexdigest(), "tasks": selected,
            "license": "MIT; https://github.com/openai/human-eval/blob/" + HUMANEVAL_REVISION + "/LICENSE"}
    write_json(destination, data)
    return hashlib.sha256(destination.read_bytes()).hexdigest()


def sandbox_policy():
    # Permit system Python's runtime reads, but exclude user files, scratch areas,
    # volumes and keychains. No network or filesystem writes (except /dev/null).
    denied = ["/Users", str(Path.home()), "/Volumes", "/Library/Keychains",
              "/private/tmp", "/private/var/folders"]
    rules = " ".join("(subpath " + json.dumps(p) + ")" for p in dict.fromkeys(denied))
    return ("(version 1)(deny default)(allow process*)(allow file-read*)"
            "(deny file-read-data " + rules + ")"
            "(allow sysctl-read)(allow mach-lookup)(allow file-write* (literal \"/dev/null\"))")


def execute_python(source, timeout=5):
    if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").is_file():
        raise AutorError("Native HumanEval execution requires macOS sandbox-exec; use Harbor on other systems")
    with tempfile.TemporaryDirectory(prefix="autor-grade-") as temp:
        return subprocess.run(["/usr/bin/sandbox-exec", "-p", sandbox_policy(),
                               test_python(), "-I", "-"], input=source, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=temp,
                              env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "en_US.UTF-8"},
                              timeout=timeout)


def grade(solution, task, timeout=5):
    marker = "AUTOR_TESTS_COMPLETED_" + uuid.uuid4().hex
    program = solution + "\n\n" + task["test"] + "\ncheck(" + task["entry_point"] + ")\nprint(" + repr(marker) + ")\n"
    try:
        result = execute_python(program, timeout)
    except subprocess.TimeoutExpired:
        return {"score": 0.0, "outcome": "test_timeout", "feedback": "Generated solution exceeded the fixed test execution limit."}
    if result.returncode < 0:
        raise AutorError("Test runner was terminated by a signal; measurement unavailable")
    passed = result.returncode == 0 and marker in result.stdout.splitlines()
    # Do not feed hidden assertions, expected answers, or test source back to the agent.
    error_line = next((line for line in reversed(result.stderr.splitlines()) if line and not line.startswith(" ")), "")
    error_type = error_line.split(":", 1)[0]
    if not error_type.replace("_", "").isalnum():
        error_type = "test_failure"
    return {"score": float(passed), "outcome": "passed" if passed else (error_type or "incomplete_execution"),
            "feedback": "Original HumanEval tests passed." if passed else "Original HumanEval tests failed ({}).".format(error_type or "incomplete execution"),
            "exit_code": result.returncode}


def evaluate(harness, work, dataset, dataset_sha256):
    harness, work, dataset = Path(harness).resolve(), Path(work).resolve(), Path(dataset).resolve()
    if hashlib.sha256(dataset.read_bytes()).hexdigest() != dataset_sha256:
        raise AutorError("Pinned benchmark data changed")
    runtime = read_json(harness / "runtime.json")
    executable = runtime.get("executable", "codex")
    version = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=10)
    if version.returncode or version.stdout.strip() != runtime["cli_version"]:
        raise AutorError("Inner Codex CLI version changed")
    probe = execute_python("print('AUTOR_SANDBOX_READY')\n")
    if probe.returncode or probe.stdout.strip() != "AUTOR_SANDBOX_READY":
        raise AutorError("Local Python sandbox precheck failed")
    benchmark = read_json(dataset)
    template = (harness / "prompt.md").read_text(encoding="utf-8")
    template_hash = hashlib.sha256(template.encode()).hexdigest()
    tasks = []
    work.mkdir(parents=True, exist_ok=True)
    for task in benchmark["tasks"]:
        trial = work / task["task_id"].replace("/", "-")
        identity = digest({"task": task, "runtime": runtime, "prompt_sha256": template_hash})
        existing = trial / "result.json"
        if existing.is_file():
            saved = read_json(existing)
            if saved.get("trial_identity") != identity:
                raise AutorError("Completed trial identity changed")
            tasks.append(saved)
            continue
        if (trial / "started.json").exists():
            raise AutorError("Trial has an uncertain prior call; refusing automatic replay")
        trial.mkdir(parents=True, exist_ok=True)
        instruction = "Implement the function described below. Return the complete Python source including necessary imports.\n\n" + task["prompt"]
        prompt = template.replace("{{ instruction }}", instruction)
        if instruction not in prompt:
            raise AutorError("Inner prompt did not receive the task")
        (trial / "request.txt").write_text(prompt, encoding="utf-8")
        write_json(trial / "schema.json", SOLUTION_SCHEMA)
        argv = [executable, "exec", "--ignore-user-config", "--sandbox", "read-only", "--skip-git-repo-check",
                "--ephemeral", "--json", "--model", runtime["model"],
                "-c", 'model_reasoning_effort=' + json.dumps(runtime.get("reasoning_effort", "low")),
                "-c", 'approval_policy="never"', "--output-schema", str(trial / "schema.json"),
                "--output-last-message", str(trial / "response.json"), "-"]
        write_json(trial / "started.json", {"trial_identity": identity, "started_at": time.time()})
        started = time.monotonic()
        run_process(argv, trial, trial / "logs", runtime.get("timeout", 90), stdin=prompt)
        usage = codex_usage(trial / "logs/stdout.log")
        if not usage["completed"]:
            raise AutorError("Inner did not emit a completed turn")
        response = read_json(trial / "response.json")
        solution = response.get("solution")
        if not isinstance(solution, str) or not solution.strip():
            raise AutorError("Inner returned no usable solution")
        (trial / "solution.py").write_text(solution, encoding="utf-8")
        score = grade(solution, task, runtime.get("test_timeout", 5))
        row = {"task_id": task["task_id"], "attempt": 0, "task_checksum": digest(task),
               "trial_identity": identity, "trial_dir": str(trial), "agent_usage": usage,
               "seconds": time.monotonic() - started,
               "activation": {"file": "prompt.md", "sha256": template_hash, "used": True,
                              "request_sha256": hashlib.sha256(prompt.encode()).hexdigest()}, **score}
        write_json(existing, row)
        tasks.append(row)
    return {"status": "ok", "backend": "codex-humaneval-local", "synthetic": False,
            "benchmark_revision": benchmark["revision"], "dataset_sha256": dataset_sha256,
            "model": runtime["model"], "cli_version": runtime["cli_version"], "tasks": tasks,
            "test_python": test_python(),
            "measurement": "Two-arm smoke on original tests; no full-suite pass@k or generalization claim"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--dataset-sha256", required=True)
    args = parser.parse_args()
    write_json(args.result, evaluate(args.harness, args.work, args.dataset, args.dataset_sha256))


if __name__ == "__main__":
    main()
