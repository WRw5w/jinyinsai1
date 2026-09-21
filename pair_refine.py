"""Time-bounded joint repacking of two to four rounds with exact delivery bounds."""
import argparse
import json
import math
import random
import time
from collections import defaultdict
from pathlib import Path
from competition_solver import atomic_json
from refine_patterns import score, vector, refine
from platform_check import check
from solver import Config,Model,load_orders,load_blanks,validate_plan


def run(args):
    root=Path(args.output);root.mkdir(parents=True,exist_ok=True)
    source=Path(args.initial)
    config=json.loads((source.parent/'config.json').read_text())
    cfg=Config(**config);orders=load_orders('data/orders.normalized.csv',cfg)
    model=Model(orders,cfg,load_blanks('data/blanks.normalized.csv'))
    plan=json.loads(source.read_text());total=vector(plan,model);before=total[:]
    ids={o.oid:i for i,o in enumerate(orders)};delivered=defaultdict(int)
    for b in plan:
        for s,p in zip(b['length_scheme'],b['counts']):
            for n,L in s.items():delivered[n]+=round(L/orders[ids[n]].size)*p
    rng=random.Random(args.seed);deadline=time.perf_counter()+args.seconds
    attempts=accepted=0
    while time.perf_counter()<deadline:
        b=rng.choice(plan)
        if len(b['counts'])<args.source_rounds:continue
        selected=sorted(rng.sample(range(len(b['counts'])),args.source_rounds));attempts+=1
        names=list(dict.fromkeys(n for j in selected for n in b['length_scheme'][j]))
        ii=tuple(ids[n] for n in names);oo=[orders[i] for i in ii]
        blank=next(v for v in model.blanks if v.bid==b['blank_type'])
        old=[]
        for j in selected:
            ks=tuple(round(b['length_scheme'][j].get(n,0)/o.size) for n,o in zip(names,oo))
            old.append(model.make_round(ii,ks,b['counts'][j],blank))
        other=[delivered[n]-sum(r.ks[t]*r.parallel for r in old) for t,n in enumerate(names)]
        need=[max(0,o.pieces-q) for o,q in zip(oo,other)]
        if sum(q*o.size*o.linear_weight for q,o in zip(need,oo))>args.source_rounds*cfg.bed_weight:continue
        old_k=sum(r.knives for r in old);old_f=sum(r.finished for r in old);old_raw=sum(r.raw for r in old)
        best=None;best_total=total
        upper=min(model.parallel_limit(ii),int(cfg.bed_weight/(cfg.min_bed_length*oo[0].linear_weight)))
        for p in range(upper,0,-1):
            lo=[math.ceil(q/p) for q in need]
            hi=[(model.caps[i]-q)//p for i,q in zip(ii,other)]
            if any(x>y for x,y in zip(lo,hi)):continue
            sizes=[o.size for o in oo];L=sum(k*s for k,s in zip(lo,sizes))
            cap=min(cfg.bed_length,cfg.bed_weight/(p*oo[0].linear_weight))-cfg.round_trim
            low=cfg.min_bed_length-cfg.round_trim
            if L>args.source_rounds*cap+1e-8:continue
            variants=[]
            # Combining into one row is possible for light pairs.
            if L<=cap+1e-8:
                ks=lo.copy();length=L
                for t in sorted(range(len(oo)),key=lambda t:-sizes[t]):
                    extra=min(hi[t]-ks[t],max(0,math.ceil((low-length-1e-8)/sizes[t])))
                    ks[t]+=extra;length+=extra*sizes[t]
                r=model.make_round(ii,tuple(ks),p,blank)
                if r:variants.append([r])
            # Split the required pieces into two rows at balanced/edge lengths.
            if 2*low-1e-8<=L<=2*cap+1e-8:
                for target in [min(cap,L-low),L/2]:
                    left=[0]*len(lo);length=0
                    for t in sorted(range(len(lo)),key=lambda t:-sizes[t]):
                        take=min(lo[t],max(0,math.floor((target-length+1e-8)/sizes[t])))
                        left[t]=take;length+=take*sizes[t]
                    right=[k-v for k,v in zip(lo,left)]
                    r1=model.make_round(ii,tuple(left),p,blank);r2=model.make_round(ii,tuple(right),p,blank)
                    if r1 and r2:variants.append([r1,r2])
            for nr in range(3,args.source_rounds+1):
                if not nr*low-1e-8<=L<=nr*cap+1e-8:continue
                for balanced in [False,True]:
                    remaining=lo.copy();remaining_length=L;rows=[]
                    for slot in range(nr):
                        slots=nr-slot
                        target=remaining_length/slots if balanced else min(cap,remaining_length-(slots-1)*low)
                        take=[0]*len(lo);length=0
                        for t in sorted(range(len(lo)),key=lambda t:-sizes[t]):
                            take[t]=remaining[t] if slots==1 else min(remaining[t],max(0,math.floor((target-length+1e-8)/sizes[t])))
                            length+=take[t]*sizes[t]
                        r=model.make_round(ii,tuple(take),p,blank)
                        if r is None:break
                        rows.append(r);remaining=[v-k for v,k in zip(remaining,take)];remaining_length-=length
                    if len(rows)==nr and not any(remaining):variants.append(rows)
            for rows in variants:
                trial=[total[0]-old_k+sum(r.knives for r in rows),
                       total[1]-old_f+sum(r.finished for r in rows),
                       total[2]-old_raw+sum(r.raw for r in rows),total[3]]
                if score(trial)>score(best_total)+1e-10:best=(p,rows);best_total=trial
        if best is None:continue
        p,rows=best
        for t,n in enumerate(names):delivered[n]=other[t]+sum(r.ks[t]*p for r in rows)
        for field in ['counts','length_scheme','blank_counts']:
            for j in reversed(selected):b[field].pop(j)
        for r in rows:
            b['counts'].append(p);b['blank_counts'].append(r.blanks)
            b['length_scheme'].append({n:round(k*o.size,9) for n,k,o in zip(names,r.ks,oo) if k})
        total=best_total;accepted+=1
        if accepted%100==0:
            vector(plan,model)
            atomic_json(root/'checkpoint.json',plan)
            print(json.dumps(dict(attempts=attempts,accepted=accepted,estimated_score=score(total),totals=total)),flush=True)
    exact=vector(plan,model)
    if any(not math.isclose(x,y,rel_tol=1e-9,abs_tol=1e-5) for x,y in zip(total,exact)):
        raise ValueError('Pair refinement incremental metric mismatch')
    plan,history=refine(plan,model,4)
    independent=check(plan)
    if not independent['passed']:raise ValueError(independent['error_counts'])
    metrics=validate_plan(plan,orders,cfg,model.blanks)
    atomic_json(root/'result.json',plan);atomic_json(root/'config.json',config)
    atomic_json(root/'data_audit.json',json.loads((source.parent/'data_audit.json').read_text(encoding='utf-8')))
    atomic_json(root/'report.json',dict(before=before,after=metrics,attempts=attempts,accepted=accepted,
                                      seed=args.seed,search_seconds=args.seconds,source_rounds=args.source_rounds,post_refinement=history,
                                      independent_check=independent,official_acceptance_verified=False))
    print(json.dumps(metrics),flush=True)


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--initial',default='runs/continued_refined/result.json')
    ap.add_argument('--output',default='runs/continued_pairs')
    ap.add_argument('--seconds',type=float,default=90)
    ap.add_argument('--seed',type=int,default=916)
    ap.add_argument('--source-rounds',type=int,choices=[2,3,4],default=2)
    run(ap.parse_args())
