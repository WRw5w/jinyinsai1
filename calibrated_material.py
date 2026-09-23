"""Conservative material search using the three-feedback calibrated score.

Physical feasibility uses untruncated original diameters and canonical integer
piece lengths. Cost counts one end cut per order segment and predicts material
weight with integer-mm diameter, reproducing the observed evaluator behavior.
"""
import argparse
import copy
import json
import math
import random
import time
from collections import defaultdict
from pathlib import Path

from build_submission import merge_compatible
from competition_solver import atomic_json
from material_refine import components, choose_blanks
from platform_check import check
from solver import Config, Model, load_orders, load_blanks, validate_plan


def score(v):
    k, finished, raw, coverage=v
    return 40*min(1,90000/k)+30*finished/raw+20*coverage/4999+10


def row_vector(scheme,p,nb,blank,orders):
    knives=sum(int(L//orders[n].size)+1 for n,L in scheme.items())
    finished=sum(L*p*math.pi*(int(orders[n].diameter)/1000)**2/4*orders[n].density for n,L in scheme.items())
    return [knives,finished,nb*blank.weight,0]


def vector(plan,orders,blanks):
    total=[0,0.,0.,0]
    for b in plan:
        for scheme,p,nb in zip(b['length_scheme'],b['counts'],b['blank_counts']):
            val=row_vector(scheme,p,nb,blanks[b['blank_type']],orders)
            for i in range(3):total[i]+=val[i]
        if len(b['orders'])>1:total[3]+=len(b['orders'])
    return total


def replace_vector(total,old,new):
    return [a-b+c for a,b,c in zip(total,old,new)]


def row_refine(plan,model,deadline):
    lookup={o.oid:o for o in model.orders}; ids={o.oid:i for i,o in enumerate(model.orders)}
    blanks={b.bid:b for b in model.blanks}; total=vector(plan,lookup,blanks)
    delivered=defaultdict(int)
    for b in plan:
        for s,p in zip(b['length_scheme'],b['counts']):
            for n,L in s.items():delivered[n]+=round(L/lookup[n].size)*p
    changes=visits=0
    for b in plan:
        blank=blanks[b['blank_type']]
        for j,(s,op) in enumerate(zip(b['length_scheme'],b['counts'])):
            if time.perf_counter()>=deadline:return dict(changes=changes,visits=visits)
            visits+=1;names=list(s);ii=tuple(ids[n] for n in names);oo=[lookup[n] for n in names]
            oldks=tuple(round(s[n]/o.size) for n,o in zip(names,oo))
            oldv=row_vector(s,op,b['blank_counts'][j],blank,lookup)
            other=[delivered[n]-k*op for n,k in zip(names,oldks)]
            best=None;bestv=total;bestscore=score(total)
            for p in range(model.parallel_limit(ii),0,-1):
                lo=[max(0,math.ceil((o.pieces-q)/p)) for o,q in zip(oo,other)]
                hi=[(model.caps[i]-q)//p for i,q in zip(ii,other)]
                if any(a>b for a,b in zip(lo,hi)):continue
                physicalcap=min(model.cfg.bed_length,model.cfg.bed_weight/(p*oo[0].linear_weight))-model.cfg.round_trim
                length=sum(k*o.size for k,o in zip(lo,oo))
                if length>physicalcap+1e-8:continue
                seeds=[lo,[max(l,min(h,math.ceil(k*op/p))) for l,h,k in zip(lo,hi,oldks)]]
                for seed in seeds:
                    ks=seed.copy();length=sum(k*o.size for k,o in zip(ks,oo))
                    for t in sorted(range(len(oo)),key=lambda t:-oo[t].size):
                        if length+model.cfg.round_trim>=model.cfg.min_bed_length-1e-8:break
                        add=min(hi[t]-ks[t],max(0,math.ceil((model.cfg.min_bed_length-model.cfg.round_trim-length-1e-8)/oo[t].size)))
                        ks[t]+=add;length+=add*oo[t].size
                    r=model.make_round(ii,tuple(ks),p,blank)
                    if r is None:continue
                    ns={n:round(k*o.size,9) for n,k,o in zip(names,ks,oo) if k}
                    trial=replace_vector(total,oldv,row_vector(ns,p,r.blanks,blank,lookup))
                    value=score(trial)
                    if value>bestscore+1e-10:best=(ns,p,r,ks);bestv=trial;bestscore=value
            if best:
                ns,p,r,ks=best
                for n,k,q in zip(names,ks,other):delivered[n]=q+k*p
                b['length_scheme'][j]=ns;b['counts'][j]=p;b['blank_counts'][j]=r.blanks
                total=bestv;changes+=1
    return dict(changes=changes,visits=visits)


def repack(plan,model,deadline,seed):
    lookup={o.oid:o for o in model.orders};ids={o.oid:i for i,o in enumerate(model.orders)}
    blanks={b.bid:b for b in model.blanks};total=vector(plan,lookup,blanks);rng=random.Random(seed)
    groups=[]
    for bi,b in enumerate(plan):
        byp=defaultdict(list)
        for j,p in enumerate(b['counts']):byp[p].append(j)
        groups.extend((bi,p,len(jj)) for p,jj in byp.items() if len(jj)>1)
    groups.sort(key=lambda v:-v[2]);attempts=changes=removed=0
    for cycle in range(3):
        anychange=False
        for bi,p,_ in groups:
            if time.perf_counter()>=deadline:return dict(attempts=attempts,changes=changes,removed_rounds=removed)
            b=plan[bi];jj=[j for j,x in enumerate(b['counts']) if x==p]
            if len(jj)<2:continue
            names=list(dict.fromkeys(n for j in jj for n in b['length_scheme'][j]));ii=tuple(ids[n] for n in names);oo=[lookup[n] for n in names]
            counts=[sum(round(b['length_scheme'][j].get(n,0)/o.size) for j in jj) for n,o in zip(names,oo)]
            sizes=[round(o.size*1000) for o in oo];linear=oo[0].linear_weight;blank=blanks[b['blank_type']]
            low=math.ceil((model.cfg.min_bed_length-model.cfg.round_trim)*1000-1e-7)
            cap=math.floor((min(model.cfg.bed_length,model.cfg.bed_weight/(p*linear))-model.cfg.round_trim)*1000+1e-7)
            capacities={cap}
            for nb in range(1,1+math.ceil(model.cfg.bed_weight/blank.weight)):
                C=math.floor((nb*blank.weight/(p*linear)-model.cfg.round_trim)*1000+1e-7)
                if low<=C<=cap:capacities.add(C)
            oldv=[0,0.,0.,0]
            for j in jj:
                val=row_vector(b['length_scheme'][j],p,b['blank_counts'][j],blank,lookup)
                for t in range(3):oldv[t]+=val[t]
            net=sum(k*s for k,s in zip(counts,sizes));items=[(sizes[t],t) for t,k in enumerate(counts) for _ in range(k)]
            best=None;bestv=total;bestscore=score(total)
            order_ranks=[list(range(len(names))),sorted(range(len(names)),key=lambda t:-counts[t]*sizes[t]),sorted(range(len(names)),key=lambda t:-sizes[t])]
            shuffled=list(range(len(names)));rng.shuffle(shuffled);order_ranks.append(shuffled)
            sequences=[[(sizes[t],t) for t in ordering for _ in range(counts[t])] for ordering in order_ranks]
            for C in sorted(capacities,reverse=True):
                if math.ceil(net/C)>len(jj):continue
                for sequence in sequences:
                    bins=[];lengths=[]
                    for size,t in sequence:
                        fitting=[j for j,L in enumerate(lengths) if L+size<=C]
                        if fitting:
                            # Keep pieces of an order together whenever feasible.
                            target=max(fitting,key=lambda j:(bins[j].get(t,0)>0,lengths[j]))
                        else:target=len(bins);bins.append(defaultdict(int));lengths.append(0)
                        bins[target][t]+=1;lengths[target]+=size
                    if len(bins)>len(jj):continue
                    feasible=True
                    for j in sorted(range(len(bins)),key=lambda j:lengths[j]):
                        while lengths[j]<low:
                            options=[(t not in bins[j],sizes[t],donor,t) for donor,bc in enumerate(bins) if donor!=j
                                     for t,k in bc.items() if k and lengths[donor]-sizes[t]>=low and lengths[j]+sizes[t]<=C]
                            if not options:feasible=False;break
                            enough=[x for x in options if lengths[j]+x[1]>=low]
                            _,size,donor,t=min(enough or options)
                            bins[donor][t]-=1;lengths[donor]-=size;bins[j][t]+=1;lengths[j]+=size
                        if not feasible:break
                    if not feasible:continue
                    rows=[];newv=[0,0.,0.,0]
                    for bc in bins:
                        ks=tuple(bc.get(t,0) for t in range(len(names)));r=model.make_round(ii,ks,p,blank)
                        if r is None:rows=[];break
                        ns={n:round(k*o.size,9) for n,k,o in zip(names,ks,oo) if k};rows.append((ns,r.blanks))
                        val=row_vector(ns,p,r.blanks,blank,lookup)
                        for t in range(3):newv[t]+=val[t]
                    attempts+=1
                    if not rows:continue
                    trial=replace_vector(total,oldv,newv);value=score(trial)
                    if value>bestscore+1e-10:best=rows;bestv=trial;bestscore=value
            if best:
                removed+=len(jj)-len(best)
                for key in ('length_scheme','counts','blank_counts'):
                    for j in reversed(jj):b[key].pop(j)
                for ns,nb in best:b['length_scheme'].append(ns);b['counts'].append(p);b['blank_counts'].append(nb)
                total=bestv;changes+=1;anychange=True
        if not anychange:break
    return dict(attempts=attempts,changes=changes,removed_rounds=removed)


def run(args):
    started=time.perf_counter();deadline=started+args.seconds;root=Path(args.output);root.mkdir(parents=True,exist_ok=True)
    config=json.loads(Path('runs/pattern_dp_pool_5pct/config.json').read_text());config['max_overproduction_ratio']=.05
    cfg=Config(**config);orders=load_orders('data/orders.normalized.csv',cfg);blanks=load_blanks('data/blanks.normalized.csv');model=Model(orders,cfg,blanks)
    lookup={o.oid:o for o in orders};blookup={b.bid:b for b in blanks}
    original=json.loads(Path(args.initial).read_text(encoding='utf-8-sig'));before=vector(original,lookup,blookup);plan=components(original)
    switches=choose_blanks(plan,model);plan=merge_compatible(plan,orders,cfg.max_rounds)
    if score(vector(plan,lookup,blookup))<score(before)-1e-10:plan=copy.deepcopy(original)
    history=[dict(stage='component_blank_choice',switches=switches,vector=vector(plan,lookup,blookup))]
    rows=row_refine(plan,model,min(deadline,time.perf_counter()+args.seconds*.28))
    history.append(dict(stage='row_refine',stats=rows,vector=vector(plan,lookup,blookup)))
    print(json.dumps(history[-1]),flush=True)
    packed=repack(plan,model,deadline-2,args.seed)
    history.append(dict(stage='same_parallel_repack',stats=packed,vector=vector(plan,lookup,blookup)))
    candidate=components(plan);switches2=choose_blanks(candidate,model);candidate=merge_compatible(candidate,orders,cfg.max_rounds)
    if score(vector(candidate,lookup,blookup))>=score(vector(plan,lookup,blookup))-1e-10:plan=candidate
    final=vector(plan,lookup,blookup);metrics=validate_plan(plan,orders,cfg,blanks);independent=check(plan)
    if not independent['passed']:raise ValueError(independent['error_counts'])
    if score(final)<score(before)-1e-10:raise ValueError('Calibrated score degraded')
    result=dict(before=before,after=final,before_score=score(before),predicted_score=score(final),history=history,
        physical_metrics=metrics,independent_check=independent,seconds=time.perf_counter()-started,
        official_acceptance_verified=False,knife_component_cap_is_hypothesis=True,
        forecast_formula='per entry int(L // size)+1; finished mass uses int(diameter_mm)')
    for name,data in [('result.json',plan),('config.json',config),('report.json',result),('data_audit.json',json.loads(Path('runs/pattern_dp_pool_5pct/data_audit.json').read_text(encoding='utf-8')))]:atomic_json(root/name,data)
    print(json.dumps({k:v for k,v in result.items() if k not in ('history','independent_check')}),flush=True)


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--initial',default='submission_optimized/初赛结果_棒材优化.json')
    ap.add_argument('--output',default='runs/calibrated_material');ap.add_argument('--seconds',type=float,default=100)
    ap.add_argument('--seed',type=int,default=260916);run(ap.parse_args())
