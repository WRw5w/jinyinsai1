"""Deterministic pattern DP + capacity-safe row merging + weighted-score sweep.

The estimated score uses the observed 40/30/20/10 weights. Local physical costs
are reported separately; they are not presented as official scores.
"""
import argparse
import json
import math
import subprocess
import time
import random
from dataclasses import replace
from collections import defaultdict
from pathlib import Path

from build_submission import merge_compatible
from competition_solver import atomic_json, Factory
from platform_check import check
from solver import Config, Model, Round, _ceil, _export, load_blanks, load_orders, validate_plan


def estimated_score(m):
    # 90000 is inferred from the observed 113683 knives / 79.17 subscore.
    return 40 * min(1, 90000 / m['knives']) + 30 * m['yield_rate'] + 20 * m['coverage'] + 10


class CalibratedModel(Model):
    """Keep original-diameter feasibility, price exported entries like feedback."""
    def price_round(self, ids, row):
        if row is None:return None
        cuts=0;finished=0.0
        for i,k in zip(ids,row.ks):
            if not k:continue
            o=self.orders[i];length=round(k*o.size,9)
            cuts+=int(length//o.size)+1
            mu=math.pi*(int(o.diameter)/1000)**2/4*o.density
            finished+=length*row.parallel*mu
        return replace(row,knives=cuts,finished=finished)

    def make_round(self, ids, ks, parallel, blank):
        return self.price_round(ids,super().make_round(ids,ks,parallel,blank))


def fuse_rows(plan, model):
    """Merge compatible parallel rows without splitting orders across schemes.

    Union-find tracks the connected schemes and enforces the 100-round limit
    on the eventual component, including every still-unmerged row of its orders.
    """
    lookup = {o.oid:i for i,o in enumerate(model.orders)}
    parent = list(range(len(model.orders)))
    nr = [0] * len(parent)
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    groups = defaultdict(list)
    for batch in plan:
        ids = [lookup[n] for n in batch['orders']]
        root = find(ids[0])
        for i in ids[1:]:
            parent[find(i)] = root
        nr[root] += len(batch['counts'])
        o = model.orders[ids[0]]
        for s,p,b in zip(batch['length_scheme'],batch['counts'],batch['blank_counts']):
            groups[o.steel,o.diameter,batch['blank_type'],p].append((dict(s),b))
    final = []
    for (steel,dia,bid,p), rows in groups.items():
        blank = next(b for b in model.blanks if b.bid==bid)
        linear = model.orders[lookup[next(iter(rows[0][0]))]].linear_weight
        cap = min(model.cfg.bed_length,model.cfg.bed_weight/(p*linear))-2*model.cfg.trim
        rows.sort(key=lambda r:sum(r[0].values()))
        while rows:
            lengths, old_blanks = rows.pop()
            length = sum(lengths.values())
            # Best-fit matching; each merge cannot increase cuts or blank use.
            for j in range(len(rows)-1,-1,-1):
                other, other_blanks = rows[j]
                if length + sum(other.values()) > cap + 1e-8:
                    continue
                a=find(lookup[next(iter(lengths))]); b=find(lookup[next(iter(other))])
                joined_rounds = nr[a]+nr[b]-1 if a!=b else nr[a]-1
                if joined_rounds>model.cfg.max_rounds:
                    continue
                merged = dict(lengths)
                for oid,L in other.items():merged[oid]=round(merged.get(oid,0)+L,9)
                ids=tuple(lookup[n] for n in merged)
                ks=tuple(round(L/model.orders[i].size) for i,L in zip(ids,merged.values()))
                trial=model.make_round(ids,ks,p,blank)
                if trial is None and sum(merged.values())+2*model.cfg.trim<model.cfg.min_bed_length:
                    # A provisional short row may be pooled further; it is
                    # never exported before satisfying the bed lower bound.
                    total=sum(merged.values())+2*model.cfg.trim
                    nb=_ceil(total*p/model.blank_length(ids,blank))
                    trial=Round(ks,p,nb,sum(ks)+1,sum(merged.values())*p*linear,nb*blank.weight)
                if trial is None or trial.blanks>old_blanks+other_blanks:
                    continue
                parent[b]=a;nr[a]=joined_rounds
                rows.pop(j);lengths=merged;length=sum(merged.values());old_blanks=trial.blanks
            final.append((bid,p,lengths,old_blanks))
    bad_roots={find(lookup[next(iter(s))]) for bid,p,s,b in final
               if sum(s.values())+2*model.cfg.trim<model.cfg.min_bed_length-1e-8}
    components={}
    for bid,p,lengths,b in final:
        root=find(lookup[next(iter(lengths))])
        if root in bad_roots:continue
        batch=components.setdefault(root,dict(orders=[],length_scheme=[],counts=[],blank_type=bid,blank_counts=[]))
        batch['length_scheme'].append(lengths);batch['counts'].append(p);batch['blank_counts'].append(b)
    fallback=[];factory=Factory();rng=random.Random(19);deadline=time.perf_counter()+60
    for i,o in enumerate(model.orders):
        if find(i) in bad_roots:
            batch=factory(model,(i,),rng,deadline)
            if batch is None:raise ValueError('Cannot repair unpoolable short row')
            fallback.extend(_export([batch],model.orders,model.cfg))
        else:
            components[find(i)]['orders'].append(o.oid)
    return merge_compatible(list(components.values())+fallback,model.orders,model.cfg.max_rounds)


def run(args):
    total_started=time.perf_counter()
    root=Path(args.output);root.mkdir(parents=True,exist_ok=True)
    cfg=Config(**json.loads(Path('data/competition.config.json').read_text()))
    cfg.max_overproduction_ratio=args.overproduction
    cfg.objective='platform_score';cfg.baseline_knives=90000
    orders=load_orders('data/orders.normalized.csv',cfg)
    blanks=load_blanks('data/blanks.normalized.csv')
    model=(CalibratedModel if args.calibrated else Model)(orders,cfg,blanks)
    specs=defaultdict(list)
    for i,o in enumerate(orders):specs[o.diameter,o.size,o.density].append(i)
    cases=[]
    for tag,ids in enumerate(specs.values()):
        first=ids[0];o=orders[first];maxq=max(model.caps[i] for i in ids)
        for blank in blanks:
            patterns=[]
            for k in range(1 if args.pool_short else max(1,math.ceil((cfg.min_bed_length-2*cfg.trim)/o.size-1e-9)),
                           math.floor((cfg.bed_length-2*cfg.trim)/o.size+1e-9)+1):
                pmax=min(model.parallel_limit((first,)),math.floor(cfg.bed_weight/((k*o.size+2*cfg.trim)*o.linear_weight)+1e-9))
                for p in range(1,pmax+1):
                    if k*p>maxq:continue
                    r=model.make_round((first,),(k,),p,blank)
                    if r is None and args.pool_short and k*o.size+2*cfg.trim<cfg.min_bed_length:
                        total=k*o.size+2*cfg.trim
                        if total>model.blank_length((first,),blank)+1e-8:continue
                        nb=_ceil(total*p/model.blank_length((first,),blank))
                        r=Round((k,),p,nb,k+1,k*p*o.size*o.linear_weight,nb*blank.weight)
                    if r:patterns.append(model.price_round((first,),r) if args.calibrated else r)
            cases.append((tag,blank.bid,ids,maxq,patterns))
    inp=root/'dp_input.txt'
    with inp.open('w') as f:
        f.write(f'{len(cases)}\n')
        for tag,bid,ids,maxq,patterns in cases:
            quantities=sorted({orders[i].pieces for i in ids})
            f.write(f'{tag} {bid} {maxq} {len(patterns)} {len(quantities)}\n')
            for r in patterns:
                cost_mass=r.raw-args.yield_credit*r.finished
                f.write(f'{r.ks[0]*r.parallel} {r.ks[0]} {r.parallel} {r.blanks} {r.knives} {cost_mass:.9f}\n')
            f.write(' '.join(f'{q} {q+math.floor(q*args.overproduction+1e-9)}' for q in quantities)+'\n')
    base=json.loads(Path(args.initial).read_text(encoding='utf-8'))
    baseline=validate_plan(base,orders,cfg,blanks)
    if args.calibrated:
        from platform_score import evaluate
        baseline=evaluate(base)
    config=json.loads(Path('data/competition.config.json').read_text())
    config['max_overproduction_ratio']=args.overproduction
    config.update(objective='platform_score',baseline_knives=90000)
    atomic_json(root/'config.json',config)
    source_audit=json.loads(Path('data/data_audit.json').read_text(encoding='utf-8'))
    source_audit['assumptions']=[x for x in source_audit['assumptions']
                               if 'exact rounded-up' not in x and 'lexicographic objective' not in x]
    source_audit['assumptions'].append('Candidate selection uses estimated 40/30/20/10 score; 90000 knife baseline and time full score are inferred, not official evaluator equivalence')
    source_audit['assumptions'].append(f'Extra pieces <= floor(demand pieces * {args.overproduction}) per order; not an officially confirmed upper bound')
    if args.calibrated:
        source_audit['assumptions'].append('Knife and scoring mass formulas calibrated to all three official feedback samples; original diameter retained for conservative physical checks')
    atomic_json(root/'data_audit.json',source_audit)
    best_plan=base;best_metrics=baseline;history=[]
    started=time.perf_counter()
    for lam in args.lambdas:
        output=root/f'dp_{lam}.txt'
        subprocess.run([str(Path(args.engine).resolve()),str(inp.resolve()),str(output.resolve()),str(lam)],check=True)
        candidates={i:[] for i in range(len(orders))}
        with output.open() as f:
            if int(f.readline())!=len(cases):raise ValueError('DP case count mismatch')
            for tag,bid,ids,maxq,patterns in cases:
                out_tag,out_bid,nq=map(int,f.readline().split())
                if (out_tag,out_bid)!=(tag,bid):raise ValueError('DP identity mismatch')
                by_q={}
                for _ in range(nq):
                    values=list(map(int,f.readline().split()));q,nr=values[:2]
                    if nr<0:continue
                    rows=[patterns[j] for j in values[2:]]
                    produced=sum(r.ks[0]*r.parallel for r in rows)
                    if len(rows)!=nr or not q<=produced<=q+math.floor(q*args.overproduction+1e-9):raise ValueError('DP reconstruction invalid')
                    if nr>cfg.max_rounds:continue
                    by_q[q]=rows
                for i in ids:
                    rows=by_q.get(orders[i].pieces)
                    if rows:
                        value=sum(r.knives+lam*(r.raw-args.yield_credit*r.finished) for r in rows)
                        candidates[i].append((value,bid,rows))
        plan=[]
        for i,o in enumerate(orders):
            if not candidates[i]:raise ValueError(f'No exact DP plan for {o.oid}')
            _,bid,rows=min(candidates[i],key=lambda v:v[0])
            plan.append(dict(orders=[o.oid],length_scheme=[{o.oid:round(r.ks[0]*o.size,9)} for r in rows],
                             counts=[r.parallel for r in rows],blank_type=bid,blank_counts=[r.blanks for r in rows]))
        if not args.pool_short:
            validate_plan(plan,orders,cfg,blanks)
        plan=fuse_rows(plan,model)
        metrics=validate_plan(plan,orders,cfg,blanks)
        if args.calibrated:metrics=evaluate(plan)
        independent=check(plan)
        if not independent['passed']:raise ValueError(independent['error_counts'])
        metrics.update(estimated_score=estimated_score(metrics),lambda_kg=lam,elapsed_seconds=time.perf_counter()-started)
        metrics['overproduction_limit']=args.overproduction
        metrics['pooled_short_patterns']=args.pool_short
        metrics['yield_credit']=args.yield_credit
        if 'finished_weight' in metrics and 'finished_weight' in baseline:
            metrics['additional_finished_kg']=metrics['finished_weight']-baseline['finished_weight']
        metrics['feedback_calibrated']=args.calibrated
        atomic_json(root/f'candidate_{lam}.json',plan)
        atomic_json(root/f'candidate_{lam}.metrics.json',metrics)
        history.append(metrics);print(json.dumps(metrics),flush=True)
        if estimated_score(metrics)>estimated_score(best_metrics):
            best_metrics,best_plan=metrics,plan
            atomic_json(root/'result.json',plan)
            atomic_json(root/'result.metrics.json',metrics)
    atomic_json(root/'result.json',best_plan)
    atomic_json(root/'result.metrics.json',best_metrics)
    atomic_json(root/'report.json',dict(baseline=baseline,best=best_metrics,candidates=history,
                                     total_elapsed_seconds=time.perf_counter()-total_started,
                                     elapsed_seconds_scope='candidate elapsed_seconds excludes initial pattern enumeration; total_elapsed_seconds includes it',
                                     official_acceptance_verified=False,estimated_score_only=True))


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--initial',default='submission_fixed/初赛结果_棒材优化.json')
    ap.add_argument('--output',default='runs/pattern_dp_v1')
    ap.add_argument('--engine',default='pattern_dp.exe')
    ap.add_argument('--lambdas',type=float,nargs='+',default=[0.0001,0.0003,0.0006,0.001])
    ap.add_argument('--overproduction',type=float,default=0)
    ap.add_argument('--pool-short',action='store_true')
    ap.add_argument('--yield-credit',type=float,default=0,
                    help='Finished mass credit in raw-material scalar cost; ~1/yield targets yield ratio')
    ap.add_argument('--calibrated',action='store_true',help='Use per-entry floor division and integer-diameter score calibrated to platform feedback')
    run(ap.parse_args())
