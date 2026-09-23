"""Repack every equal-bundle subset of a complete order component at once."""
import argparse
import json
import math
import time
from collections import defaultdict
from pathlib import Path

from competition_solver import atomic_json
from normal_group_search import Components, exact_two_split, materialize, multi_split
from platform_check import check
from refine_patterns import refine, score, vector
from solver import Config, Model, load_blanks, load_orders, validate_plan


def run(args):
    start=time.perf_counter();root=Path(args.output);root.mkdir(parents=True,exist_ok=True)
    source=Path(args.initial);config=json.loads((source.parent/'config.json').read_text())
    cfg=Config(**config)
    if cfg.max_overproduction_ratio>0.05 or cfg.max_rounds>100 or cfg.length_mode!='net_shared_trim':raise ValueError('Normal branch constraints required')
    orders=load_orders('data/orders.normalized.csv',cfg);blanks=load_blanks('data/blanks.normalized.csv')
    model=Model(orders,cfg,blanks);lookup={o.oid:i for i,o in enumerate(orders)};blank_byid={b.bid:b for b in blanks}
    plan=json.loads(source.read_text());before=validate_plan(plan,orders,cfg,blanks)
    total=vector(plan,model);rows=[];delivered=[0]*len(orders);uf=Components(len(orders))
    for b in plan:
        for s,p in zip(b['length_scheme'],b['counts']):
            ks={lookup[n]:round(L/orders[lookup[n]].size) for n,L in s.items()}
            ids=tuple(ks);r=model.make_round(ids,tuple(ks.values()),p,blank_byid[b['blank_type']])
            rows.append(dict(ks=ks,p=p,bid=b['blank_type'],r=r));uf.join(ids)
            for i,k in ks.items():delivered[i]+=k*p
    pools=defaultdict(list)
    for j,r in enumerate(rows):pools[uf.find(next(iter(r['ks']))),r['bid'],r['p']].append(j)
    deadline=time.perf_counter()+args.seconds;history=[];attempts=0
    for key,selected in sorted(pools.items(),key=lambda item:-len(item[1])):
        if time.perf_counter()>=deadline:break
        if len(selected)<2:continue
        attempts+=1;old=[rows[j] for j in selected];p=key[2];blank=blank_byid[key[1]]
        ids=tuple(dict.fromkeys(i for r in old for i in r['ks']));oo=[orders[i] for i in ids]
        other=[delivered[i]-sum(r['ks'].get(i,0)*p for r in old) for i in ids]
        lo=[max(0,math.ceil((o.pieces-q)/p)) for o,q in zip(oo,other)]
        hi=[(model.caps[i]-q)//p for i,q in zip(ids,other)]
        if any(a>b for a,b in zip(lo,hi)):raise ValueError('Existing equal-p vector should always be feasible')
        sizes=[o.size for o in oo];linear=oo[0].linear_weight;length=sum(k*s for k,s in zip(lo,sizes))
        cap=min(cfg.bed_length,cfg.bed_weight/(p*linear))-cfg.round_trim;low=cfg.min_bed_length-cfg.round_trim
        oldk=sum(r['r'].knives for r in old);oldf=sum(r['r'].finished for r in old);oldraw=sum(r['r'].raw for r in old)
        best=None;best_total=total;bestscore=score(total)
        for nr in range(max(1,math.ceil(length/cap-1e-10)),len(selected)+1):
            if not nr*low-1e-8<=length<=nr*cap+1e-8:continue
            optimistic_raw=math.ceil((length+nr*cfg.round_trim)*p*linear/blank.weight-1e-10)*blank.weight
            optimistic=[total[0]-oldk+sum(lo)+nr,total[1]-oldf+length*p*linear,total[2]-oldraw+optimistic_raw,total[3]]
            if score(optimistic)<=bestscore+1e-10:continue
            if nr==1:packed=[lo]
            elif nr==2:
                left=exact_two_split(lo,sizes,p,linear,blank.weight,low,cap,cfg.round_trim)
                packed=[left,[v-k for v,k in zip(lo,left)]] if left is not None else None
            else:packed=multi_split(lo,sizes,p,linear,blank.weight,low,cap,cfg.round_trim,nr)
            if packed is None:continue
            proposed=[model.make_round(ids,ks,p,blank) for ks in packed]
            if not all(proposed):continue
            trial=[total[0]-oldk+sum(r.knives for r in proposed),total[1]-oldf+sum(r.finished for r in proposed),total[2]-oldraw+sum(r.raw for r in proposed),total[3]]
            value=score(trial)
            if value>bestscore+1e-10:best=proposed;best_total=trial;bestscore=value
        if best is None:continue
        for t,i in enumerate(ids):delivered[i]=other[t]+sum(r.ks[t]*p for r in best)
        for j,r in zip(selected,best):rows[j]=dict(ks={i:k for i,k in zip(ids,r.ks) if k},p=p,bid=blank.bid,r=r)
        for j in selected[len(best):]:rows[j]=None
        total=best_total
        history.append(dict(old_rounds=len(selected),new_rounds=len(best),parallel=p,estimated_score=bestscore,totals=total,seconds=time.perf_counter()-start))
        print(json.dumps(history[-1]),flush=True)
    plan=materialize(rows,model);exact=vector(plan,model)
    if any(not math.isclose(a,b,rel_tol=1e-9,abs_tol=1e-5) for a,b in zip(total[:3],exact[:3])):raise ValueError('Incremental metric drift')
    plan,post=refine(plan,model,3);checked=check(plan)
    if not checked['passed']:raise ValueError(checked['error_counts'])
    metrics=validate_plan(plan,orders,cfg,blanks)
    atomic_json(root/'result.json',plan);atomic_json(root/'config.json',config)
    atomic_json(root/'data_audit.json',json.loads((source.parent/'data_audit.json').read_text(encoding='utf-8')))
    atomic_json(root/'report.json',dict(before=before,after=metrics,attempts=attempts,accepted=len(history),history=history,post_refinement=post,independent_check=checked,seconds=time.perf_counter()-start,official_acceptance_verified=False))
    print(json.dumps(metrics),flush=True)


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--initial',default='runs/normal_group_hetero8_focus/result.json')
    ap.add_argument('--output',default='runs/normal_component_pack')
    ap.add_argument('--seconds',type=float,default=180)
    run(ap.parse_args())
