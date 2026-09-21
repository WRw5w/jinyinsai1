"""Run the C++ heterogeneous-parallel search and validate every exported result."""
import argparse
import json
import subprocess
import time
from pathlib import Path
from competition_solver import atomic_json
from platform_check import check
from solver import Config,Model,load_orders,load_blanks,validate_plan
from platform_score import evaluate, load_scoring_data

def goal(r):return min(100.0,r["score_uncapped"]+30.0)

def run(args):
    started=time.perf_counter();source=Path(args.initial);root=Path(args.output);root.mkdir(parents=True,exist_ok=True)
    config=json.loads((source.parent/'config.json').read_text());cfg=Config(**config)
    if (cfg.length_mode,cfg.trim,cfg.min_bed_length,cfg.bed_length,cfg.bed_weight,cfg.rolling_yield) != ('net_shared_trim',1,50,150,60000,1):
        raise ValueError('C++ kernel is specialized to the formal competition physical constraints')
    orders=load_orders('data/orders.normalized.csv',cfg);blanks=load_blanks('data/blanks.normalized.csv');model=Model(orders,cfg,blanks)
    plan=json.loads(source.read_text(encoding='utf-8'));physical_before=validate_plan(plan,orders,cfg,blanks);before=evaluate(plan);ids={o.oid:i for i,o in enumerate(orders)}
    if [b.bid for b in blanks]!=list(range(1,len(blanks)+1)):raise ValueError('Blank IDs must be consecutive')
    inp=root/'engine_input.txt';out=root/'engine_output.txt'
    with inp.open('w') as f:
        f.write(f'{len(orders)} {len(blanks)} {len(plan)}\n')
        for i,o in enumerate(orders):
            costs=[0]+[int(round(k*o.size,9)//o.size)+1 for k in range(1,int(148/o.size)+4)]
            mu=3.141592653589793*(int(o.diameter)/1000)**2/4*o.density
            f.write(f'{o.pieces} {model.caps[i]} {o.size:.12f} {o.linear_weight:.15f} {model.parallel_limit((i,))} {mu:.15f} {len(costs)} '+' '.join(map(str,costs))+'\n')
        f.write(' '.join(str(b.weight) for b in blanks)+'\n')
        for batch in plan:
            f.write(f"{batch['blank_type']} {len(batch['orders'])} {len(batch['counts'])}\n")
            f.write(' '.join(str(ids[n]) for n in batch['orders'])+'\n')
            for s,p,nb in zip(batch['length_scheme'],batch['counts'],batch['blank_counts']):
                f.write(f'{p} {nb} {len(s)} '+ ' '.join(f'{ids[n]} {round(L/orders[ids[n]].size)}' for n,L in s.items())+'\n')
    subprocess.run([str(Path(args.engine).resolve()),str(inp),str(out),str(args.seconds),str(args.seed),str(args.beam)],check=True)
    with out.open() as f:
        if int(f.readline())!=len(plan):raise ValueError('Batch count mismatch')
        for batch in plan:
            nr=int(f.readline());batch['counts']=[];batch['blank_counts']=[];batch['length_scheme']=[]
            for _ in range(nr):
                p,nb,n,*pairs=map(int,f.readline().split())
                if len(pairs)!=2*n:raise ValueError('Row parse mismatch')
                s={orders[i].oid:round(k*orders[i].size,9) for i,k in zip(pairs[::2],pairs[1::2])}
                batch['counts'].append(p);batch['blank_counts'].append(nb);batch['length_scheme'].append(s)
    physical_after=validate_plan(plan,orders,cfg,blanks);after=evaluate(plan);independent=check(plan)
    if not independent['passed']:raise ValueError(independent['error_counts'])
    if goal(after)<goal(before)-1e-8:raise ValueError('Knife-hunting search regressed on its own objective')
    atomic_json(root/'result.json',plan);atomic_json(root/'config.json',config)
    atomic_json(root/'data_audit.json',json.loads((source.parent/'data_audit.json').read_text(encoding='utf-8')))
    report=dict(before=before,after=after,physical_before=physical_before,physical_after=physical_after,independent_check=independent,elapsed_seconds=time.perf_counter()-started,official_acceptance_verified=False,seed=args.seed,beam=args.beam)
    atomic_json(root/'report.json',report);print(json.dumps(after),flush=True)

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--initial',default='runs/calibrated_initial/result.json')
    ap.add_argument('--output',default='runs/calibrated_heterogeneous')
    ap.add_argument('--engine',default='calibrated_knife_hunt.exe')
    ap.add_argument('--seconds',type=float,default=120)
    ap.add_argument('--seed',type=int,default=9106)
    ap.add_argument('--beam',type=int,default=48)
    run(ap.parse_args())
