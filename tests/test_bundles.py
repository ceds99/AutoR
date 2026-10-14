import hashlib
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from autor import adapters, engine
from autor.bundles import run_export, verify_export
from autor.cli import initialize_preset
from autor.presets import demo_config, humaneval_preset
from autor.storage import AutorError, read_json, write_json


class BundleTests(unittest.TestCase):
    def test_relocated_demo_survives_source_removal_and_refuses_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            run = base / "original/run"
            initialize_preset(run, demo_config(proposals=1))
            engine.Experiment(run).run()
            engine.export(run, base / "original/export")
            shutil.copytree(base / "original/export", base / "moved")
            shutil.rmtree(base / "original")
            self.assertTrue(verify_export(base / "moved")["portable"])
            result = run_export(base / "moved", base / "results")
            self.assertEqual(result["score"], 0.5)
            with self.assertRaisesRegex(AutorError, "new work directory"):
                run_export(base / "moved", base / "results")
            (base / "moved/harness/prompt.md").write_text("changed")
            with self.assertRaisesRegex(AutorError, "integrity"):
                verify_export(base / "moved")

    def test_humaneval_bundles_dataset_and_resolves_new_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            seed = base / "seed"
            seed.mkdir()
            (seed / "prompt.md").write_text("Do not look up benchmark tests or reference solutions.\n{{ instruction }}")
            write_json(seed / "runtime.json", {"model": "fixture", "cli_version": "fixture"})
            data = base / "source-data.json"
            write_json(data, {"revision": "fixture", "tasks": []})
            sha = hashlib.sha256(data.read_bytes()).hexdigest()
            config = humaneval_preset("fixture", "fixture", data, sha)
            run = base / "run"
            engine.initialize(run, seed, config)
            prompt_sha = hashlib.sha256((seed / "prompt.md").read_bytes()).hexdigest()
            result = adapters.normalize_result({"status": "ok", "tasks": [{"task_id": "fixture", "score": 0,
                "task_checksum": "fixed", "activation": {"file": "prompt.md", "sha256": prompt_sha, "used": True}}]})
            with patch.object(adapters, "doctor", return_value=[]), patch.object(adapters, "evaluate", return_value=result):
                engine.Experiment(run).run(steps=0)
            engine.export(run, base / "bundle")
            data.unlink()
            shutil.rmtree(run)
            with patch.object(adapters, "evaluate", return_value=result) as evaluator:
                run_export(base / "bundle", base / "work")
                argv = evaluator.call_args.args[0]["evaluator"]["argv"]
                self.assertEqual(argv[0], sys.executable)
                self.assertIn(str((base / "bundle/benchmark.json").resolve()), argv)
                self.assertNotIn(str(data), argv)
            self.assertTrue((base / "bundle/HUMANEVAL_LICENSE").is_file())

    def test_wrong_build_refuses_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            initialize_preset(base / "run", demo_config(proposals=0))
            engine.Experiment(base / "run").run()
            engine.export(base / "run", base / "bundle")
            with patch("autor.bundles.outer_fingerprint", return_value="different"):
                self.assertFalse(verify_export(base / "bundle")["outer_matches"])
                with self.assertRaisesRegex(AutorError, "matching outer"):
                    run_export(base / "bundle", base / "work")
            self.assertFalse((base / "work").exists())

    def test_generic_command_is_not_claimed_portable(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            config = demo_config(proposals=0)
            config["proposer"] = {"kind": "codex", "model": "fixture"}
            config["evaluator"] = {"kind": "command", "argv": [sys.executable, "external.py"]}
            initialize_preset(base / "run", config)
            with patch.object(adapters, "doctor", return_value=[]), patch.object(adapters, "evaluate", return_value={
                "status": "ok", "tasks": [{"task_id": "fixture", "score": 0}]}):
                engine.Experiment(base / "run").run()
            engine.export(base / "run", base / "bundle")
            self.assertFalse(verify_export(base / "bundle")["portable"])
            with self.assertRaisesRegex(AutorError, "separately configured"):
                run_export(base / "bundle", base / "work")
