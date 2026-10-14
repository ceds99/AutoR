"""Relocatable export envelopes. Historical absolute paths are never executed."""

import hashlib
from pathlib import Path
import shutil
import sys

from . import adapters
from .storage import AutorError, digest, manifest, outer_fingerprint, read_json, write_json


def seal_export(destination, state):
    destination = Path(destination)
    settings = state["config"]["evaluator"]
    portable = settings.get("portable", {})
    if settings["kind"] == "demo":
        evaluator = {"kind": "demo"}
    elif settings["kind"] == "harbor":
        evaluator = dict(settings)
        evaluator.pop("executable", None)  # Resolve Harbor from the receiving environment.
    elif settings["kind"] == "command" and portable.get("kind") == "humaneval":
        source = Path(portable["dataset"])
        if hashlib.sha256(source.read_bytes()).hexdigest() != portable["dataset_sha256"]:
            raise AutorError("Pinned benchmark data changed; export refused")
        shutil.copyfile(source, destination / "benchmark.json")
        shutil.copyfile(Path(__file__).parent / "data/HUMANEVAL_LICENSE", destination / "HUMANEVAL_LICENSE")
        evaluator = {"kind": "humaneval", "dataset_sha256": portable["dataset_sha256"]}
    else:
        evaluator = {"kind": "external-command", "note": "Supply and validate the external evaluator separately; arbitrary commands are not portable."}
    write_json(destination / "evaluation-spec.json", {
        "schema_version": 1, "evaluator": evaluator,
        "timeout": state["config"]["budget"]["evaluation_timeout"],
        "require_activation_file": settings.get("require_activation_file"),
    })
    (destination / "README.txt").write_text(
        "Install the matching AutoR wheel. Verify before evaluating:\n"
        "  autor verify-export /path/to/bundle\n"
        "  autor run-export /path/to/bundle --work /path/to/new-results\n"
        "Real backends require their runtime and credentials and incur model usage.\n"
        "provenance.json contains historical paths for audit only.\n"
        "Harbor's legacy harbor-job.json is location-specific; run-export regenerates paths.\n",
        encoding="utf-8")
    write_json(destination / "bundle.json", {"schema_version": 1, "outer_hash": state["outer_hash"],
        "outer_version": state["outer_version"], "files": manifest(destination)})


def verify_export(bundle):
    bundle = Path(bundle).resolve()
    files = manifest(bundle)
    envelope = read_json(bundle / "bundle.json")
    files.pop("bundle.json", None)
    if envelope.get("schema_version") != 1 or files != envelope.get("files"):
        raise AutorError("Export integrity check failed (missing, added, or changed file)")
    provenance = read_json(bundle / "provenance.json")
    if digest(manifest(bundle / "harness")) != provenance["candidate"]["hash"]:
        raise AutorError("Export harness differs from measured candidate")
    if envelope["outer_hash"] != provenance["outer_hash"]:
        raise AutorError("Export provenance mismatch")
    kind = read_json(bundle / "evaluation-spec.json")["evaluator"]["kind"]
    return {"verified": True, "candidate": provenance["candidate"]["id"], "backend": kind,
            "outer_matches": envelope["outer_hash"] == outer_fingerprint(),
            "portable": kind in ("demo", "humaneval", "harbor"),
            "bundle_sha256": hashlib.sha256((bundle / "bundle.json").read_bytes()).hexdigest()}


def run_export(bundle, work):
    bundle, work = Path(bundle).resolve(), Path(work).resolve()
    verified = verify_export(bundle)
    if not verified["outer_matches"]:
        raise AutorError("Install the matching outer build before evaluating this export")
    if not verified["portable"]:
        raise AutorError("External command export requires a separately configured evaluator")
    if work.exists() or bundle == work or bundle in work.parents or work in bundle.parents:
        raise AutorError("Choose a new work directory outside the bundle; existing calls are not replayed")
    spec = read_json(bundle / "evaluation-spec.json")
    evaluator = spec["evaluator"]
    if evaluator["kind"] == "humaneval":
        evaluator = {"kind": "command", "argv": [
            sys.executable, str(Path(__file__).parent / "humaneval.py"),
            "--harness", "{harness}", "--work", "{work}", "--result", "{result}",
            "--dataset", str(bundle / "benchmark.json"), "--dataset-sha256", evaluator["dataset_sha256"]]}
    work.mkdir(parents=True)
    write_json(work / "started.json", {"bundle_sha256": verified["bundle_sha256"],
        "candidate": verified["candidate"], "timeout": spec["timeout"]})
    result = adapters.evaluate({"evaluator": evaluator}, bundle / "harness", work, spec["timeout"])
    if spec.get("require_activation_file"):
        name = spec["require_activation_file"]
        expected = hashlib.sha256((bundle / "harness" / name).read_bytes()).hexdigest()
        if any(t.get("activation", {}).get("sha256") != expected
               or t.get("activation", {}).get("file") != name
               or t.get("activation", {}).get("used") is not True for t in result["tasks"]):
            raise AutorError("Missing or mismatched activation receipt")
    verify_export(bundle)  # Reject an evaluator that changed the measured bundle.
    provenance = read_json(bundle / "provenance.json")
    previous = {(t["task_id"], t.get("attempt", 0)): t.get("task_checksum")
                for t in provenance["candidate"]["task_scores"]}
    observed = {(t["task_id"], t.get("attempt", 0)): t.get("task_checksum") for t in result["tasks"]}
    if previous != observed:
        raise AutorError("Export evaluation task coverage or content changed")
    result["export_verification"] = verified
    write_json(work / "evaluation.json", result)
    return result
