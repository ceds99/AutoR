"""Prepare and run a gated, equal-cap local Harbor A/B/C comparison."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autor import engine
from autor.cli import initialize_preset, summary
from autor.config import finite_number
from autor.presets import harbor_preset
from autor.storage import AutorError, digest, outer_fingerprint, read_json, write_json
from scripts.terminal_bench_pilot import load
from scripts.frozen_harbor_evaluator import sha, support_hash, verify_images, invalid_verifier_feedback


def natural_failure(trial):
    trial=Path(trial);row=read_json(trial/'result.json')
    score=(row.get('verifier_result') or {}).get('rewards',{}).get('reward')
    if row.get('exception_info') or not finite_number(score) or score >= 1:
        raise AutorError('Gate requires a valid below-perfect task result')
    if not (row.get('agent_execution') or {}).get('finished_at'):
        raise AutorError('Agent execution did not complete')
    events=[]
    for line in (trial/'agent/codex.txt').read_text(errors='replace').splitlines():
        try:events.append(json.loads(line))
        except ValueError:pass
    if not any(e.get('type')=='turn.completed' for e in events):
        raise AutorError('No completed agent turn; infrastructure/timeout is not a natural failure')
    feedback=(trial/'verifier/test-stdout.txt').read_text(errors='replace')
    if invalid_verifier_feedback(feedback):
        raise AutorError('Scoring output contains an infrastructure failure, not a natural failure')
    if not re.search(r'collected\s+[1-9]\d*\s+items?',feedback) or 'AssertionError' not in feedback:
        raise AutorError('Need executed scoring tests and concrete failure feedback')
    return {'task':row['task_name'],'trial':row['trial_name'],'score':score,
            'task_source':row.get('task_id'),'agent_info':row.get('agent_info'),
            'task_checksum':row['task_checksum'],'result_sha256':sha(trial/'result.json'),
            'agent_log_sha256':sha(trial/'agent/codex.txt'),
            'verifier_stdout_sha256':sha(trial/'verifier/test-stdout.txt')}


def prepare(args):
    root=Path(args.root).resolve();pilot=Path(args.pilot).resolve()
    if root.exists():raise AutorError('Choose a new comparison directory')
    meta=load(pilot);gate=natural_failure(args.failure_trial)
    tasks=meta['evaluator']['task_names']
    if gate['task'] not in tasks:raise AutorError('Failure task must be in the fixed comparison set')
    if (gate.get('task_source') or {}).get('git_commit_id')!=meta['task_revision']:
        raise AutorError('Failure source revision differs from the comparison')
    info=gate.get('agent_info') or {}
    if info.get('version')!=meta['evaluator']['agent_version'] or (info.get('model_info') or {}).get('name')!=meta['evaluator']['model'].split('/')[-1]:
        raise AutorError('Failure model/CLI differs from the comparison')
    instructions=read_json(args.instructions)
    if set(instructions)!=set(tasks):raise AutorError('Public instruction coverage mismatch')
    images=read_json(args.images)
    if {x['task'] for x in images}!=set(tasks):raise AutorError('Select exactly the comparison image receipts')
    verify_images(args.images,sha(args.images),tasks)
    root.mkdir(parents=True)
    write_json(root/'instructions.json',instructions);write_json(root/'images.json',images)
    settings=meta['evaluator']
    common=harbor_preset(args.outer_model,settings['model'],settings['agent_version'],tasks)
    common['objective']+=' Diagnose failures from execution feedback; do not embed task answers or task-name dispatch.'
    common['proposer'].update(executable=args.codex,cli_version=args.codex_version,reasoning_effort='low')
    common['evaluator']={'kind':'command','require_activation_file':'prompt.md','task_names':tasks,
        'argv':[sys.executable,str(Path(__file__).with_name('frozen_harbor_evaluator.py')),
            '--pilot',str(pilot),'--pilot-sha256',sha(pilot/'pilot.json'),
            '--instructions',str(root/'instructions.json'),'--instructions-sha256',sha(root/'instructions.json'),
            '--images',str(root/'images.json'),'--images-sha256',sha(root/'images.json'),
            '--support-sha256',support_hash(),'--harness','{harness}','--work','{work}',
            '--result','{result}','--timeout','{timeout}']}
    common['budget']={'max_proposals':6,'max_evaluations':8,'max_seconds':1800,
                      'proposal_timeout':120,'evaluation_timeout':900}
    for name,strategy,mixing in [('A','beam',False),('B','adaptive_beam',False),('C','adaptive_beam',True)]:
        config=json.loads(json.dumps(common));config['strategy']=strategy
        config['search']={'beam_width':2,'children_per_parent':2}
        if strategy=='adaptive_beam':config['search'].update(mixing=mixing,confirmation_samples=2)
        initialize_preset(root/name,config)
    plan={'outer_hash':outer_fingerprint(),'runner_sha256':sha(__file__),'failure_gate':gate,
          'tasks':tasks,'budget_per_arm':common['budget'],'max_task_trials_per_arm':8*len(tasks),
          'holdout_evaluated':False,'pilot_sha256':sha(pilot/'pilot.json'),
          'status':'prepared','note':'Equal caps, actual usage reported. Small exploratory comparison, not a significance test.'}
    write_json(root/'plan.json',plan)
    write_json(root/'contract.json',{'plan_hash':digest(plan)})
    return plan


def check(root):
    plan=read_json(root/'plan.json')
    if digest(plan)!=read_json(root/'contract.json')['plan_hash'] or plan['outer_hash']!=outer_fingerprint() or plan['runner_sha256']!=sha(__file__):
        raise AutorError('Frozen comparison plan/code changed')
    return plan


def baselines(root):
    root=Path(root);plan=check(root);states={}
    if any(engine.load(root/name)['proposals_used'] for name in ['A','B','C']):
        raise AutorError('Cannot re-open the baseline gate after search started')
    for name in ['A','B','C']:
        states[name]=engine.Experiment(root/name).run(0)
    reasons=[];checksums=[]
    for name,state in states.items():
        seed=state['candidates'][0]
        samples=seed.get('samples') or [{'score':seed.get('score'),'tasks':seed.get('task_scores',[])}]
        if seed['status']!='evaluated':reasons.append(name+': invalid seed measurement');continue
        if any(sample['score']>=1 for sample in samples):reasons.append(name+': seed reached ceiling; failure is not consistently reproduced')
        if any(not any(t['task_id']==plan['failure_gate']['task'] and t['score']<1 for t in sample['tasks']) for sample in samples):
            reasons.append(name+': selected natural failure did not recur in every seed sample')
        for sample in samples:
            for task in sample['tasks']:
                if task['task_id']==plan['failure_gate']['task'] and task['score']<1:
                    try:natural_failure(task['trial_dir'])
                    except (AutorError,OSError,KeyError) as error:
                        reasons.append(name+': invalid failure evidence: '+str(error))
        checksums.append(state['task_checksums'])
        if dict(state['task_checksums']).get(plan['failure_gate']['task'])!=plan['failure_gate']['task_checksum']:
            reasons.append(name+': failure task contents differ from screening')
    if checksums and any(x!=checksums[0] for x in checksums):reasons.append('Task content differs across arms')
    gate={'allowed':not reasons,'reasons':reasons,'seed_hashes':{n:s['candidates'][0]['hash'] for n,s in states.items()},
          'seed_results':{n:summary(s) for n,s in states.items()},
          'seed_state_hashes':{n:digest(s) for n,s in states.items()}}
    if len(set(gate['seed_hashes'].values()))!=1:gate['allowed']=False;gate['reasons'].append('Seed harnesses differ')
    write_json(root/'baseline-gate.json',gate)
    return gate


def search(root):
    root=Path(root);plan=check(root);gate=read_json(root/'baseline-gate.json')
    if not gate['allowed']:raise AutorError('Baseline reproduction gate blocked comparison: '+str(gate['reasons']))
    results={}
    for name in ['A','B','C']:
        state=engine.load(root/name)
        # Before first proposal, verify the exact seed state approved by the gate.
        if state['proposals_used']==0 and digest(state)!=gate['seed_state_hashes'][name]:
            raise AutorError('Seed state changed after gate')
        state=engine.Experiment(root/name).run()
        completed=sum(len(s['tasks']) for c in state['candidates'] for s in c.get('samples',[])) if name!='A' else sum(len(c.get('task_scores',[])) for c in state['candidates'] if c['status']=='evaluated')
        results[name]={**summary(state),'reserved_task_trials':state['evaluations_used']*len(plan['tasks']),
                       'completed_valid_task_trials':completed}
        write_json(root/'comparison.json',{'status':'running' if name!='C' else 'complete',
                   'plan_hash':digest(plan),'holdout_evaluated':False,'results':results})
    return results


def parser():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    init=sub.add_parser('prepare');init.add_argument('root',type=Path)
    for key in ['pilot','failure-trial','instructions','images','outer-model','codex','codex-version']:
        init.add_argument('--'+key,required=True)
    for phase in ['baselines','search']:sub.add_parser(phase).add_argument('root',type=Path)
    return p


if __name__=='__main__':
    args=parser().parse_args()
    result=prepare(args) if args.command=='prepare' else (baselines(args.root) if args.command=='baselines' else search(args.root))
    print(json.dumps(result,ensure_ascii=False))
