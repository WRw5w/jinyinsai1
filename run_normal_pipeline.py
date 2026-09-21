"""Combine validated normal candidates, refine, and package the best result.

The stopping target is a local estimate. No score is claimed as official.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from competition_solver import atomic_json
from refine_patterns import portfolio
from platform_check import check
from solver import Config,Model,load_orders,load_blanks,validate_plan

def run(args):
    root=Path(args.output);root.mkdir(parents=True,exist_ok=True)
    source=Path('runs/continued_triples')
    config=json.loads((source/'config.json').read_text());cfg=Config(**config)
    model=Model(load_orders('data/orders.normalized.csv',cfg),cfg,load_blanks('data/blanks.normalized.csv'))
    audit=json.loads((source/'data_audit.json').read_text(encoding='utf-8'))
    history=[]
    roots=['continued_triples','normal_heterogeneous','normal_heterogeneous_next',
           'material_refine_heterogeneous','normal_group_search','normal_group_multi8','normal_group_hetero8']
    paths=[Path('runs')/name/'result.json' for name in roots if (Path('runs')/name/'result.json').exists()]
    paths += [Path(p) for p in args.extra_inputs]
    plan,origins=portfolio(paths,model)
    if not check(plan)['passed']:raise ValueError('Combined candidate failed physical validation')
    def keep(plan,stage):
        metrics=validate_plan(plan,model.orders,cfg,model.blanks)
        if not check(plan)['passed']:raise ValueError('Independent check failed')
        atomic_json(root/'result.json',plan);atomic_json(root/'config.json',config);atomic_json(root/'data_audit.json',audit)
        history.append(dict(stage=stage,metrics=metrics));print(json.dumps(history[-1]),flush=True)
        atomic_json(root/'progress.json',dict(history=history,target=args.target,
                    local_target_reached=metrics['platform_score_estimate']>=args.target,official_acceptance_verified=False))
        return metrics
    metrics=keep(plan,'portfolio');atomic_json(root/'origins.json',origins)
    for iteration in range(args.cycles):
        commands=[('material',[sys.executable,'-X','utf8','material_refine.py','--seconds','15','--passes','2','--repack-seconds','60']),
                  ('heterogeneous',[sys.executable,'-X','utf8','heterogeneous_search.py','--seconds','120','--seed',str(30000+iteration),'--beam','64'])]
        for name,command in commands:
            if metrics['platform_score_estimate']>=args.target:break
            output=root/f'{iteration}_{name}'
            subprocess.run(command+['--initial',str(root/'result.json'),'--output',str(output)],check=True)
            trial=json.loads((output/'result.json').read_text())
            updated=validate_plan(trial,model.orders,cfg,model.blanks)
            if updated['platform_score_estimate']>metrics['platform_score_estimate']+1e-9:
                plan=trial;metrics=keep(plan,f'{iteration}_{name}')
        if metrics['platform_score_estimate']>=args.target:break
    # Save immutable provenance for the exact files used by this run.
    hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    atomic_json(root/'report.json',dict(history=history,after=metrics,input_sha256=hashes,
               local_target=args.target,local_target_reached=metrics['platform_score_estimate']>=args.target,
               official_acceptance_verified=False))
    subprocess.run([sys.executable,'-X','utf8','build_submission.py','--input',str(root/'result.json'),
        '--config',str(root/'config.json'),'--audit',str(root/'data_audit.json'),'--output-dir',args.package],check=True)

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',default='runs/normal_target_985')
    ap.add_argument('--package',default='submission_normal_best')
    ap.add_argument('--target',type=float,default=98.5)
    ap.add_argument('--cycles',type=int,default=3)
    ap.add_argument('--extra-inputs',nargs='*',default=[])
    run(ap.parse_args())
