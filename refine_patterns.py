"""Combine independently feasible homogeneous groups and refine round allocations."""
import argparse
import copy
import json
import math
import time
from collections import defaultdict
from pathlib import Path

from competition_solver import atomic_json
from optimize_patterns import estimated_score
from platform_check import check
from solver import Config, Model, load_orders, load_blanks, validate_plan


def score(v):
    k,f,r,c=v
    return 40*min(1,90000/k)+30*f/r+20*c/4999+10


def vector(plan, model):
    m=validate_plan(plan, model.orders, model.cfg, model.blanks)
    return [m['knives'],m['finished_weight'],m['blank_weight'],round(m['coverage']*len(model.orders))]


def group_vector(batches,lookup,blanks):
    k=f=r=c=0
    for b in batches:
        if len(b['orders'])>1:c+=len(b['orders'])
        for s,p,nb in zip(b['length_scheme'],b['counts'],b['blank_counts']):
            k+=1+sum(round(L/lookup[n].size) for n,L in s.items())
            f+=sum(L*p*lookup[n].linear_weight for n,L in s.items())
            r+=nb*blanks[b['blank_type']].weight
    return [k,f,r,c]


def portfolio(paths,model):
    lookup={o.oid:o for o in model.orders}; blanks={b.bid:b for b in model.blanks}
    options=defaultdict(list)
    for path in paths:
        plan=json.loads(path.read_text(encoding='utf-8'))
        try:validate_plan(plan,model.orders,model.cfg,model.blanks)
        except ValueError:continue
        groups=defaultdict(list)
        for b in plan:
            o=lookup[b['orders'][0]];groups[o.steel,o.diameter].append(b)
        for key,batches in groups.items():options[key].append((group_vector(batches,lookup,blanks),batches,str(path)))
    chosen={g:opts[0] for g,opts in options.items()}
    total=[sum(v[0][j] for v in chosen.values()) for j in range(4)]
    for iteration in range(20):
        changed=False
        for g,opts in options.items():
            old=chosen[g]
            for opt in opts:
                trial=[total[j]-old[0][j]+opt[0][j] for j in range(4)]
                if score(trial)>score(total)+1e-10:
                    total=trial;chosen[g]=opt;old=opt;changed=True
        if not changed:break
    return copy.deepcopy([b for _,batches,_ in chosen.values() for b in batches]), {str(k):v[2] for k,v in chosen.items()}


def refine(plan,model,passes):
    ids={o.oid:i for i,o in enumerate(model.orders)}
    delivered=defaultdict(int)
    for b in plan:
        for s,p in zip(b['length_scheme'],b['counts']):
            for n,L in s.items():delivered[n]+=round(L/model.orders[ids[n]].size)*p
    total=vector(plan,model);history=[]
    for iteration in range(passes):
        changes=0
        for batch in plan:
            blank=next(b for b in model.blanks if b.bid==batch['blank_type'])
            for row in range(len(batch['counts'])):
                old_s=batch['length_scheme'][row];old_p=batch['counts'][row]
                names=list(old_s);ii=tuple(ids[n] for n in names)
                oo=[model.orders[i] for i in ii]
                old_ks=tuple(round(old_s[n]/o.size) for n,o in zip(names,oo))
                old_r=model.make_round(ii,old_ks,old_p,blank)
                other=[delivered[n]-k*old_p for n,k in zip(names,old_ks)]
                best_r=old_r;best_p=old_p;best_total=total
                for p in range(1,model.parallel_limit(ii)+1):
                    lo=[max(0,math.ceil((o.pieces-q)/p)) for o,q in zip(oo,other)]
                    hi=[(model.caps[i]-q)//p for i,q in zip(ii,other)]
                    if any(a>b for a,b in zip(lo,hi)):continue
                    target=max(0,model.cfg.min_bed_length-model.cfg.round_trim)
                    # Two different completions of a too-short row; both obey caps.
                    candidates=[lo, [max(a,min(z,math.ceil(k*old_p/p))) for a,z,k in zip(lo,hi,old_ks)]]
                    for seed in candidates:
                        ks=seed.copy();length=sum(k*o.size for k,o in zip(ks,oo))
                        if length<target:
                            for j in sorted(range(len(oo)),key=lambda j:-oo[j].size):
                                add=min(hi[j]-ks[j],max(0,math.ceil((target-length-1e-8)/oo[j].size)))
                                ks[j]+=add;length+=add*oo[j].size
                        r=model.make_round(ii,tuple(ks),p,blank)
                        if r is None:continue
                        candidate=[total[0]-old_r.knives+r.knives,total[1]-old_r.finished+r.finished,
                                   total[2]-old_r.raw+r.raw,total[3]]
                        if score(candidate)>score(best_total)+1e-10:
                            best_r,best_p,best_total=r,p,candidate
                if best_r is not old_r:
                    for n,k,q in zip(names,best_r.ks,other):delivered[n]=q+k*best_p
                    batch['length_scheme'][row]={n:round(k*o.size,9) for n,k,o in zip(names,best_r.ks,oo) if k}
                    batch['counts'][row]=best_p;batch['blank_counts'][row]=best_r.blanks
                    total=best_total;changes+=1
            # Switching all rows together keeps the single blank type per scheme.
            for alternative in model.blanks:
                if alternative.bid==batch['blank_type']:continue
                rows=[]
                for s,p in zip(batch['length_scheme'],batch['counts']):
                    ii=tuple(ids[n] for n in s);ks=tuple(round(L/model.orders[i].size) for i,L in zip(ii,s.values()))
                    rows.append(model.make_round(ii,ks,p,alternative))
                if all(rows):
                    old_weight=sum(batch['blank_counts'])*next(b.weight for b in model.blanks if b.bid==batch['blank_type'])
                    new_weight=sum(r.raw for r in rows)
                    if new_weight<old_weight-1e-8:
                        total[2]+=new_weight-old_weight;batch['blank_type']=alternative.bid
                        batch['blank_counts']=[r.blanks for r in rows];changes+=1
        exact=vector(plan,model)
        if any(not math.isclose(a,b,rel_tol=1e-9,abs_tol=1e-5) for a,b in zip(total,exact)):
            raise ValueError('Incremental cost drift')
        if not check(plan)['passed']:raise ValueError('Independent check failed')
        history.append(dict(iteration=iteration,changes=changes,estimated_score=score(exact),totals=exact))
        print(json.dumps(history[-1]),flush=True)
        if changes==0:break
    return plan,history


def run(args):
    started=time.perf_counter();root=Path(args.output);root.mkdir(parents=True,exist_ok=True)
    config=json.loads(Path('runs/pattern_dp_pool_5pct/config.json').read_text())
    cfg=Config(**config);orders=load_orders('data/orders.normalized.csv',cfg)
    model=Model(orders,cfg,load_blanks('data/blanks.normalized.csv'))
    paths=[Path(args.initial)]
    for directory in args.inputs:paths+=sorted(Path(directory).glob('candidate_*.json'))
    paths=[p for p in paths if not p.name.endswith('.metrics.json')]
    plan,origins=portfolio(paths,model)
    before=validate_plan(plan,orders,cfg,model.blanks)
    atomic_json(root/'portfolio.json',plan)
    plan,history=refine(plan,model,args.passes)
    after=validate_plan(plan,orders,cfg,model.blanks)
    atomic_json(root/'result.json',plan);atomic_json(root/'config.json',config)
    audit=json.loads(Path('runs/pattern_dp_pool_5pct/data_audit.json').read_text(encoding='utf-8'))
    atomic_json(root/'data_audit.json',audit)
    atomic_json(root/'report.json',dict(before=before,after=after,history=history,origins=origins,
                                     elapsed_seconds=time.perf_counter()-started,official_acceptance_verified=False))
    print(json.dumps(after),flush=True)


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--inputs',nargs='+',default=['runs/pattern_dp_pool_5pct','runs/continued_credit'])
    ap.add_argument('--initial',default='submission_optimized/初赛结果_棒材优化.json')
    ap.add_argument('--output',default='runs/continued_refined')
    ap.add_argument('--passes',type=int,default=5)
    run(ap.parse_args())
