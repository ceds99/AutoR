from pathlib import Path
from types import SimpleNamespace
import importlib.util
import shlex
import tempfile
import unittest
from unittest.mock import patch
from autor.storage import AutorError, write_json
from autor import adapters
from scripts import frozen_harbor_evaluator as bridge
from scripts.terminal_bench_pilot import prepare


class FrozenHarborTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.pilot=self.root/'pilot'
        prepare(self.pilot,'fixture','0.153.4','a'*64,task_names=['task-one'])
        self.instructions=self.root/'instructions.json'
        write_json(self.instructions,{'task-one':'Do the task.'})
        self.args=SimpleNamespace(pilot=str(self.pilot),pilot_sha256=bridge.sha(self.pilot/'pilot.json'),
            instructions=str(self.instructions),instructions_sha256=bridge.sha(self.instructions),
            support_sha256=bridge.support_hash(),harness=str(self.pilot/'harness'),
            work=str(self.root/'work'),result=str(self.root/'result.json'),timeout=60)
        self.args.images=str(self.root/'images.json');self.args.images_sha256='unused'

    def test_changed_contract_rejected_before_any_call(self):
        self.instructions.write_text('{}')
        with patch.object(bridge,'run_process',side_effect=AssertionError('must not call')):
            with self.assertRaisesRegex(AutorError,'snapshots changed'):bridge.run(self.args)
        self.assertFalse((self.root/'work/started.json').exists())

    def test_unsupported_code_change_rejected_before_call(self):
        self.args.support_sha256='0'*64
        with patch.object(bridge,'run_process',side_effect=AssertionError('must not call')):
            with self.assertRaisesRegex(AutorError,'code or pilot'):bridge.run(self.args)

    def test_started_attempt_blocks_replay(self):
        work=self.root/'work';work.mkdir();(work/'started.json').write_text('{}')
        with patch.object(bridge,'verify_images'), patch.object(bridge,'run_process',side_effect=AssertionError('must not call')):
            with self.assertRaises(FileExistsError):bridge.run(self.args)

    def test_image_tag_drift_blocks_evaluation(self):
        images=self.root/'images.json'
        write_json(images,[{'task':'task-one','image':'fixture:tag','id':'sha256:old','architecture':'amd64'}])
        with patch.object(bridge.subprocess,'check_output',return_value='[{"Id":"sha256:new","Architecture":"amd64"}]'):
            with self.assertRaisesRegex(AutorError,'image changed'):
                bridge.verify_images(images,bridge.sha(images),['task-one'])

    def test_command_bridge_receives_actual_remaining_timeout(self):
        config={'evaluator':{'kind':'command','argv':['fake','{timeout}','{result}']}}
        seen=[]
        def run(argv,cwd,logs,timeout):
            seen.append((argv[1],timeout))
            write_json(argv[2],{'status':'ok','tasks':[{'task_id':'fixture','score':0}]})
        with patch.object(adapters,'run_process',run):
            adapters.evaluate(config,self.pilot/'harness',self.root/'command',42.5)
        self.assertEqual(seen,[('42.5',42.5)])

    def test_cleanup_is_scoped_to_generated_trial_project(self):
        trial=self.root/'work/jobs/evaluation/task__AbCd';trial.mkdir(parents=True)
        with patch.object(bridge.subprocess,'check_output',return_value='owned-id\n') as query, patch.object(bridge.subprocess,'run') as remove:
            self.assertEqual(bridge.cleanup_owned(self.root/'work'),['owned-id'])
        self.assertEqual(query.call_args.args[0][-1],'label=com.docker.compose.project=task__abcd__env')
        self.assertEqual(remove.call_args.args[0],['docker','rm','-f','owned-id'])

    @unittest.skipUnless(importlib.util.find_spec('harbor'),'Harbor optional dependency')
    def test_actual_rendered_command_required_not_just_prompt_path(self):
        from harbor.agents.installed.base import render_prompt_template
        trial=self.root/'trial';trial.mkdir()
        harness=self.pilot/'harness';prompt=harness/'prompt.md'
        write_json(trial/'result.json',{'config':{'agent':{'kwargs':{'prompt_template_path':str(prompt)}}}})
        rendered=render_prompt_template(prompt,'Do the task.')
        (trial/'trial.log').write_text('codex exec -- '+shlex.quote(rendered)+' 2>&1')
        self.assertTrue(bridge.verify_activation(trial,harness,'Do the task.')['used'])
        (trial/'trial.log').write_text('codex exec -- wrong 2>&1')
        with self.assertRaisesRegex(AutorError,'Rendered candidate'):bridge.verify_activation(trial,harness,'Do the task.')
