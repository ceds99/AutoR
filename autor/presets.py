"""Small starting contracts; not a MA implementation."""

from pathlib import Path
import sys


SEED_PROMPT = """Complete the task using the available tools.

{{ instruction }}
"""


def demo_config(strategy="branching", proposals=3):
    return {
        "schema_version": 1,
        "objective": "Exercise the outer loop with synthetic string fixtures; this is not agent evaluation.",
        "strategy": strategy,
        "editable_files": ["prompt.md"],
        "required_text": {"prompt.md": ["{{ instruction }}"]},
        "proposer": {"kind": "demo"}, "evaluator": {"kind": "demo"},
        "budget": {"max_proposals": proposals, "max_evaluations": proposals + 1,
                   "max_seconds": 60, "proposal_timeout": 10, "evaluation_timeout": 10},
    }


def harbor_preset(outer_model, inner_model, inner_version, tasks, strategy="branching"):
    return {
        "schema_version": 1,
        "objective": "Improve the reusable task workflow of the inner Codex harness on terminal tasks. "
                     "Keep the model, runtime, task set, verifier and resource limits fixed.",
        "strategy": strategy,
        "editable_files": ["prompt.md"],
        "required_text": {"prompt.md": ["{{ instruction }}"]},
        "proposer": {"kind": "codex", "model": outer_model},
        "evaluator": {"kind": "harbor", "agent": "codex", "model": inner_model,
                      "agent_version": inner_version, "dataset": "terminal-bench",
                      "dataset_version": "2.0", "task_names": tasks, "attempts": 1,
                      "metric": "reward", "prompt_file": "prompt.md", "agent_timeout": 120},
        # A smoke experiment: one proposal plus baseline, no automatic retries.
        "budget": {"max_proposals": 1, "max_evaluations": 2, "max_seconds": 900,
                   "proposal_timeout": 120, "evaluation_timeout": 360},
    }


def humaneval_preset(model, cli_version, dataset, dataset_sha256):
    return {
        "schema_version": 1,
        "objective": "Run one real harness-evolution smoke round on a small fixed set of Python tasks. "
                     "Propose one reusable clarity, checking, or simplification change to the inner prompt. "
                     "If all sampled tasks pass, report that honestly and treat any new hypothesis as unconfirmed. "
                     "Do not embed task-specific algorithms or answers.",
        "strategy": "branching", "editable_files": ["prompt.md"],
        "required_text": {"prompt.md": ["{{ instruction }}", "Do not look up benchmark tests or reference solutions."]},
        "proposer": {"kind": "codex", "model": model, "reasoning_effort": "low", "cli_version": cli_version},
        "evaluator": {
            "kind": "command", "require_activation_file": "prompt.md",
            "portable": {"kind": "humaneval", "dataset": str(dataset), "dataset_sha256": dataset_sha256},
            "argv": [sys.executable, str(Path(__file__).parent / "humaneval.py"),
                     "--harness", "{harness}", "--work", "{work}", "--result", "{result}",
                     "--dataset", str(dataset), "--dataset-sha256", dataset_sha256],
        },
        "budget": {"max_proposals": 1, "max_evaluations": 2, "max_seconds": 600,
                   "proposal_timeout": 120, "evaluation_timeout": 220},
    }
