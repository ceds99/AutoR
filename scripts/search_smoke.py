#!/usr/bin/env python3
"""Exercise real controller code with a deliberately scripted search landscape.

No models or benchmark tasks are called. Scores encode a low-scoring branch
whose descendants win, solely to inspect search mechanics and provenance.
"""

import argparse
import hashlib
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autor import adapters, engine
from autor.cli import initialize_preset, summary
from autor.presets import demo_config
from autor.storage import write_json


PREFIX = "Fixture branch: "
SCORES = {"": 0.5, "1": 0.7, "2": 0.4, "1.1": 0.72, "1.2": 0.73,
          "2.1": 0.9, "2.2": 1.0}


def trace(harness):
    lines = (Path(harness) / "prompt.md").read_text().splitlines()
    return next((line[len(PREFIX):] for line in reversed(lines) if line.startswith(PREFIX)), "")


def fixture_propose(config, parent, evidence, work, timeout):
    parent_id = evidence["parent"]["id"]
    ordinal = 1 + sum(parent_id in c["parents"] for c in evidence["history"])
    branch = ".".join(filter(None, [trace(parent), str(ordinal)]))
    return {"diagnosis": "Synthetic landscape; no real agent diagnosis.",
            "hypothesis": "Exercise branch " + branch,
            "edits": [{"path": "prompt.md", "content": (parent / "prompt.md").read_text()
                       + "\n" + PREFIX + branch + "\n"}]}


def fixture_evaluate(config, harness, work, timeout):
    branch = trace(harness)
    score = SCORES.get(branch, 0.74 if branch.startswith("1.") else 0.6)
    return {"status": "ok", "synthetic": True, "backend": "scripted-search-landscape",
            "tasks": [{"task_id": "synthetic-landscape", "score": score,
                       "feedback": "Score assigned by fixture, not a benchmark result."}]}


def run_comparison(root):
    root = Path(root)
    if root.exists():
        raise ValueError("Choose a new output directory")
    root.mkdir(parents=True)
    results = {}
    with patch.object(adapters, "propose", fixture_propose), patch.object(adapters, "evaluate", fixture_evaluate):
        for strategy in ("hill_climb", "branching", "beam"):
            run = root / strategy
            initialize_preset(run, demo_config(strategy, 6))
            # Exercise a restart in the middle of the first generation.
            engine.Experiment(run).run(1)
            state = engine.Experiment(run).run()
            results[strategy] = summary(state)
    evidence = {"synthetic": True, "model_calls": 0,
                "purpose": "Controller/search verification only; no optimization or generalization claim.",
                "fixture_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "outer_hash": state["outer_hash"], "results": results}
    write_json(root / "comparison.json", evidence)
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    run_comparison(args.output)
    print(args.output / "comparison.json")
