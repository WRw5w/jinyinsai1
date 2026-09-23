"""Cross-scheme physical repacking with order-component and delivery bounds.

Search preserves the original valid orders, <=5% surplus, all physical
constraints, one blank type per order, and <=100 rounds per final scheme.
"""
import argparse
import json
import math
import random
import time
import itertools
from collections import defaultdict
from pathlib import Path

from build_submission import merge_compatible
from competition_solver import atomic_json
from platform_check import check
from refine_patterns import refine, score, vector
from solver import Config, Model, load_blanks, load_orders, validate_plan


class Components:
    def __init__(self, size):
        self.parent=list(range(size));self.rounds=[0]*size
    def find(self,i):
        while self.parent[i]!=i:
            self.parent[i]=self.parent[self.parent[i]];i=self.parent[i]
        return i
    def join(self, ids):
        roots={self.find(i) for i in ids};root=min(roots)
        nr=sum(self.rounds[r] for r in roots)
        for r in roots:self.parent[r]=root
        self.rounds[root]=nr
        return root


def exact_two_split(counts, sizes, p, linear, blank_weight, low, cap, trim):
    """Exact bounded subset sum in mm, minimizing the two billet counts.

    Source lengths have at most three decimal places in metres. Integer
    conversion is checked, so this function never silently rounds input sizes.
    """
    units=[round(s*1000) for s in sizes]
    if any(abs(u/1000-s)>1e-8 for u,s in zip(units,sizes)):return None
    total=sum(k*s for k,s in zip(counts,units))
    lower=max(math.ceil(low*1000-1e-7),total-math.floor(cap*1000+1e-7))
    upper=min(math.floor(cap*1000+1e-7),total-math.ceil(low*1000-1e-7))
    if lower>upper:return None
    bits=1;history=[];chunks=[];mask=(1<<(upper+1))-1
    for i,(n,u) in enumerate(zip(counts,units)):
        step=1
        while n:
            take=min(step,n);n-=take;step*=2
            shift=take*u;history.append(bits);chunks.append((i,take,shift))
            bits=(bits|(bits<<shift))&mask
    max_blanks=math.ceil((cap+trim)*p*linear/blank_weight-1e-10)
    min_total=math.ceil((total/1000+2*trim)*p*linear/blank_weight-1e-10)
    for nb in range(max(2,min_total),2*max_blanks+1):
        for n1 in range(1,max_blanks+1):
            n2=nb-n1
            if not 1<=n2<=max_blanks:continue
            hi=min(upper,math.floor((n1*blank_weight/(p*linear)-trim)*1000+1e-7))
            lo=max(lower,total-math.floor((n2*blank_weight/(p*linear)-trim)*1000+1e-7))
            if lo>hi:continue
            valid=(bits>>lo)&((1<<(hi-lo+1))-1)
            if not valid:continue
            target=lo+valid.bit_length()-1;result=[0]*len(counts)
            for previous,(i,take,shift) in zip(reversed(history),reversed(chunks)):
                if (previous>>target)&1:continue
                target-=shift;result[i]+=take
            if target:raise ValueError('Subset-sum reconstruction failed')
            return result
    return None


def bounded_subset(counts, units, low, high):
    if low>high or high<0:return None
    low=max(0,low);bits=1;history=[];chunks=[];mask=(1<<(high+1))-1
    for i,(n,u) in enumerate(zip(counts,units)):
        step=1
        while n:
            take=min(step,n);n-=take;step*=2
            shift=take*u;history.append(bits);chunks.append((i,take,shift))
            bits=(bits|(bits<<shift))&mask
    valid=bits>>low
    if not valid:return None
    target=low+valid.bit_length()-1;out=[0]*len(counts)
    for previous,(i,take,shift) in zip(reversed(history),reversed(chunks)):
        if (previous>>target)&1:continue
        target-=shift;out[i]+=take
    if target:raise ValueError('Subset-sum reconstruction failed')
    return out


def billet_partitions(total, slots, low, high, prefix=()):
    if slots==0:
        if total==0:yield prefix
        return
    for take in range(min(high,total-(slots-1)*low),low-1,-1):
        if total-take>(slots-1)*take:continue
        yield from billet_partitions(total-take,slots-1,low,take,prefix+(take,))


def multi_split(counts,sizes,p,linear,blank_weight,low,cap,trim,nr):
    """Try billet-count partitions, packing each round with exact integer DP."""
    units=[round(s*1000) for s in sizes]
    if any(abs(u/1000-s)>1e-8 for u,s in zip(units,sizes)):return None
    L=sum(n*u for n,u in zip(counts,units));lo=math.ceil(low*1000-1e-7)
    maxnb=math.ceil((cap+trim)*p*linear/blank_weight-1e-10)
    minnb=math.ceil((low+trim)*p*linear/blank_weight-1e-10)
    totalnb=max(nr*minnb,math.ceil((L/1000+nr*trim)*p*linear/blank_weight-1e-10))
    limit=math.floor(cap*1000+1e-7)
    for budget in range(totalnb,min(nr*maxnb,totalnb+3)+1):
        tried=0
        base,extra=divmod(budget,nr)
        balanced=(base+1,)*extra+(base,)*(nr-extra)
        partitions=itertools.chain([balanced] if minnb<=base and base+(extra>0)<=maxnb else [],billet_partitions(budget,nr,minnb,maxnb))
        considered=0
        for partition in partitions:
            considered+=1
            if considered>100:break
            caps=[min(limit,math.floor((nb*blank_weight/(p*linear)-trim)*1000+1e-7)) for nb in partition]
            if sum(caps)<L:continue
            for reverse in [False,True]:
                tried+=1
                if tried>20:return None
                capacities=list(reversed(caps)) if reverse else caps
                rem=counts[:];remaining=L;result=[]
                for slot,capa in enumerate(capacities):
                    slots=nr-slot
                    if slots==1:
                        if lo<=remaining<=capa:result.append(rem)
                        break
                    lower=max(lo,remaining-sum(capacities[slot+1:]))
                    upper=min(capa,remaining-(slots-1)*lo)
                    take=bounded_subset(rem,units,lower,upper)
                    if take is None:break
                    result.append(take);rem=[v-k for v,k in zip(rem,take)]
                    remaining-=sum(k*u for k,u in zip(take,units))
                if len(result)==nr:return result
    return None


def materialize(rows,model):
    uf=Components(len(model.orders));parts={}
    for r in rows:
        if r is not None:uf.join(r['ks'])
    for r in rows:
        if r is None:continue
        root=uf.find(next(iter(r['ks'])))
        b=parts.setdefault(root,dict(orders=[],length_scheme=[],counts=[],blank_type=r['bid'],blank_counts=[]))
        if b['blank_type']!=r['bid']:raise ValueError('Mixed blank types in order component')
        b['length_scheme'].append({model.orders[i].oid:round(k*model.orders[i].size,9) for i,k in r['ks'].items() if k})
        b['counts'].append(r['p']);b['blank_counts'].append(r['r'].blanks)
    for i,o in enumerate(model.orders):parts[uf.find(i)]['orders'].append(o.oid)
    return merge_compatible(list(parts.values()),model.orders,model.cfg.max_rounds)


def run(args):
    start=time.perf_counter();root=Path(args.output);root.mkdir(parents=True,exist_ok=True)
    source=Path(args.initial);config=json.loads((source.parent/'config.json').read_text())
    cfg=Config(**config);orders=load_orders('data/orders.normalized.csv',cfg)
    if cfg.max_overproduction_ratio>0.05 or cfg.max_rounds>100 or cfg.length_mode!='net_shared_trim':
        raise ValueError('Normal branch requires <=5% surplus, <=100 rounds, and net integer cutting lengths')
    model=Model(orders,cfg,load_blanks('data/blanks.normalized.csv'))
    plan=json.loads(source.read_text());baseline=validate_plan(plan,orders,cfg,model.blanks)
    lookup={o.oid:i for i,o in enumerate(orders)};blanks={b.bid:b for b in model.blanks}
    rows=[];groups=defaultdict(list);uf=Components(len(orders));delivered=[0]*len(orders)
    for b in plan:
        for s,p in zip(b['length_scheme'],b['counts']):
            ks={lookup[n]:round(L/orders[lookup[n]].size) for n,L in s.items()}
            ids=tuple(ks);r=model.make_round(ids,tuple(ks.values()),p,blanks[b['blank_type']])
            rows.append(dict(ks=ks,p=p,bid=b['blank_type'],r=r))
            o=orders[ids[0]];groups[o.steel,o.diameter,b['blank_type']].append(len(rows)-1)
            uf.join(ids)
            for i,k in ks.items():delivered[i]+=k*p
    component_rows=defaultdict(set)
    for j,r in enumerate(rows):
        comp=uf.find(next(iter(r['ks'])))
        uf.rounds[comp]+=1;component_rows[comp].add(j)
    total=vector(plan,model);initial_components=len({uf.find(i) for i in range(len(orders))})
    rng=random.Random(args.seed);deadline=time.perf_counter()+args.seconds
    population=[g for g,rr in groups.items() for _ in rr]
    accepted=attempts=component_rejections=0;progress=[]
    while time.perf_counter()<deadline:
        key=rng.choice(population);pool=groups[key]
        if len(pool)<args.source_rounds:continue
        seed_row=rng.choice(pool);comp=uf.find(next(iter(rows[seed_row]['ks'])))
        local=component_rows[comp]
        if len(local)>=args.source_rounds and rng.random()<0.75:
            equal=[j for j in sorted(local) if rows[j]['p']==rows[seed_row]['p']]
            target=equal if args.same_parallel and len(equal)>=args.source_rounds else sorted(local)
            selected=rng.sample(target,args.source_rounds)
        else:selected=rng.sample(pool,args.source_rounds)
        old=[rows[j] for j in selected]
        if any(r is None for r in old):continue
        attempts+=1
        ids=tuple(dict.fromkeys(i for r in old for i in r['ks']));oo=[orders[i] for i in ids]
        roots={uf.find(i) for i in ids};component_n=sum(uf.rounds[r] for r in roots)
        if component_n-args.source_rounds+1>cfg.max_rounds:
            component_rejections+=1;continue
        other=[delivered[i]-sum(r['ks'].get(i,0)*r['p'] for r in old) for i in ids]
        need=[max(0,o.pieces-q) for o,q in zip(oo,other)]
        oldk=sum(r['r'].knives for r in old);oldf=sum(r['r'].finished for r in old);oldraw=sum(r['r'].raw for r in old)
        blank=blanks[key[2]];linear=oo[0].linear_weight
        upper=min(model.parallel_limit(ids),int(cfg.bed_weight/(cfg.min_bed_length*linear)))
        sizes=[o.size for o in oo];orderings=[sorted(range(len(ids)),key=lambda t:-sizes[t])]
        if len(ids)>1:orderings.append(orderings[0][::-1])
        best=None;best_total=total;best_score=score(total)
        # The entire group is available, but priority remains the largest
        # physically possible bundle counts to lower knife count.
        for p in range(upper,0,-1):
            lo=[math.ceil(q/p) for q in need]
            hi=[(model.caps[i]-q)//p for i,q in zip(ids,other)]
            if any(x>y for x,y in zip(lo,hi)):continue
            length=sum(k*s for k,s in zip(lo,sizes))
            cap=min(cfg.bed_length,cfg.bed_weight/(p*linear))-cfg.round_trim
            low=cfg.min_bed_length-cfg.round_trim
            if length>args.source_rounds*cap+1e-8:continue
            variants=[]
            if length<=cap+1e-8:
                ks=lo.copy();L=length
                for t in orderings[0]:
                    extra=min(hi[t]-ks[t],max(0,math.ceil((low-L-1e-8)/sizes[t])))
                    ks[t]+=extra;L+=extra*sizes[t]
                r=model.make_round(ids,ks,p,blank)
                if r:variants.append([r])
            if 2*low-1e-8<=length<=2*cap+1e-8:
                exact_left=exact_two_split(lo,sizes,p,linear,blank.weight,low,cap,cfg.round_trim)
                if exact_left is not None:
                    exact_right=[v-k for v,k in zip(lo,exact_left)]
                    r1=model.make_round(ids,exact_left,p,blank);r2=model.make_round(ids,exact_right,p,blank)
                    if r1 and r2:variants.append([r1,r2])
                # A cut just below a blank boundary can save a whole billet;
                # balancing two rounds blindly often misses these positions.
                targets={min(cap,length-low),max(low,length-cap),length/2}
                for nb in range(1,math.floor(cfg.bed_weight/blank.weight)+1):
                    target=nb*blank.weight/(p*linear)-cfg.round_trim
                    if low-1e-8<=target<=cap+1e-8:
                        targets.add(target);targets.add(length-target)
                for target in targets:
                    if not max(low,length-cap)-1e-8<=target<=min(cap,length-low)+1e-8:continue
                    for ordering in orderings:
                        left=[0]*len(ids);L=0
                        for t in ordering:
                            left[t]=min(lo[t],max(0,math.floor((target-L+1e-8)/sizes[t])))
                            L+=left[t]*sizes[t]
                        right=[v-k for v,k in zip(lo,left)]
                        r1=model.make_round(ids,left,p,blank);r2=model.make_round(ids,right,p,blank)
                        if r1 and r2:variants.append([r1,r2])
            if args.source_rounds>=3:
                for nr in range(3,args.source_rounds+1):
                    if not nr*low-1e-8<=length<=nr*cap+1e-8:continue
                    optimistic_raw=math.ceil((length+nr*cfg.round_trim)*p*linear/blank.weight-1e-10)*blank.weight
                    optimistic=[total[0]-oldk+sum(lo)+nr,total[1]-oldf+length*p*linear,total[2]-oldraw+optimistic_raw,total[3]]
                    if score(optimistic)<=best_score+1e-10:continue
                    packed=multi_split(lo,sizes,p,linear,blank.weight,low,cap,cfg.round_trim,nr)
                    if packed is not None:
                        exact_rows=[model.make_round(ids,ks,p,blank) for ks in packed]
                        if all(exact_rows):variants.append(exact_rows)
                    for ordering in orderings:
                        for balanced in [False,True]:
                            rem=lo.copy();remL=length;proposed=[]
                            for slot in range(nr):
                                slots=nr-slot;target=remL/slots if balanced else min(cap,remL-(slots-1)*low)
                                take=[0]*len(ids);L=0
                                for t in ordering:
                                    take[t]=rem[t] if slots==1 else min(rem[t],max(0,math.floor((target-L+1e-8)/sizes[t])))
                                    L+=take[t]*sizes[t]
                                r=model.make_round(ids,take,p,blank)
                                if r is None:break
                                proposed.append(r);rem=[v-k for v,k in zip(rem,take)];remL-=L
                            if len(proposed)==nr and not any(rem):variants.append(proposed)
            for proposed in variants:
                if component_n-args.source_rounds+len(proposed)>cfg.max_rounds:continue
                trial=[total[0]-oldk+sum(r.knives for r in proposed),total[1]-oldf+sum(r.finished for r in proposed),total[2]-oldraw+sum(r.raw for r in proposed),total[3]]
                value=score(trial)
                if value>best_score+1e-10:best=(p,proposed);best_total=trial;best_score=value
        if best is None:continue
        p,proposed=best
        for t,i in enumerate(ids):delivered[i]=other[t]+sum(r.ks[t]*p for r in proposed)
        joined_rows=set().union(*(component_rows.pop(r) for r in roots))
        comp=uf.join(ids);uf.rounds[comp]-=args.source_rounds-len(proposed)
        for j,r in zip(selected,proposed):rows[j]=dict(ks={i:k for i,k in zip(ids,r.ks) if k},p=p,bid=blank.bid,r=r)
        for j in selected[len(proposed):]:rows[j]=None;pool.remove(j);joined_rows.remove(j)
        component_rows[comp]=joined_rows
        total=best_total;accepted+=1
        if accepted%100==0:
            progress.append(dict(attempts=attempts,accepted=accepted,estimated_score=score(total),totals=total,seconds=time.perf_counter()-start))
            atomic_json(root/'checkpoint.json',materialize(rows,model))
            print(json.dumps(progress[-1]),flush=True)
    plan=materialize(rows,model)
    exact=vector(plan,model)
    if any(not math.isclose(a,b,rel_tol=1e-9,abs_tol=1e-5) for a,b in zip(total[:3],exact[:3])):raise ValueError('Incremental metric drift')
    plan,refinement=refine(plan,model,4)
    checked=check(plan)
    if not checked['passed']:raise ValueError(checked['error_counts'])
    metrics=validate_plan(plan,orders,cfg,model.blanks)
    atomic_json(root/'result.json',plan);atomic_json(root/'config.json',config)
    atomic_json(root/'data_audit.json',json.loads((source.parent/'data_audit.json').read_text(encoding='utf-8')))
    atomic_json(root/'report.json',dict(before=baseline,after=metrics,attempts=attempts,accepted=accepted,initial_components=initial_components,component_rejections=component_rejections,seed=args.seed,seconds=time.perf_counter()-start,history=progress,post_refinement=refinement,independent_check=checked,official_acceptance_verified=False))
    print(json.dumps(metrics),flush=True)


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--initial',default='runs/continued_triples/result.json')
    ap.add_argument('--output',default='runs/normal_group_search')
    ap.add_argument('--seconds',type=float,default=180)
    ap.add_argument('--seed',type=int,default=92016)
    ap.add_argument('--source-rounds',type=int,default=2,choices=range(2,65))
    ap.add_argument('--same-parallel',action='store_true',help='Prefer a common existing bundle size inside components')
    run(ap.parse_args())
