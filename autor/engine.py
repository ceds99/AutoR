"""Persistent sequential experiment loop. Search policy stays outside the LLM."""

import difflib
import hashlib
from pathlib import Path
import time

from . import __version__
from . import adapters
from .config import validate, validate_harness
from .storage import (AutorError, copy_harness, digest, locked, manifest,
                      outer_fingerprint, read_json, safe_relative, write_json)


def initialize(root, seed, config):
    root, seed = Path(root).resolve(), Path(seed).resolve()
    validate(config)
    if root.exists():
        raise AutorError("Experiment path already exists; choose a new path or resume it")
    if root == seed or seed in root.parents:
        raise AutorError("Experiment directory cannot be inside its seed")
    files = manifest(seed)
    if not files:
        raise AutorError("Seed harness is empty")
    validate_harness(config, seed)
    for name in config["editable_files"]:
        if name not in files:
            raise AutorError("Editable file must exist in the seed: " + name)
    root.mkdir(parents=True)
    path = root / "candidates/c0000/harness"
    path.parent.mkdir(parents=True)
    copy_harness(seed, path)
    state = {
        "schema_version": 1, "outer_version": __version__, "outer_hash": outer_fingerprint(),
        "config": config, "config_hash": digest(config), "created_at": time.time(),
        "best": None, "proposals_used": 0, "evaluations_used": 0, "seconds_used": 0.0,
        "active": None, "stop_reason": None, "task_keys": None, "task_checksums": None,
        "candidates": [{"id": "c0000", "parents": [], "depth": 0, "status": "ready",
                        "hash": digest(files), "hypothesis": "Imported seed; MA is outside this run"}],
        "events": [],
    }
    write_json(root / "run.json", state)
    return state


def load(root, verify_outer=True):
    root = Path(root)
    state = read_json(root / "run.json")
    validate(state["config"])
    if digest(state["config"]) != state["config_hash"]:
        raise AutorError("Frozen config changed; start a new experiment")
    if verify_outer and outer_fingerprint() != state["outer_hash"]:
        raise AutorError("Outer code or skill changed; start a new experiment to preserve provenance")
    for item in state["candidates"]:
        if item.get("hash") and item["status"] in ("ready", "evaluating", "evaluated"):
            if digest(manifest(root / "candidates" / item["id"] / "harness")) != item["hash"]:
                raise AutorError("Harness snapshot was modified: " + item["id"])
    return state


def select_parent(state):
    items = [c for c in state["candidates"] if c["status"] == "evaluated"]
    if not items:
        raise AutorError("No measured parent is available")
    if state["config"]["strategy"] == "hill_climb":
        return next(c for c in items if c["id"] == state["best"])
    # Bounded breadth-first expansion: two children per measured candidate.
    # Lower-scoring parents remain eligible. Merging is deliberately deferred.
    counts = {c["id"]: sum(c["id"] in x["parents"] for x in state["candidates"]) for c in items}
    available = [c for c in items if counts[c["id"]] < 2] or items
    return min(available, key=lambda c: (c["depth"], counts[c["id"]], -c["score"], c["id"]))


def apply_proposal(config, parent, child, proposal):
    if not isinstance(proposal, dict) or not isinstance(proposal.get("hypothesis"), str):
        raise AutorError("Proposal requires a hypothesis")
    edits = proposal.get("edits")
    if not isinstance(edits, list) or not edits or len(edits) > 8:
        raise AutorError("Proposal needs 1–8 file replacements")
    seen = set()
    for edit in edits:
        if not isinstance(edit, dict):
            raise AutorError("Malformed edit")
        name = edit.get("path")
        safe_relative(name)
        if name not in config["editable_files"] or name in seen:
            raise AutorError("Edit is outside the allowlist or duplicated: " + name)
        content = edit.get("content")
        if not isinstance(content, str) or len(content.encode()) > 65536:
            raise AutorError("Each replacement must be UTF-8 text of at most 64 KiB")
        seen.add(name)
    copy_harness(parent, child)
    patch = []
    for edit in edits:
        p = Path(child) / edit["path"]
        old = p.read_text(encoding="utf-8")
        new = edit["content"]
        p.write_text(new, encoding="utf-8")
        patch.extend(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                          fromfile="a/" + edit["path"], tofile="b/" + edit["path"]))
    validate_harness(config, child)
    if manifest(parent) == manifest(child):
        raise AutorError("Proposal makes no change")
    (Path(child).parent / "change.patch").write_text("".join(patch), encoding="utf-8")
    return digest(manifest(child))


class Experiment:
    def __init__(self, root, notify=None):
        self.root = Path(root).resolve()
        self.state = None
        self.notify = notify or (lambda event: None)

    def save(self):
        write_json(self.root / "run.json", self.state)

    def event(self, kind, **data):
        event = {"event": kind, "time": time.time(), **data}
        self.state["events"].append(event)
        self.save()
        self.notify(event)

    def candidate_dir(self, candidate):
        return self.root / "candidates" / candidate["id"]

    def remaining(self):
        return self.state["config"]["budget"]["max_seconds"] - self.state["seconds_used"]

    def call(self, kind, candidate, fn):
        config = self.state["config"]
        timeout = min(self.remaining(), config["budget"][kind + "_timeout"])
        if timeout <= 0:
            raise AutorError("Time budget exhausted")
        counter = "proposals_used" if kind == "proposal" else "evaluations_used"
        maximum = "max_proposals" if kind == "proposal" else "max_evaluations"
        if self.state[counter] >= config["budget"][maximum]:
            raise AutorError(kind + " budget exhausted")
        self.state[counter] += 1  # Reserve before calling; failures consume budget.
        candidate["status"] = "proposing" if kind == "proposal" else "evaluating"
        self.state["active"] = {"candidate": candidate["id"], "kind": kind,
                                "started_at": time.time(), "timeout": timeout}
        self.event(kind + "_started", candidate=candidate["id"])
        started = time.monotonic()
        try:
            return fn(timeout)
        finally:
            self.state["seconds_used"] += time.monotonic() - started
            self.state["active"] = None
            self.save()

    def finish_evaluation(self, candidate, result):
        result = adapters.normalize_result(result)
        activation_file = self.state["config"]["evaluator"].get("require_activation_file")
        if activation_file:
            expected = hashlib.sha256((self.candidate_dir(candidate) / "harness" / activation_file).read_bytes()).hexdigest()
            for task in result["tasks"]:
                receipt = task.get("activation", {})
                if receipt.get("file") != activation_file or receipt.get("sha256") != expected or receipt.get("used") is not True:
                    raise AutorError("Missing or mismatched candidate activation receipt: " + task["task_id"])
            candidate["activation_verified"] = True
        if self.state["task_keys"] is not None and result["task_keys"] != self.state["task_keys"]:
            raise AutorError("Task coverage differs from baseline; result cannot be compared")
        checksums = sorted((t["task_id"], t.get("task_checksum")) for t in result["tasks"])
        if self.state["task_checksums"] is not None and checksums != [tuple(x) for x in self.state["task_checksums"]]:
            raise AutorError("Task contents differ from baseline; result cannot be compared")
        if self.state["task_keys"] is None:
            self.state["task_keys"] = result["task_keys"]
            self.state["task_checksums"] = checksums
        candidate.update(status="evaluated", score=result["score"], task_scores=result["tasks"])
        if candidate["parents"]:
            parent = next(c for c in self.state["candidates"] if c["id"] == candidate["parents"][0])
            previous = {(t["task_id"], t.get("attempt", 0)): t["score"] for t in parent["task_scores"]}
            comparison = {"parent": parent["id"], "delta": candidate["score"] - parent["score"],
                          "improved": [], "regressed": [], "unchanged": []}
            for task in result["tasks"]:
                change = task["score"] - previous[(task["task_id"], task.get("attempt", 0))]
                category = "improved" if change > 0 else "regressed" if change < 0 else "unchanged"
                comparison[category].append({"task_id": task["task_id"], "attempt": task.get("attempt", 0), "delta": change})
            candidate["comparison"] = comparison
        best = next((c for c in self.state["candidates"] if c["id"] == self.state["best"]), None)
        candidate["promoted"] = best is None or candidate["score"] > best["score"]
        if candidate["promoted"]:
            self.state["best"] = candidate["id"]
        self.event("evaluated", candidate=candidate["id"], score=candidate["score"],
                   promoted=candidate["promoted"])

    def recover(self):
        active = self.state["active"]
        if active:
            self.state["seconds_used"] += min(max(0, time.time() - active["started_at"]), active["timeout"])
            self.state["active"] = None
        for c in self.state["candidates"]:
            if c["status"] not in ("proposing", "evaluating"):
                continue
            result_path = self.candidate_dir(c) / "evaluation.json"
            if c["status"] == "evaluating" and result_path.is_file():
                try:
                    self.finish_evaluation(c, read_json(result_path))
                    continue
                except (AutorError, ValueError, OSError) as e:
                    c["error"] = str(e)
            c["status"] = "interrupted"
            c.setdefault("error", "Interrupted call; its reserved budget remains consumed. No automatic replay.")
            self.event("recovered_interruption", candidate=c["id"])
        self.save()

    def evaluate(self, candidate):
        directory = self.candidate_dir(candidate)
        try:
            result = self.call("evaluation", candidate, lambda timeout: adapters.evaluate(
                self.state["config"], directory / "harness", directory / "evaluation", timeout))
            if digest(manifest(directory / "harness")) != candidate["hash"]:
                raise AutorError("Evaluator modified the harness snapshot")
            write_json(directory / "evaluation.json", result)
            self.finish_evaluation(candidate, result)
        except Exception as e:
            candidate.update(status="error", error=str(e))
            self.event("evaluation_failed", candidate=candidate["id"], error=str(e))

    def propose(self):
        parent = select_parent(self.state)
        candidate = {"id": "c{:04d}".format(len(self.state["candidates"])),
                     "parents": [parent["id"]], "depth": parent["depth"] + 1, "status": "proposing"}
        self.state["candidates"].append(candidate)
        directory = self.candidate_dir(candidate)
        directory.mkdir(parents=True)
        evidence = {
            "proposal_number": self.state["proposals_used"] + 1,
            "parent": parent,
            "history": [{k: c[k] for k in ("id", "parents", "status", "score", "diagnosis", "hypothesis", "comparison", "error") if k in c}
                        for c in self.state["candidates"][:-1]],
        }
        phase = "proposal"
        try:
            proposal = self.call("proposal", candidate, lambda timeout: adapters.propose(
                self.state["config"], self.candidate_dir(parent) / "harness", evidence,
                directory / "proposal", timeout))
            phase = "validation"
            write_json(directory / "proposal.json", proposal)
            candidate["hypothesis"] = proposal.get("hypothesis", "")
            candidate["diagnosis"] = proposal.get("diagnosis", "Not supplied")
            if proposal.get("usage"):
                candidate["proposal_usage"] = proposal["usage"]
            candidate["hash"] = apply_proposal(self.state["config"], self.candidate_dir(parent) / "harness",
                                                directory / "harness", proposal)
            before = manifest(self.candidate_dir(parent) / "harness")
            after = manifest(directory / "harness")
            candidate["changed_files"] = [name for name in sorted(set(before) | set(after)) if before.get(name) != after.get(name)]
            if any(c.get("hash") == candidate["hash"] for c in self.state["candidates"][:-1]):
                raise AutorError("Duplicate harness already in archive; skipping evaluation")
            candidate["status"] = "ready"
            self.event("candidate_ready", candidate=candidate["id"], parent=parent["id"])
        except Exception as e:
            candidate.update(status="error" if phase == "proposal" else "rejected",
                             failure_phase=phase, error=str(e))
            self.event("proposal_failed" if phase == "proposal" else "proposal_rejected",
                       candidate=candidate["id"], error=str(e))

    def run(self, steps=None):
        with locked(self.root):
            self.state = load(self.root)
            self.recover()
            failures = [c for c in adapters.doctor(self.state["config"]) if not c["ok"]]
            if failures:
                raise AutorError("Missing prerequisites: " + "; ".join(c["detail"] for c in failures))
            self.state["stop_reason"] = None
            used_at_start = self.state["proposals_used"]
            budget = self.state["config"]["budget"]
            try:
                while True:
                    if self.remaining() <= 0:
                        reason = "time_budget"
                        break
                    ready = next((c for c in self.state["candidates"] if c["status"] == "ready"), None)
                    if ready:
                        if self.state["evaluations_used"] >= budget["max_evaluations"]:
                            reason = "evaluation_budget"
                            break
                        self.evaluate(ready)
                        continue
                    if not self.state["best"]:
                        reason = "no_measured_seed"
                        break
                    if steps is not None and self.state["proposals_used"] - used_at_start >= steps:
                        reason = "step_limit"
                        break
                    if self.state["proposals_used"] >= budget["max_proposals"]:
                        reason = "proposal_budget"
                        break
                    if self.state["evaluations_used"] >= budget["max_evaluations"]:
                        reason = "evaluation_budget"
                        break
                    self.propose()
            except KeyboardInterrupt:
                self.recover()
                reason = "interrupted"
            self.state["stop_reason"] = reason
            self.event("stopped", reason=reason, best=self.state["best"])
            from .report import write_report
            write_report(self.root, self.state)
            return self.state


def export(root, destination, candidate_id=None):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    with locked(root):
        state = load(root)
        chosen = candidate_id or state["best"]
        candidate = next((c for c in state["candidates"] if c["id"] == chosen), None)
        if not candidate or candidate["status"] != "evaluated":
            raise AutorError("Only evaluated candidates can be exported")
        if destination.exists() or root == destination or root in destination.parents:
            raise AutorError("Export to a new directory outside the experiment")
        destination.mkdir(parents=True)
        copy_harness(root / "candidates" / chosen / "harness", destination / "harness")
        write_json(destination / "provenance.json", {
            "candidate": candidate, "config": state["config"], "outer_hash": state["outer_hash"],
            "outer_version": state["outer_version"], "source_experiment": str(root),
            "warning": "Search-set measurement only; no demonstrated held-out improvement.",
        })
        if state["config"]["evaluator"]["kind"] == "harbor":
            write_json(destination / "harbor-job.json", adapters.harbor_config(
                state["config"]["evaluator"], destination / "harness", destination / "evaluation"))
        from .bundles import seal_export
        seal_export(destination, state)
        return chosen
