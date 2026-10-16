from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autor import adapters, engine
from autor.adaptive import adaptive_view, diverse_select
from autor.cli import initialize_preset
from autor.config import validate
from autor.presets import demo_config
from autor.storage import AutorError, read_json


def candidate(name, values):
    return {'id': name, 'score': sum(values) / len(values),
            'task_scores': [{'task_id': str(i), 'score': v} for i, v in enumerate(values)]}


class AdaptiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'run'
        self.calls = []
        self.evidence = []
        self.values = {0: [0.2, 0.2], 1: [0.8, 0.3], 2: [0.3, 0.8], 3: [0.8, 0.8]}
        def propose(config, parent, evidence, work, timeout):
            self.evidence.append(evidence)
            return {'diagnosis': 'Synthetic fixture.', 'hypothesis': 'Exercise a branch.',
                    'edits': [{'path': 'prompt.md', 'content': '{{ instruction }}\nID='+str(evidence['proposal_number'])}]}
        def evaluate(config, harness, work, timeout):
            text = (harness / 'prompt.md').read_text()
            number = int(text.split('ID=')[1]) if 'ID=' in text else 0
            self.calls.append(number)
            values = self.values.get(number, [0.1, 0.1])
            if callable(values): values = values(self.calls.count(number))
            return {'status': 'ok', 'synthetic': True, 'tasks': candidate('', values)['task_scores']}
        for name, function in [('propose', propose), ('evaluate', evaluate)]:
            mock = patch.object(adapters, name, function);mock.start();self.addCleanup(mock.stop)

    def init(self, proposals=3, evaluations=12, mixing=False):
        config = demo_config('adaptive_beam', proposals)
        config['search'] = {'mixing': mixing}
        config['budget']['max_evaluations'] = evaluations
        initialize_preset(self.root, config)

    def test_diversity_keeps_weaker_complement_over_redundant_high_score(self):
        values = [candidate('a', [.9, .1]), candidate('b', [.85, .1]), candidate('c', [.1, .7])]
        self.assertEqual([x['id'] for x in diverse_select(values, 2)], ['a', 'c'])

    def test_directed_children_confirm_before_mix_and_charge_one_slot(self):
        self.init(mixing=True)
        state = engine.Experiment(self.root).run()
        self.assertEqual(self.calls, [0, 0, 1, 1, 2, 2, 3, 3])
        self.assertEqual(state['candidates'][3]['parents'], ['c0001', 'c0002'])
        self.assertEqual(state['best'], 'c0003')
        self.assertEqual(state['evaluations_used'], 8)
        self.assertEqual([e['search_directive']['operator'] for e in self.evidence],
                         ['failure_analysis', 'verification', 'mix'])
        self.assertIn('ID=2', self.evidence[-1]['secondary_parents'][0]['editable_contents']['prompt.md'])
        self.assertNotIn('samples', self.evidence[-1]['parent'])
        self.assertEqual(len(self.evidence[-1]['parent']['measurements']), 2)
        round2 = state['search']['rounds'][1]
        self.assertEqual((len(round2['children']), round2['slots']), (1, 4))
        self.assertTrue(round2['mix_used'])
        self.assertEqual(round2['allocation_reasons']['c0001'], 'replicated_improvement')
        self.assertTrue((self.root/'candidates/c0003/measurements/m0002/result.json').exists())
        self.assertEqual(read_json(self.root/'candidates/c0003/evaluation.json')['measurement_count'], 2)

    def test_no_mixing_arm_and_stable_partial_resume(self):
        self.init()
        first = engine.Experiment(self.root).run(1)
        self.assertFalse(first['search']['rounds'][0]['complete'])
        final = engine.Experiment(self.root).run()
        self.assertEqual(final['candidates'][3]['parents'], ['c0001'])
        calls = list(self.calls)
        replay = engine.Experiment(self.root).run()
        self.assertEqual(self.calls, calls)
        self.assertEqual(final['search'], replay['search'])

    def test_no_promotion_from_one_lucky_sample(self):
        self.init(proposals=1)
        self.values[1] = lambda n: [.9, .9] if n == 1 else [.1, .1]
        state = engine.Experiment(self.root).run()
        child = state['candidates'][1]
        self.assertTrue(child['confirmed'])
        self.assertEqual(child['score'], .5)
        self.assertFalse(child['promoted'])
        self.assertEqual(state['best'], 'c0000')
        self.assertEqual([s['score'] for s in child['samples']], [.9, .1])

    def test_budget_exhaustion_keeps_unconfirmed_child_out_of_best(self):
        self.init(evaluations=3)
        state = engine.Experiment(self.root).run()
        self.assertEqual(state['stop_reason'], 'evaluation_budget')
        self.assertEqual(state['best'], 'c0000')
        self.assertEqual(state['candidates'][1]['status'], 'needs_confirmation')
        self.assertEqual(state['evaluations_used'], 3)
        self.assertFalse(state['search']['rounds'][0]['complete'])

    def test_confirmation_failure_is_retained_not_replayed_or_scored_zero(self):
        self.init(proposals=1)
        self.values[1] = lambda n: [.8, .8] if n == 1 else (_ for _ in ()).throw(AutorError('offline'))
        state = engine.Experiment(self.root).run()
        child = state['candidates'][1]
        self.assertEqual(child['status'], 'error')
        self.assertEqual(len(child['samples']), 1)
        self.assertEqual(state['best'], 'c0000')
        self.assertEqual(state['evaluations_used'], 4)
        engine.Experiment(self.root).run()
        self.assertEqual(self.calls, [0, 0, 1, 1])

    def test_recover_new_sample_file_not_stale_aggregate(self):
        self.init(proposals=0)
        original = engine.Experiment.finish_evaluation
        interrupted = []
        def finish(obj, child, result):
            if child.get('pending_measurement') == 2 and not interrupted:
                interrupted.append(True)
                raise KeyboardInterrupt()
            return original(obj, child, result)
        self.values[0] = lambda n: [.2, .2] if n == 1 else [.4, .4]
        with patch.object(engine.Experiment, 'finish_evaluation', finish):
            state = engine.Experiment(self.root).run()
        self.assertEqual(self.calls, [0, 0])
        self.assertEqual(len(state['candidates'][0]['samples']), 2)
        self.assertAlmostEqual(state['candidates'][0]['score'], .3)
        self.assertEqual(state['best'], 'c0000')
        engine.Experiment(self.root).run()
        self.assertEqual(self.calls, [0, 0])

    def test_weak_parent_gets_one_slot_and_insufficient_parents_skip_mix(self):
        self.init(proposals=2, mixing=True)
        self.values[1] = [.1, .1];self.values[2] = [.1, .1]
        state = engine.Experiment(self.root).run()
        view = adaptive_view(state)
        self.assertEqual(view['next_parents'], ['c0001'])
        self.assertEqual(view['directive']['remaining_parent_slots'], 1)
        self.assertEqual(view['directive']['allocation_reason'], 'limited_non_improving_exploration')
        self.assertEqual(self.calls, [0, 0, 1, 2])

    def test_settings_are_strict(self):
        for settings in [{'confirmation_samples': 1}, {'confirmation_samples': True}, {'mixing': 1}]:
            config = demo_config('adaptive_beam');config['search'] = settings
            with self.assertRaises(AutorError):validate(config)
        config = demo_config('beam');config['search'] = {'mixing': True}
        with self.assertRaises(AutorError):validate(config)

    def test_failed_mix_is_charged_once_and_not_automatically_retried(self):
        self.init(proposals=4, mixing=True)
        engine.Experiment(self.root).run(2)
        with patch.object(adapters, 'propose', side_effect=AutorError('provider unavailable')):
            state = engine.Experiment(self.root).run(1)
        self.assertEqual(state['candidates'][3]['parents'], ['c0001', 'c0002'])
        self.assertEqual(state['candidates'][3]['status'], 'error')
        state = engine.Experiment(self.root).run(1)
        self.assertEqual(state['candidates'][4]['parents'], ['c0002'])
        self.assertEqual(state['proposals_used'], 4)
        self.assertEqual(state['search']['rounds'][1]['failed'], ['c0003'])
