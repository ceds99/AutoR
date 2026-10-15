from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autor import adapters, engine
from autor.cli import initialize_preset, parser
from autor.config import validate
from autor.presets import demo_config
from autor.search import beam_view, select_parent
from autor.storage import AutorError, write_json
from scripts.search_smoke import fixture_propose, fixture_evaluate


class BeamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "run"
        for method, fixture in (("propose", fixture_propose), ("evaluate", fixture_evaluate)):
            mock = patch.object(adapters, method, fixture)
            mock.start()
            self.addCleanup(mock.stop)

    def init(self, proposals=6, **settings):
        config = demo_config("beam", proposals)
        config["search"] = settings
        return initialize_preset(self.root, config)

    def run_outer(self, steps=None):
        return engine.Experiment(self.root).run(steps)

    def test_two_generations_keep_low_branch_and_prune(self):
        self.init()
        state = self.run_outer()
        self.assertEqual([c["parents"] for c in state["candidates"]],
                         [[], ["c0000"], ["c0000"], ["c0001"], ["c0002"], ["c0001"], ["c0002"]])
        self.assertEqual(state["best"], "c0006")
        self.assertEqual((state["proposals_used"], state["evaluations_used"]), (6, 7))
        rounds = state["search"]["rounds"]
        self.assertEqual(rounds[0]["selected"], ["c0001", "c0002"])
        self.assertEqual(rounds[1]["selected"], ["c0006", "c0004"])
        self.assertEqual(rounds[1]["pruned"], ["c0005", "c0003"])
        self.assertTrue(all(r["complete"] for r in rounds))
        self.assertIn("c0006, c0004", (self.root / "findings.md").read_text())
        engine.export(self.root, Path(self.temp.name) / "export", "c0003")

    def test_partial_round_resume_does_not_select_early(self):
        self.init()
        first = self.run_outer(1)
        self.assertEqual(first["search"]["rounds"][0]["selected"], [])
        self.assertEqual(first["search"]["next_parent"], "c0000")
        self.assertEqual(first["search"]["frontier"], ["c0000"])
        mid = self.run_outer(3)
        self.assertFalse(mid["search"]["rounds"][1]["complete"])
        self.assertEqual(mid["search"]["next_parent"], "c0001")
        last = self.run_outer()
        with patch.object(adapters, "propose", side_effect=AssertionError("replay")), patch.object(
                adapters, "evaluate", side_effect=AssertionError("replay")):
            replay = self.run_outer()
        self.assertEqual(last["search"], replay["search"])
        self.assertEqual(replay["evaluations_used"], 7)

    def test_pruned_candidates_never_parent_later_round(self):
        self.init(8)
        state = self.run_outer()
        self.assertEqual(state["candidates"][7]["parents"], ["c0006"])
        self.assertEqual(state["candidates"][8]["parents"], ["c0004"])
        self.assertEqual(state["best"], "c0006")

    def test_failed_and_rejected_slots_exhaust_frontier_without_retry(self):
        self.init()
        self.run_outer(0)
        with patch.object(adapters, "propose", side_effect=AutorError("provider failed")):
            self.run_outer(1)
        with patch.object(adapters, "propose", return_value={"hypothesis": "invalid", "edits": []}):
            state = self.run_outer()
        self.assertEqual(state["stop_reason"], "search_exhausted")
        self.assertEqual((state["proposals_used"], state["evaluations_used"]), (2, 1))
        self.assertEqual(state["search"]["rounds"][0]["failed"], ["c0001", "c0002"])
        self.assertEqual(state["best"], "c0000")
        self.assertTrue(self.run_outer()["search"]["exhausted"])

    def test_evaluation_error_is_not_a_scored_child(self):
        self.init()
        self.run_outer(0)
        with patch.object(adapters, "evaluate", side_effect=AutorError("container offline")):
            self.run_outer(1)
        state = self.run_outer(1)
        self.assertEqual(state["search"]["frontier"], ["c0002"])
        self.assertNotIn("score", state["candidates"][1])
        self.assertEqual(state["best"], "c0000")  # 0.4 child still reproduces.
        state = self.run_outer(1)
        self.assertEqual(state["candidates"][3]["parents"], ["c0002"])
        self.assertEqual(state["best"], "c0003")

    def test_crash_consumes_slot_and_recovers_without_replay(self):
        self.init()
        self.run_outer(0)
        with patch.object(adapters, "propose", side_effect=KeyboardInterrupt):
            interrupted = self.run_outer()
        self.assertEqual(interrupted["proposals_used"], 1)
        self.assertEqual(interrupted["candidates"][1]["status"], "interrupted")
        state = self.run_outer(1)
        self.assertEqual(state["candidates"][2]["parents"], ["c0000"])
        self.assertEqual(state["search"]["frontier"], ["c0002"])

    def test_caps_leave_incomplete_round_without_premature_pruning(self):
        config = demo_config("beam", 6)
        config["budget"]["max_evaluations"] = 2
        initialize_preset(self.root, config)
        state = self.run_outer()
        self.assertEqual(state["stop_reason"], "evaluation_budget")
        self.assertEqual(state["proposals_used"], 1)
        self.assertEqual(state["search"]["rounds"][0]["selected"], [])
        state["seconds_used"] = config["budget"]["max_seconds"]
        write_json(self.root / "run.json", state)
        with patch.object(adapters, "propose", side_effect=AssertionError("time exceeded")):
            self.assertEqual(self.run_outer()["stop_reason"], "time_budget")

    def test_ties_are_stable_and_pending_child_blocks_generation(self):
        self.init(2, beam_width=1)
        state = self.run_outer()
        for c in state["candidates"][1:]:
            c["score"] = 0.5
        self.assertEqual(beam_view(state)["frontier"], ["c0001"])
        self.assertEqual(select_parent(state)["id"], "c0001")
        state["candidates"][2]["status"] = "evaluating"
        self.assertIsNone(beam_view(state)["next_parent"])
        with self.assertRaisesRegex(AutorError, "unfinished"):
            select_parent(state)

    def test_parameter_validation_and_cli(self):
        for settings in ({"beam_width": 0}, {"beam_width": True}, {"children_per_parent": 9},
                         {"children_per_parent": 1.5}, {"unknown": 1}, None):
            config = demo_config("beam")
            config["search"] = settings
            with self.subTest(settings=settings), self.assertRaises(AutorError):
                validate(config)
        config = demo_config("branching")
        config["search"] = {"beam_width": 2}
        with self.assertRaises(AutorError):
            validate(config)
        args = parser().parse_args(["demo", "unused", "--strategy", "beam", "--beam-width", "2"])
        self.assertEqual(args.beam_width, 2)
        self.assertIsNone(args.proposals)

    def test_same_budget_hill_climb_stays_on_first_branch(self):
        initialize_preset(self.root, demo_config("hill_climb", 6))
        state = self.run_outer()
        best = next(c for c in state["candidates"] if c["id"] == state["best"])
        self.assertEqual(best["score"], 0.74)
        self.assertEqual(state["evaluations_used"], 7)
