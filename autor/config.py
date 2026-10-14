"""Validate and freeze the experiment contract before any calls are made."""

import math
from pathlib import Path

from .storage import AutorError, safe_relative


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate(config):
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise AutorError("config.schema_version must be 1")
    if not isinstance(config.get("objective"), str) or not config["objective"].strip():
        raise AutorError("An objective is required")
    if config.get("strategy") not in ("hill_climb", "branching"):
        raise AutorError("strategy must be hill_climb or branching")
    allowed = config.get("editable_files")
    if not isinstance(allowed, list) or not allowed or len(set(allowed)) != len(allowed):
        raise AutorError("editable_files must be a nonempty list of unique exact paths")
    for value in allowed:
        safe_relative(value)
    for path, texts in config.get("required_text", {}).items():
        safe_relative(path)
        if not isinstance(texts, list) or not all(isinstance(t, str) and t for t in texts):
            raise AutorError("required_text values must be lists of nonempty strings")
    budget = config.get("budget", {})
    for key in ("max_proposals", "max_evaluations"):
        if type(budget.get(key)) is not int or budget[key] < (1 if key == "max_evaluations" else 0):
            raise AutorError("Invalid budget." + key)
    for key in ("max_seconds", "proposal_timeout", "evaluation_timeout"):
        if not finite_number(budget.get(key)) or budget[key] <= 0:
            raise AutorError("Invalid budget." + key)
    proposer = config.get("proposer", {})
    if proposer.get("kind") not in ("demo", "codex"):
        raise AutorError("proposer.kind must be demo or codex")
    if proposer["kind"] == "codex" and not proposer.get("model"):
        raise AutorError("Set an explicit outer model so the experiment stays fixed")
    evaluator = config.get("evaluator", {})
    if evaluator.get("require_activation_file"):
        safe_relative(evaluator["require_activation_file"])
    if evaluator.get("kind") not in ("demo", "command", "harbor"):
        raise AutorError("evaluator.kind must be demo, command, or harbor")
    if proposer["kind"] == "demo" and evaluator["kind"] != "demo":
        raise AutorError("The scripted demo proposer may only use the demo evaluator")
    if evaluator["kind"] == "command":
        argv = evaluator.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(v, str) for v in argv):
            raise AutorError("command evaluator needs an argv list; shell strings are not supported")
    if evaluator["kind"] == "harbor":
        for key in ("model", "agent_version", "dataset", "dataset_version", "metric"):
            if not isinstance(evaluator.get(key), str) or not evaluator[key].strip():
                raise AutorError("Harbor requires evaluator." + key)
        tasks = evaluator.get("task_names")
        if not isinstance(tasks, list) or not tasks or len(set(tasks)) != len(tasks):
            raise AutorError("Harbor requires explicit unique task_names")
        if any(not isinstance(t, str) or not t or any(c in t for c in '*?[]') for t in tasks):
            raise AutorError("Use exact Harbor task names, not wildcards")
        if evaluator.get("agent", "codex") != "codex":
            raise AutorError("The first Harbor preset supports the built-in codex agent")
        if type(evaluator.get("attempts", 1)) is not int or evaluator.get("attempts", 1) < 1:
            raise AutorError("Harbor attempts must be a positive integer")
        safe_relative(evaluator.get("prompt_file", "prompt.md"))
        if not finite_number(evaluator.get("agent_timeout", 120)) or evaluator.get("agent_timeout", 120) <= 0:
            raise AutorError("Invalid Harbor agent_timeout")
    return config


def validate_harness(config, harness):
    for file, texts in config.get("required_text", {}).items():
        path = Path(harness) / file
        if not path.is_file():
            raise AutorError("Required file is missing: " + file)
        content = path.read_text(encoding="utf-8")
        if not all(t in content for t in texts):
            raise AutorError("Required text was removed from " + file)
    if config["evaluator"]["kind"] == "harbor":
        content = (Path(harness) / config["evaluator"].get("prompt_file", "prompt.md")).read_text(encoding="utf-8")
        if "{{ instruction }}" not in content and "{{instruction}}" not in content:
            raise AutorError("Harbor prompt must preserve {{ instruction }}")
