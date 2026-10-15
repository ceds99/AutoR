import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from autor import adapters
from autor.permissions import config_args, verify_boundary
from autor.presets import demo_config
from autor.storage import AutorError


CODEX = shutil.which("codex") or "/Applications/ChatGPT.app/Contents/Resources/codex"


class ProposerPermissionTests(unittest.TestCase):
    def test_profile_has_exact_read_root_and_no_legacy_sandbox(self):
        args = config_args("/tmp/staged-input")
        joined = " ".join(args)
        self.assertIn('default_permissions="autor-proposal"', joined)
        self.assertIn('"' + str(Path('/tmp/staged-input').resolve()) + '"="read"', joined)
        self.assertIn('network.enabled=false', joined)
        self.assertIn('web_search="disabled"', joined)
        self.assertIn('project_doc_max_bytes=0', joined)
        self.assertNotIn('--sandbox', args)
        self.assertNotIn(':root', joined)
        self.assertNotIn(':workspace_roots', joined)

    def test_unavailable_boundary_never_starts_model_process(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            parent = root / "parent"
            parent.mkdir()
            (parent / "prompt.md").write_text("{{ instruction }}")
            config = demo_config()
            config["proposer"] = {"kind": "codex", "model": "fixture"}
            with patch("autor.permissions.verify_boundary", side_effect=AutorError("unavailable")), patch.object(
                    adapters, "run_process") as process:
                with self.assertRaisesRegex(AutorError, "unavailable"):
                    adapters.propose(config, parent, {}, root / "proposal", 10)
                process.assert_not_called()

    def test_any_failed_probe_rejects_and_removes_canaries(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            work = root / "proposal"
            work.mkdir()
            checks = dict(input_readable=True, outside_denied=False, symlink_escape_denied=True,
                          write_denied=True, command_network_denied=True)
            completed = subprocess.CompletedProcess([], 0, json.dumps(checks), "")
            with patch("autor.permissions.subprocess.run", return_value=completed):
                with self.assertRaisesRegex(AutorError, "no model request"):
                    verify_boundary("fixture-codex", work)
            self.assertEqual(list(root.iterdir()), [work])
            self.assertEqual(list(work.iterdir()), [])

    @unittest.skipUnless(sys.platform == "darwin" and Path(CODEX).is_file(), "requires local macOS Codex CLI")
    def test_actual_sandbox_denies_outside_symlink_writes_and_network(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp) / "proposal"
            work.mkdir()
            receipt = verify_boundary(CODEX, work)
            self.assertTrue(all(receipt["checks"].values()))
            self.assertEqual(receipt["model_calls"], 0)
            self.assertEqual(json.loads((work / "isolation.json").read_text()), receipt)
            self.assertEqual([p.name for p in work.iterdir()], ["isolation.json"])
