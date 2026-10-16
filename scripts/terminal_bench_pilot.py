"""A bounded, baseline-only Terminal-Bench pilot. No outer/proposer is invoked.

Use an independently curated, salted holdout commitment before preparing a run.
This script never reads holdout task names or results.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autor import adapters
from autor.config import finite_number
from autor.presets import SEED_PROMPT
from autor.process import run_process
from autor.storage import AutorError, digest, read_json, write_json


REVISION = "69671fbaac6d67a7ef0dfec016cc38a64ef7a77c"
SEARCH_TASKS = ["fix-git", "log-summary-date-ranges", "nginx-request-logging", "cancel-async-tasks"]
APT_CONFIG = 'Acquire::http::Timeout "15";\nAcquire::https::Timeout "15";\nAcquire::Retries "0";\n'


def runtime_digest(root):
    """Hash trusted runtime files and internal symlinks; reject escapes/special files."""
    root = Path(root).resolve()
    entries = {}
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            if root not in path.resolve().parents:
                raise AutorError("Runtime symlink escapes its directory")
            entries[name] = {"link": os.readlink(path)}
        elif path.is_file():
            entries[name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                             "executable": bool(path.stat().st_mode & 0o111)}
        elif not path.is_dir():
            raise AutorError("Runtime contains a special file")
    return digest(entries)


def prepare(root, model, version, holdout_commitment, bounded_apt=False, node_runtime=None,
            task_names=None, agent_timeout=180, max_seconds=1200, apt_proxy=None, network_proxy=None):
    root = Path(root).resolve()
    if root.exists():
        raise AutorError("Choose a new pilot directory")
    if not re.fullmatch(r"[a-f0-9]{64}", holdout_commitment):
        raise AutorError("Supply the SHA-256 commitment of a salted, separately stored holdout manifest")
    if not model.strip() or not re.fullmatch(r"\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?", version):
        raise AutorError("An explicit model and exact Codex npm version are required")
    tasks = list(SEARCH_TASKS if task_names is None else task_names)
    if (not 1 <= len(tasks) <= 6 or any(not isinstance(t, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", t) for t in tasks)
            or len(set(tasks)) != len(tasks)):
        raise AutorError("Choose 1–6 unique exact search task names")
    if any(not finite_number(v) or v <= 0 for v in (agent_timeout, max_seconds)):
        raise AutorError("Agent and pilot time budgets must be positive finite seconds")
    if apt_proxy is not None and not bounded_apt:
        raise AutorError("APT proxy requires --bounded-apt")
    if network_proxy is not None and node_runtime is None:
        raise AutorError("Network proxy requires --node-runtime for the container-only profile")
    for proxy in (apt_proxy, network_proxy):
        if proxy is None:
            continue
        parsed = urlsplit(proxy)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ("", "/") or any(c.isspace() for c in proxy)):
            raise AutorError("Proxy must be an HTTP(S) origin without credentials")
    harness = root / "harness"
    harness.mkdir(parents=True)
    (harness / "prompt.md").write_text(SEED_PROMPT, encoding="utf-8")
    settings = {"kind": "harbor", "model": model, "agent_version": version,
                "dataset": "terminal-bench", "dataset_version": "2.0", "task_names": tasks,
                "attempts": 1, "metric": "reward", "agent_timeout": agent_timeout}
    job = adapters.harbor_config(settings, harness, root)
    # Pin explicit task commits instead of resolving a mutable remote registry at run time.
    job["datasets"] = []
    job["tasks"] = [{"path": name, "git_url": "https://github.com/harbor-framework/terminal-bench-2.git",
                     "git_commit_id": REVISION, "source": "terminal-bench-2.0-pilot"} for name in tasks]
    job["agents"][0]["kwargs"]["reasoning_effort"] = "low"
    overrides = {}
    if bounded_apt:
        apt_path = root / "runtime/apt-network.conf"
        apt_path.parent.mkdir(parents=True, exist_ok=True)
        apt_config = APT_CONFIG
        if apt_proxy:
            apt_config += 'Acquire::http::Proxy ' + json.dumps(apt_proxy) + ';\n'
            apt_config += 'Acquire::https::Proxy ' + json.dumps(apt_proxy) + ';\n'
        apt_path.write_text(apt_config, encoding="utf-8")
        overrides["runtime/apt-network.conf"] = hashlib.sha256(apt_path.read_bytes()).hexdigest()
        job["environment"]["mounts"] = [{"type": "bind", "source": str(apt_path),
            "target": "/etc/apt/apt.conf.d/99autor-pilot-network", "read_only": True}]
    if node_runtime:
        source = Path(node_runtime).resolve()
        expected = runtime_digest(source)
        destination = root / "runtime/node"
        shutil.copytree(source, destination, symlinks=True)
        if runtime_digest(destination) != expected:
            raise AutorError("Node runtime changed while staging")
        overrides["runtime/node"] = expected
        job["environment"].setdefault("mounts", []).append({"type": "bind", "source": str(destination),
            "target": "/opt/autor-node", "read_only": True})
        job["environment"]["env"] = {"BASH_ENV": "/etc/profile.d/autor-node.sh"}
        # Source this in container bash commands without replacing the host Docker client PATH.
        profile_path = root / "runtime/node-profile.sh"
        profile = ('export PATH="/opt/autor-node/bin:$PATH"\n'
                   'if [ -r /opt/autor-node/ca-certificates.crt ]; then\n'
                   '  export SSL_CERT_FILE=/opt/autor-node/ca-certificates.crt\n'
                   'fi\n')
        if network_proxy:
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                profile += 'export ' + name + '=' + shlex.quote(network_proxy) + '\n'
            profile += 'export NO_PROXY=localhost,127.0.0.1,::1\nexport no_proxy="$NO_PROXY"\n'
        profile_path.write_text(profile, encoding="utf-8")
        overrides["runtime/node-profile.sh"] = hashlib.sha256(profile_path.read_bytes()).hexdigest()
        job["environment"]["mounts"].append({"type": "bind", "source": str(profile_path),
            "target": "/etc/profile.d/autor-node.sh", "read_only": True})
    write_json(root / "search-job.json", job)
    metadata = {"schema_version": 1, "mode": "baseline-only", "evaluator": settings,
                "task_revision": REVISION, "holdout_count": 2, "holdout_commitment": holdout_commitment,
                "job_hash": digest(job), "prompt_sha256": hashlib.sha256((harness / "prompt.md").read_bytes()).hexdigest(),
                "max_seconds": max_seconds, "runtime_overrides": overrides, "limitations": "Short-budget pilot, not an official leaderboard score. No holdout data is loaded and no proposer is called."}
    write_json(root / "pilot.json", metadata)
    write_json(root / "contract.json", {"pilot_sha256": digest(metadata)})
    return metadata


def load(root):
    root = Path(root).resolve()
    metadata = read_json(root / "pilot.json")
    if digest(metadata) != read_json(root / "contract.json")["pilot_sha256"]:
        raise AutorError("Pilot contract changed")
    if digest(read_json(root / "search-job.json")) != metadata["job_hash"]:
        raise AutorError("Pinned Harbor job changed")
    if hashlib.sha256((root / "harness/prompt.md").read_bytes()).hexdigest() != metadata["prompt_sha256"]:
        raise AutorError("Baseline prompt changed")
    for name, expected in metadata.get("runtime_overrides", {}).items():
        if name == "runtime/node":
            actual = runtime_digest(root / name)
        elif name in ("runtime/apt-network.conf", "runtime/node-profile.sh"):
            actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
        else:
            raise AutorError("Unknown runtime override")
        if actual != expected:
            raise AutorError("Pinned runtime override changed")
    return metadata


def preflight(root):
    load(root)
    checks = []
    for name, argv, expected in [
        ("Harbor 0.24.0", ["harbor", "--version"], "0.24.0"),
        ("Linux Docker daemon", ["docker", "info", "--format", "{{.OSType}}"], "linux"),
        ("Docker Compose", ["docker", "compose", "version", "--short"], None),
    ]:
        if shutil.which(argv[0]) is None:
            checks.append({"check": name, "ok": False, "detail": "Executable unavailable"})
            continue
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=15)
            ok = result.returncode == 0 and (expected in result.stdout.split() if expected else bool(result.stdout.strip()))
            checks.append({"check": name, "ok": ok, "detail": "Ready" if ok else "Version mismatch or service unavailable"})
        except (OSError, subprocess.TimeoutExpired):
            checks.append({"check": name, "ok": False, "detail": "Preflight failed or timed out"})
    explicit = os.environ.get("CODEX_AUTH_JSON_PATH")
    forced = os.environ.get("CODEX_FORCE_AUTH_JSON", "").lower() in ("1", "true", "yes", "on")
    auth_file = Path(explicit).expanduser() if explicit else Path.home() / ".codex/auth.json"
    auth_ok = auth_file.is_file() if explicit or forced else bool(os.environ.get("OPENAI_API_KEY"))
    checks.append({"check": "Harbor inner authentication configured", "ok": auth_ok,
                   "detail": "Presence check only; no credential values read and no model request made"})
    return {"ready": all(c["ok"] for c in checks), "checks": checks}


def run(root):
    root = Path(root).resolve()
    metadata = load(root)
    readiness = preflight(root)
    write_json(root / "preflight.json", readiness)
    if not readiness["ready"]:
        raise AutorError("Pilot prerequisites unavailable; see preflight.json. No evaluation started.")
    # O_EXCL makes concurrent runs and uncertain-call replay fail before execution.
    with (root / "started.json").open("x", encoding="utf-8") as handle:
        json.dump({"started_at": time.time(), "pilot_hash": digest(metadata)}, handle)
    try:
        seconds = run_process(["harbor", "run", "--config", str(root / "search-job.json")],
                              root, root / "logs", metadata["max_seconds"])
        load(root)
        result = adapters.parse_harbor(root / "jobs/evaluation", metadata["evaluator"])
        result.update(pilot="baseline-only", seconds=seconds, proposer_calls=0,
                      holdout_evaluated=False, holdout_commitment=metadata["holdout_commitment"])
        write_json(root / "baseline-result.json", result)
        return {"status": "ok", "score": result["score"], "failed_tasks": [t["task_id"] for t in result["tasks"] if t["score"] == 0],
                "proposer_calls": 0, "holdout_evaluated": False}
    except BaseException as error:
        write_json(root / "failure.json", {"status": "error", "error": str(error) or type(error).__name__, "error_type": type(error).__name__,
                    "note": "Not a zero-score measurement. Inspect Harbor trials; uncertain calls are not automatically replayed."})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("prepare")
    init.add_argument("root", type=Path)
    init.add_argument("--inner-model", required=True)
    init.add_argument("--inner-version", required=True)
    init.add_argument("--holdout-commitment", required=True)
    init.add_argument("--node-runtime", type=Path, help="Stage a trusted extracted Linux Node distribution as a hashed read-only runtime mount")
    init.add_argument("--bounded-apt", action="store_true", help="Freeze an APT 15-second connection timeout with no retries; recorded runtime deviation")
    init.add_argument("--apt-proxy", help="Optional credential-free proxy origin reachable from the container; requires --bounded-apt and is frozen in the runtime receipt")
    init.add_argument("--network-proxy", help="Optional credential-free HTTP(S) proxy for container commands only; requires --node-runtime; loopback traffic bypasses it")
    init.add_argument("--task", action="append", help="Exact search task name; repeat for 1–6 tasks. Defaults to the original four-task pilot.")
    init.add_argument("--agent-timeout", type=float, default=180)
    init.add_argument("--max-seconds", type=float, default=1200)
    for command in ("check", "run"):
        sub.add_parser(command).add_argument("root", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.root, args.inner_model, args.inner_version, args.holdout_commitment,
                             args.bounded_apt, args.node_runtime, args.task, args.agent_timeout, args.max_seconds,
                             args.apt_proxy, args.network_proxy)
        elif args.command == "check":
            result = preflight(args.root)
            write_json(args.root / "preflight.json", result)
        else:
            result = run(args.root)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result.get("ready") is False else 0
    except (AutorError, OSError, ValueError) as error:
        print("Terminal-Bench pilot: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
