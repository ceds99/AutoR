"""Evaluate a candidate against a frozen local Harbor pilot contract.

This bridge is local experimental infrastructure, not a portable BOS publisher.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autor import adapters
from autor.config import finite_number
from autor.process import run_process
from autor.storage import AutorError, digest, read_json, write_json
from scripts.terminal_bench_pilot import load


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def support_hash():
    return digest({name: sha(Path(__file__).parent/name)
                   for name in ['frozen_harbor_evaluator.py', 'terminal_bench_pilot.py']})


def verify_harbor_version():
    from importlib.metadata import version
    result=subprocess.run(['harbor','--version'],capture_output=True,text=True,timeout=10)
    if result.returncode or '0.24.0' not in result.stdout.split() or version('harbor')!='0.24.0':
        raise AutorError('Frozen evaluator requires Harbor CLI and library 0.24.0')


def verify_images(path, expected, tasks):
    if sha(path) != expected:
        raise AutorError('Frozen image receipts changed')
    images=read_json(path)
    if {item['task'] for item in images} != set(tasks):
        raise AutorError('Image receipts do not cover exactly the selected tasks')
    for item in images:
        current=json.loads(subprocess.check_output(['docker','image','inspect',item['image']],text=True,timeout=15))[0]
        if current['Id'] != item['id'] or current['Architecture'] != item['architecture']:
            raise AutorError('Frozen task image changed: '+item['task'])


def invalid_verifier_feedback(feedback):
    # Fail closed on known infrastructure errors even when an upstream script writes 0.
    return bool(re.search(r'rosetta error|No space left on device|Cannot connect to the Docker daemon|ERROR collecting|no tests ran',feedback,re.I))


def image_content_digest(row):
    """Compare rebuilt images without the per-trial Compose project label."""
    config=json.loads(json.dumps(row['Config']))
    labels=config.get('Labels') or {}
    labels.pop('com.docker.compose.project',None)
    config['Labels']=labels
    return digest({'rootfs':row['RootFS'],'config':config,
                   'architecture':row['Architecture'],'os':row['Os']})


def verify_trial_images(trial, receipts, task):
    checked=[]
    for item in receipts:
        if item['task']!=task or 'project_suffix' not in item:continue
        suffix=item['project_suffix']
        if not re.fullmatch(r'__(?:env|verifier__[a-z0-9_-]+)',suffix):
            raise AutorError('Unsupported trial image project suffix')
        project=Path(trial).name.lower()+suffix
        ids=subprocess.check_output(['docker','image','ls','-q','--filter',
            'label=com.docker.compose.project='+project,'--filter',
            'label=com.docker.compose.service=main'],text=True,timeout=15).split()
        ids=list(dict.fromkeys(ids))
        if len(ids)!=1:raise AutorError('Missing or ambiguous rebuilt trial image: '+project)
        row=json.loads(subprocess.check_output(['docker','image','inspect',ids[0]],text=True,timeout=15))[0]
        actual=image_content_digest(row)
        if actual!=item['content_sha256']:
            raise AutorError('Rebuilt task image content changed: '+task+suffix)
        checked.append({'project_suffix':suffix,'image_id':row['Id'],'content_sha256':actual})
    return checked


def cleanup_owned(work):
    """Remove agent and separate verifier containers for these exact trial prefixes."""
    trials=[p.name.lower() for p in (Path(work)/'jobs/evaluation').glob('*') if p.is_dir()]
    rows=subprocess.check_output(['docker','ps','-a','--filter','label=com.docker.compose.project',
        '--format','{{.ID}} {{.Label "com.docker.compose.project"}}'],text=True,timeout=5)
    removed=[]
    for line in rows.splitlines():
        fields=line.split()
        if len(fields)!=2:continue
        ident,project=fields
        if any(project==t+'__env' or project.startswith(t+'__verifier__') for t in trials):
            removed.append(ident)
    if removed:
        subprocess.run(['docker','rm','-f',*removed],check=True,capture_output=True,timeout=10)
    return removed


def verify_activation(trial, harness, instruction):
    from harbor.agents.installed.base import render_prompt_template
    from harbor.models.task.task import strip_canary
    prompt = Path(harness)/'prompt.md'
    row = read_json(Path(trial)/'result.json')
    if Path(row['config']['agent']['kwargs']['prompt_template_path']).resolve() != prompt.resolve():
        raise AutorError('Harbor used a different prompt path')
    rendered = render_prompt_template(prompt, strip_canary(instruction))
    command_fragment = '-- ' + shlex.quote(rendered) + ' 2>&1'
    if command_fragment not in (Path(trial)/'trial.log').read_text(errors='replace'):
        raise AutorError('Rendered candidate prompt not found in actual Codex command')
    return {'file':'prompt.md','sha256':sha(prompt),'used':True,
            'rendered_prompt_sha256':hashlib.sha256(rendered.encode()).hexdigest(),
            'method':'Exact rendered prompt matched in the executed Codex command log'}


def run(args):
    pilot=Path(args.pilot).resolve();work=Path(args.work).resolve();harness=Path(args.harness).resolve()
    if sha(pilot/'pilot.json') != args.pilot_sha256 or support_hash() != args.support_sha256:
        raise AutorError('Frozen evaluator code or pilot metadata changed')
    if sha(args.instructions) != args.instructions_sha256:
        raise AutorError('Public instruction snapshots changed')
    meta=load(pilot); instructions=read_json(args.instructions)
    if set(instructions) != set(meta['evaluator']['task_names']):
        raise AutorError('Instruction coverage differs from the frozen task set')
    if not finite_number(args.timeout) or args.timeout <= 30:
        raise AutorError('Need more than 30 seconds including cancellation reserve')
    verify_harbor_version()
    verify_images(args.images,args.images_sha256,meta['evaluator']['task_names'])
    work.mkdir(parents=True,exist_ok=True)
    with (work/'started.json').open('x') as f:
        json.dump({'pilot_sha256':args.pilot_sha256,'prompt_sha256':sha(harness/'prompt.md')},f)
    prompt_hash=sha(harness/'prompt.md')
    job=read_json(pilot/'search-job.json');job['jobs_dir']=str(work/'jobs')
    job['agents'][0]['kwargs']['prompt_template_path']=str(harness/'prompt.md')
    # Keep build images for post-run content checks; Compose down still removes containers.
    job['environment']['delete']=False
    write_json(work/'job.json',job)
    try:
        seconds=run_process(['harbor','run','--config',str(work/'job.json')],work,work/'harbor-logs',args.timeout-30)
        load(pilot)
        verify_images(args.images,args.images_sha256,meta['evaluator']['task_names'])
        if support_hash() != args.support_sha256 or sha(args.instructions) != args.instructions_sha256:
            raise AutorError('Evaluator support or instruction snapshots changed during evaluation')
        if sha(harness/'prompt.md') != prompt_hash:
            raise AutorError('Candidate prompt changed during evaluation')
        result=adapters.parse_harbor(work/'jobs/evaluation',meta['evaluator'])
        for task in result['tasks']:
            trial=Path(task['trial_dir'])
            full_feedback=(trial/'verifier/test-stdout.txt').read_text(errors='replace')
            if invalid_verifier_feedback(full_feedback):
                raise AutorError('Verifier infrastructure failure: '+task['task_id'])
            task['image_checks']=verify_trial_images(trial,read_json(args.images),task['task_id'])
            task['activation']=verify_activation(trial,harness,instructions[task['task_id']])
            log=trial/'agent/codex.txt'
            if log.exists():
                # Execution evidence only; no hidden test/solution files are read.
                task['execution_feedback']=log.read_text(errors='replace')[-18000:]
        result.update(seconds=seconds,pilot_sha256=args.pilot_sha256,
                      task_trials_completed=len(result['tasks']))
        write_json(args.result,result)
        return result
    except BaseException as error:
        try:cleanup={'removed_containers':cleanup_owned(work)}
        except Exception as cleanup_error:cleanup={'error':str(cleanup_error),'requires_operator_check':True}
        write_json(work/'failure.json',{'error_type':type(error).__name__,'error':str(error),
                  'cleanup':cleanup,
                  'note':'No zero score is substituted. Inspect trial results; no automatic replay.'})
        raise


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ['pilot','pilot-sha256','instructions','instructions-sha256','support-sha256','images','images-sha256','harness','work','result']:
        p.add_argument('--'+key,required=True)
    p.add_argument('--timeout',type=float,default=900)
    return p


if __name__=='__main__':
    args=parser().parse_args()
    def cancelled(signum,frame):
        raise KeyboardInterrupt('Evaluator cancelled; stop the owned Harbor process group')
    signal.signal(signal.SIGTERM,cancelled)
    try:run(args)
    except (AutorError,OSError,ValueError) as error:
        print('Frozen Harbor evaluator: '+str(error),file=sys.stderr);raise SystemExit(1)
