"""Equal-cap synthetic A/B/C experiment. This is not benchmark evidence."""
import argparse
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autor import adapters, engine
from autor.cli import initialize_preset, summary
from autor.presets import demo_config
from autor.storage import write_json


def propose(config, parent, evidence, work, timeout):
    number = evidence['proposal_number']
    direction = evidence.get('search_directive', {}).get('operator', 'undirected')
    return {'diagnosis': 'Synthetic controlled task vectors, not real failures.',
            'hypothesis': 'Synthetic proposal '+str(number),
            'edits': [{'path':'prompt.md','content':'{{ instruction }}\nFixture=%s:%s\n' % (number, direction)}]}


def evaluate(config, harness, work, timeout):
    text = (Path(harness)/'prompt.md').read_text()
    tag = text.split('Fixture=')[-1].strip() if 'Fixture=' in text else '0:seed'
    number, direction = tag.split(':'); number = int(number)
    values = {0:[.2,.2],1:[.8,.3],2:[.3,.8]}.get(number, [.7,.4])
    if direction == 'mix': values = [.8,.8]
    return {'status':'ok','synthetic':True,'tasks':[
        {'task_id':name,'score':score,'feedback':'Assigned synthetic fixture score.'}
        for name,score in zip(['fixture-a','fixture-b'],values)]}


def run(root):
    root = Path(root)
    if root.exists():raise ValueError('Choose a new directory')
    root.mkdir(parents=True)
    results = {}
    with patch.object(adapters,'propose',propose), patch.object(adapters,'evaluate',evaluate):
        for name,strategy,mixing in [('A','beam',False),('B','adaptive_beam',False),('C','adaptive_beam',True)]:
            config=demo_config(strategy,6)
            config['budget']['max_evaluations']=8
            if strategy=='adaptive_beam':config['search']={'mixing':mixing}
            folder=root/name
            initialize_preset(folder,config)
            engine.Experiment(folder).run(1)
            state=engine.Experiment(folder).run()
            results[name]={**summary(state),'completed_task_trials':2*state['evaluations_used'],
                           'sample_counts':{c['id']:len(c.get('samples',[1])) for c in state['candidates'] if c['status']=='evaluated'}}
    write_json(root/'comparison.json',{'synthetic':True,'model_calls':0,
        'warning':'Synthetic outcomes exercise control flow; they do not estimate strategy effectiveness.',
        'caps':{'max_proposals':6,'max_evaluations':8,'max_task_trials':16,'max_seconds':60},
        'outer_hash':state['outer_hash'],'results':results})
    return results


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('output',type=Path)
    args=parser.parse_args();run(args.output);print(args.output/'comparison.json')
