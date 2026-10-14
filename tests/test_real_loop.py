import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from autor import adapters, engine
from autor.cli import initialize_preset
from autor.humaneval import evaluate, execute_python, grade
from autor.presets import demo_config
from autor.storage import AutorError, write_json


@unittest.skipUnless(sys.platform == "darwin", "macOS native runner")
class NativeHumanEvalTests(unittest.TestCase):
    def test_actual_python_tests_distinguish_pass_fail_and_early_exit(self):
        task = {"entry_point": "f", "test": "def check(candidate):\n    assert candidate(2) == 3\n"}
        self.assertEqual(grade("def f(x):\n    return x + 1", task)["score"], 1)
        self.assertEqual(grade("def f(x):\n    return x", task)["score"], 0)
        self.assertEqual(grade("import sys; sys.exit(0)", task)["score"], 0)

    def test_sandbox_denies_user_files_writes_and_network(self):
        with tempfile.TemporaryDirectory() as temp:
            secret = Path(temp) / "secret.txt"
            secret.write_text("fixture only")
            source = "import socket,os\nfrom pathlib import Path\n"
            source += "try:\n    Path(" + repr(str(secret)) + ").read_text()\n    print('READ_ALLOWED')\nexcept PermissionError:\n    print('READ_DENIED')\n"
            source += "try:\n    Path('output').write_text('x')\n    print('WRITE_ALLOWED')\nexcept PermissionError:\n    print('WRITE_DENIED')\n"
            source += "try:\n    socket.socket().connect(('127.0.0.1',9))\n    print('NETWORK_ALLOWED')\nexcept PermissionError:\n    print('NETWORK_DENIED')\n"
            result = execute_python(source)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(), ["READ_DENIED", "WRITE_DENIED", "NETWORK_DENIED"])

    def test_completed_trials_are_reused_and_dataset_is_pinned(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            harness = root / "harness"
            harness.mkdir()
            (harness / "prompt.md").write_text("Do the task.\n{{ instruction }}")
            counter = root / "calls"
            fake = root / "fake-codex"
            fake.write_text("#!" + sys.executable + "\nimport sys,json\nfrom pathlib import Path\n"
                            "if '--version' in sys.argv:\n    print('fixture-cli');sys.exit(0)\n"
                            "counter=Path(" + repr(str(counter)) + ")\n"
                            "counter.write_text(str(int(counter.read_text())+1) if counter.exists() else '1')\n"
                            "sys.stdin.read()\n"
                            "Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text(json.dumps({'solution':'def f(x): return x + 1'}))\n"
                            "print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':1}}))\n")
            fake.chmod(0o755)
            write_json(harness / "runtime.json", {"executable": str(fake), "cli_version": "fixture-cli", "model": "fixture-model"})
            dataset = root / "data.json"
            write_json(dataset, {"revision": "fixture", "tasks": [{"task_id": "fixture", "prompt": "def f(x):", "entry_point": "f",
                                                                 "test": "def check(candidate):\n    assert candidate(2)==3\n"}]})
            sha = hashlib.sha256(dataset.read_bytes()).hexdigest()
            first = evaluate(harness, root / "work", dataset, sha)
            second = evaluate(harness, root / "work", dataset, sha)
            self.assertEqual(first["tasks"], second["tasks"])
            self.assertEqual(counter.read_text(), "1")
            self.assertEqual(first["tasks"][0]["score"], 1)
            dataset.write_text("{}")
            with self.assertRaisesRegex(AutorError, "data changed"):
                evaluate(harness, root / "work", dataset, sha)


class ExperimentOrganizationTests(unittest.TestCase):
    def test_comparison_report_and_changed_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "run"
            initialize_preset(root, demo_config(proposals=1))
            state = engine.Experiment(root).run()
            child = state["candidates"][1]
            self.assertEqual(child["changed_files"], ["prompt.md"])
            self.assertEqual(len(child["comparison"]["improved"]), 1)
            self.assertEqual(child["comparison"]["regressed"], [])
            self.assertIn("c0001", (root / "findings.md").read_text())

    def test_missing_activation_prevents_promotion(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "run"
            config = demo_config(proposals=0)
            config["evaluator"]["require_activation_file"] = "prompt.md"
            initialize_preset(root, config)
            state = engine.Experiment(root).run()
            self.assertIsNone(state["best"])
            self.assertIn("activation receipt", state["candidates"][0]["error"])

    def test_matching_activation_is_recorded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "run"
            config = demo_config(proposals=0)
            config["evaluator"]["require_activation_file"] = "prompt.md"
            initialize_preset(root, config)
            sha = hashlib.sha256((root / "candidates/c0000/harness/prompt.md").read_bytes()).hexdigest()
            result = {"status": "ok", "tasks": [{"task_id": "fixture", "score": 1,
                      "activation": {"file": "prompt.md", "sha256": sha, "used": True}}]}
            with patch.object(adapters, "evaluate", return_value=result):
                state = engine.Experiment(root).run()
            self.assertTrue(state["candidates"][0]["activation_verified"])


if __name__ == "__main__":
    unittest.main()
