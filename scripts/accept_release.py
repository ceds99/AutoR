"""Accept a built wheel in two fresh environments outside the checkout.

No model calls unless --real-model is supplied. Failed attempts keep evidence;
rerunning requires a new output directory. This does not provision a new OS.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import time


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--wheel", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--real-model")
    p.add_argument("--seed-prompt", type=Path)
    args = p.parse_args()
    wheel, output = args.wheel.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    root = Path(tempfile.mkdtemp(prefix="autor-v01-acceptance-")).resolve()
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONNOUSERSITE"] = "1"
    receipt = {"status": "running", "wheel": wheel.name,
               "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
               "platform": platform.platform(), "workspace": str(root),
               "scope": "Two clean virtualenvs on the same host; shared existing Codex authentication. No second OS or Harbor job.",
               "commands": []}

    def save():
        (output / "acceptance.json").write_text(json.dumps(receipt, indent=2) + "\n")

    def command(name, argv, cwd, expected=0, timeout=700):
        print(json.dumps({"step": name, "event": "started"}), flush=True)
        start = time.monotonic()
        completed = subprocess.run([str(x) for x in argv], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
        logs = output / "commands"
        logs.mkdir(exist_ok=True)
        (logs / (name + ".stdout")).write_text(completed.stdout)
        (logs / (name + ".stderr")).write_text(completed.stderr)
        receipt["commands"].append({"step": name, "argv": [str(x) for x in argv], "cwd": str(cwd),
                                    "exit_code": completed.returncode, "expected_exit": expected,
                                    "seconds": time.monotonic() - start})
        save()
        if completed.returncode != expected:
            raise RuntimeError(name + " failed; inspect " + str(logs))
        print(json.dumps({"step": name, "event": "passed"}), flush=True)
        return completed.stdout

    try:
        for label in ("a", "b"):
            command("venv-" + label, ["uv", "venv", "--python", "3.12", root / label], root)
            command("install-" + label, ["uv", "pip", "install", "--python", root / label / "bin/python", wheel], root)
            code = ("import autor,json,sys;from pathlib import Path;from autor.storage import outer_fingerprint;"
                    "print(json.dumps({'module':autor.__file__,'python':sys.version,'outer_hash':outer_fingerprint(),"
                    "'skill':(Path(autor.__file__).parent/'skills/autor-research/SKILL.md').is_file(),"
                    "'license':(Path(autor.__file__).parent/'data/HUMANEVAL_LICENSE').is_file()}))")
            info = json.loads(command("import-" + label, [root / label / "bin/python", "-I", "-c", code], root))
            assert Path(info["module"]).is_relative_to(root / label), info
            assert info["skill"] and info["license"], info
            receipt["environment_" + label] = info
        assert receipt["environment_a"]["outer_hash"] == receipt["environment_b"]["outer_hash"]
        a, b = root / "a/bin/autor", root / "b/bin/autor"
        source = root / "source"
        source.mkdir()
        command("demo", [a, "demo", source / "demo"], source)
        command("demo-resume", [a, "run", source / "demo"], source)
        state = json.loads((source / "demo/run.json").read_text())
        assert state["proposals_used"] == 3 and state["evaluations_used"] == 4
        command("demo-export", [a, "export", source / "demo", source / "demo-export"], source)
        shutil.copytree(source / "demo-export", root / "moved-demo")
        if args.real_model:
            if args.seed_prompt is None:
                raise RuntimeError("--seed-prompt is required for the real failure-feedback acceptance")
            shutil.copyfile(args.seed_prompt, source / "seed.md")
            command("real-init", [a, "init-humaneval", source / "failure-loop", "--model", args.real_model,
                                   "--task", "HumanEval/0", "--seed-prompt", source / "seed.md"], source)
            command("real-doctor", [a, "doctor", "--run", source / "failure-loop"], source)
            command("real-loop", [a, "run", source / "failure-loop"], source)
            state = json.loads((source / "failure-loop/run.json").read_text())
            assert state["candidates"][0]["status"] == "evaluated" and state["candidates"][0]["score"] == 0
            assert len(state["candidates"]) == 2 and state["candidates"][1]["status"] == "evaluated"
            command("real-resume", [a, "run", source / "failure-loop"], source)
            resumed = json.loads((source / "failure-loop/run.json").read_text())
            assert (resumed["proposals_used"], resumed["evaluations_used"], resumed["seconds_used"]) == (
                state["proposals_used"], state["evaluations_used"], state["seconds_used"])
            command("real-export", [a, "export", source / "failure-loop", source / "real-export"], source)
            shutil.copytree(source / "real-export", root / "moved-real")
            receipt["real_loop"] = {"baseline": state["candidates"][0]["score"],
                "child": state["candidates"][1]["score"], "best": state["best"],
                "seconds": state["seconds_used"], "proposals": state["proposals_used"], "evaluations": state["evaluations_used"]}
        # Archive evidence, then remove the producer environment AND original paths.
        shutil.copytree(source, output / "source-evidence")
        shutil.rmtree(source)
        shutil.rmtree(root / "a")
        receipt["producer_environment_removed"] = True
        command("moved-demo-verify", [b, "verify-export", root / "moved-demo"], root)
        command("moved-demo-run", [b, "run-export", root / "moved-demo", "--work", root / "demo-results"], root)
        command("moved-demo-no-replay", [b, "run-export", root / "moved-demo", "--work", root / "demo-results"], root, expected=1)
        shutil.copytree(root / "moved-demo", root / "tampered")
        (root / "tampered/harness/prompt.md").write_text("tampered")
        command("tamper-rejected", [b, "verify-export", root / "tampered"], root, expected=1)
        if args.real_model:
            command("moved-real-verify", [b, "verify-export", root / "moved-real"], root)
            command("moved-real-run", [b, "run-export", root / "moved-real", "--work", root / "real-results"], root)
            measured = json.loads((root / "real-results/evaluation.json").read_text())
            receipt["export_rerun"] = {"score": measured["score"], "backend": measured["backend"],
                                        "synthetic": measured["synthetic"]}
        for name in ("moved-demo", "demo-results", "moved-real", "real-results"):
            if (root / name).exists():
                shutil.copytree(root / name, output / name)
        receipt["status"] = "passed"
        save()
        print(json.dumps(receipt), flush=True)
    except BaseException as error:
        receipt["status"] = "failed"
        receipt["error"] = str(error)
        save()
        raise


if __name__ == "__main__":
    main()
