import argparse
import json
from pathlib import Path
import sys
import subprocess
import tempfile

from . import adapters, engine
from .config import validate
from .presets import SEED_PROMPT, demo_config, harbor_preset, humaneval_preset
from .storage import AutorError, read_json, write_json


def output(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)


def nonnegative(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return number


def summary(state):
    return {"best": state["best"], "stop_reason": state["stop_reason"],
            "strategy": state["config"]["strategy"],
            "synthetic": state["config"]["evaluator"]["kind"] == "demo",
            **({"search": engine.beam_view(state)} if state["config"]["strategy"] == "beam" else {}),
            "budget": {k: state[k] for k in ("proposals_used", "evaluations_used", "seconds_used")},
            "candidates": [{k: c[k] for k in ("id", "parents", "status", "score", "error") if k in c}
                           for c in state["candidates"]]}


def initialize_preset(run, config):
    with tempfile.TemporaryDirectory(prefix="autor-seed-") as temp:
        seed = Path(temp)
        (seed / "prompt.md").write_text(SEED_PROMPT, encoding="utf-8")
        write_json(seed / "runtime.json", {"description": "Fixed runtime; prompt.md is the only mutable surface",
                                          "evaluator": config["evaluator"]})
        return engine.initialize(run, seed, config)


def parser():
    p = argparse.ArgumentParser(description="AutoR: a fixed outer for versioned inner harnesses")
    sub = p.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="Run a synthetic fixture without model calls or Docker")
    demo.add_argument("run", type=Path)
    demo.add_argument("--strategy", choices=["hill_climb", "branching", "beam"], default="branching")
    demo.add_argument("--proposals", type=nonnegative, help="Defaults to 6 for beam, 3 otherwise")
    demo.add_argument("--beam-width", type=nonnegative)
    demo.add_argument("--children-per-parent", type=nonnegative)
    init = sub.add_parser("init", help="Import an existing seed and freeze a JSON experiment config")
    init.add_argument("run", type=Path)
    init.add_argument("--seed", type=Path, required=True)
    init.add_argument("--config", type=Path, required=True)
    harbor = sub.add_parser("init-harbor", help="Prepare a small real experiment; does not execute it")
    harbor.add_argument("run", type=Path)
    harbor.add_argument("--outer-model", required=True)
    harbor.add_argument("--inner-model", required=True)
    harbor.add_argument("--inner-version", required=True, help="Exact npm @openai/codex version")
    harbor.add_argument("--task", action="append", required=True, help="Exact Terminal-Bench 2.0 task name")
    harbor.add_argument("--strategy", choices=["hill_climb", "branching", "beam"], default="branching")
    human = sub.add_parser("init-humaneval", help="Prepare a real local macOS smoke experiment using pinned HumanEval tasks")
    human.add_argument("run", type=Path)
    human.add_argument("--model", required=True, help="Explicit Codex model for both outer and inner")
    human.add_argument("--task", action="append", help="Defaults to HumanEval/0 and HumanEval/1")
    human.add_argument("--seed-prompt", type=Path, help="Import a custom prompt; required task placeholder and benchmark restriction must remain")
    run = sub.add_parser("run", help="Start/resume within the frozen budget")
    run.add_argument("run", type=Path)
    run.add_argument("--steps", type=nonnegative, help="At most this many additional proposals; 0 measures only ready candidates")
    status = sub.add_parser("status", help="Inspect lineage, results and budget")
    status.add_argument("run", type=Path)
    export = sub.add_parser("export", help="Export a measured harness and provenance")
    export.add_argument("run", type=Path)
    export.add_argument("destination", type=Path)
    export.add_argument("--candidate", help="Defaults to the best measured candidate")
    verify = sub.add_parser("verify-export", help="Check an exported bundle without model calls")
    verify.add_argument("bundle", type=Path)
    replay = sub.add_parser("run-export", help="Evaluate an exported harness independently; real backends incur model usage")
    replay.add_argument("bundle", type=Path)
    replay.add_argument("--work", type=Path, required=True, help="New output directory outside the bundle; never silently replays calls")
    doctor = sub.add_parser("doctor", help="Read-only prerequisite checks; no model calls")
    group = doctor.add_mutually_exclusive_group(required=True)
    group.add_argument("--run", type=Path)
    group.add_argument("--config", type=Path)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "demo":
            proposals = args.proposals if args.proposals is not None else (6 if args.strategy == "beam" else 3)
            config = demo_config(args.strategy, proposals)
            settings = {k: getattr(args, k) for k in ("beam_width", "children_per_parent") if getattr(args, k) is not None}
            if settings:
                config["search"] = settings
            initialize_preset(args.run, config)
            state = engine.Experiment(args.run, output).run()
            output(summary(state))
            return 0 if state["best"] is not None else 1
        if args.command == "init":
            output(summary(engine.initialize(args.run, args.seed, read_json(args.config))))
        elif args.command == "init-harbor":
            config = harbor_preset(args.outer_model, args.inner_model, args.inner_version, args.task, args.strategy)
            output(summary(initialize_preset(args.run, config)))
        elif args.command == "init-humaneval":
            from .humaneval import prepare_dataset
            if args.run.exists():
                raise AutorError("Experiment path already exists")
            version = subprocess.run(["codex", "--version"], capture_output=True, text=True, timeout=10)
            if version.returncode:
                raise AutorError("Cannot determine Codex CLI version")
            dataset = args.run.resolve().parent / (args.run.name + "-benchmark.json")
            dataset_hash = prepare_dataset(dataset, args.task or ["HumanEval/0", "HumanEval/1"])
            config = humaneval_preset(args.model, version.stdout.strip(), dataset, dataset_hash)
            with tempfile.TemporaryDirectory(prefix="autor-human-seed-") as temp:
                seed = Path(temp)
                prompt = args.seed_prompt.read_text(encoding="utf-8") if args.seed_prompt else (
                    "Solve the Python task and return the complete source in the required JSON schema.\n"
                    "Do not look up benchmark tests or reference solutions.\n\n{{ instruction }}\n")
                (seed / "prompt.md").write_text(prompt, encoding="utf-8")
                write_json(seed / "runtime.json", {"model": args.model, "cli_version": version.stdout.strip(),
                                                    "reasoning_effort": "low", "timeout": 90, "test_timeout": 5})
                output(summary(engine.initialize(args.run, seed, config)))
        elif args.command == "run":
            state = engine.Experiment(args.run, output).run(args.steps)
            output(summary(state))
            if state["stop_reason"] == "interrupted":
                return 130
            if state["best"] is None or any(c["status"] in ("error", "interrupted") for c in state["candidates"]):
                return 1
        elif args.command == "status":
            output(summary(engine.load(args.run)))
        elif args.command == "export":
            chosen = engine.export(args.run, args.destination, args.candidate)
            output({"exported": chosen, "destination": str(args.destination.resolve())})
        elif args.command == "doctor":
            config = engine.load(args.run)["config"] if args.run else validate(read_json(args.config))
            checks = adapters.doctor(config)
            output(checks)
            return 0 if all(c["ok"] for c in checks) else 1
        elif args.command == "verify-export":
            from .bundles import verify_export
            output(verify_export(args.bundle))
        elif args.command == "run-export":
            from .bundles import run_export
            output(run_export(args.bundle, args.work))
        return 0
    except (AutorError, ValueError, KeyError, TypeError, OSError) as e:
        print("AutoR: " + str(e), file=sys.stderr)
        return 1
