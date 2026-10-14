"""Optional real Harbor contract checks: no Docker, network or model calls."""

import importlib.util
from pathlib import Path
import tempfile
import unittest

from autor.adapters import harbor_config
from autor.presets import SEED_PROMPT, harbor_preset


@unittest.skipUnless(importlib.util.find_spec("harbor"), "install harbor==0.24.0 for contract tests")
class HarborContractTests(unittest.TestCase):
    def test_generated_job_validates_with_real_schema(self):
        from harbor.models.job.config import JobConfig
        config = harbor_preset("outer-test", "openai/inner-test", "0.1.0", ["extract-elf"])
        data = harbor_config(config["evaluator"], Path("/tmp/inner"), Path("/tmp/job"))
        job = JobConfig.model_validate(data)
        self.assertEqual(job.agents[0].kwargs["prompt_template_path"], "/tmp/inner/prompt.md")
        self.assertEqual(job.agents[0].kwargs["version"], "0.1.0")
        self.assertEqual(job.n_concurrent_trials, 1)
        self.assertEqual(job.retry.max_retries, 0)

    def test_seed_template_renders_real_instruction(self):
        from harbor.utils.templating import render_prompt_template
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "prompt.md"
            p.write_text(SEED_PROMPT)
            result = render_prompt_template(p, "An exact task instruction")
            self.assertIn("An exact task instruction", result)
            self.assertNotIn("{{", result)

    def test_real_codex_adapter_accepts_prompt_and_version_options(self):
        from harbor.agents.installed.codex import Codex
        with tempfile.TemporaryDirectory() as temp:
            agent = Codex(logs_dir=Path(temp), model_name="openai/test-model",
                          prompt_template_path="/tmp/prompt.md", version="0.1.0")
            self.assertEqual(agent._prompt_template_path, Path("/tmp/prompt.md"))
            self.assertEqual(agent._version, "0.1.0")


if __name__ == "__main__":
    unittest.main()
