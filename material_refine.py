"""Legal component-wise billet choice and bounded row capacity refinement.

The capped and uncapped knife scores are competing local hypotheses, not the
official evaluator. Every exported candidate is checked by both validators.
"""
import argparse
import copy
import itertools
import json
import math
import random
import time
from collections import defaultdict
from pathlib import Path

from build_submission import merge_compatible
from competition_solver import atomic_json
from platform_check import check
from solver import Config, Model, load_orders, load_blanks, validate_plan


def score(v, capped=True):
    k, f, r, c = v
    knife = min(1, 90000 / k) if capped else 90000 / k
    return 40 * knife + 30 * f / r + 20 * c / 4999 + 10


def components(plan):
    """Split only along disconnected order/round components."""
    result = []
    for b in plan:
        parent = {n: n for n in b['orders']}
        def find(n):
            while parent[n] != n:
                parent[n] = parent[parent[n]]
                n = parent[n]
            return n
        for s in b['length_scheme']:
            ns = list(s)
            for n in ns[1:]:
                parent[find(n)] = find(ns[0])
        groups = {}
        for n in b['orders']:
            root = find(n)
            if root not in groups:
                groups[root] = dict(orders=[], counts=[], length_scheme=[],
                                    blank_type=b['blank_type'], blank_counts=[])
            groups[root]['orders'].append(n)
        for s, p, nb in zip(b['length_scheme'], b['counts'], b['blank_counts']):
            target = groups[find(next(iter(s)))]
            target['length_scheme'].append(dict(s))
            target['counts'].append(p)
            target['blank_counts'].append(nb)
        result.extend(groups.values())
    return result


def totals(plan, model):
    m = validate_plan(plan, model.orders, model.cfg, model.blanks)
    return [m['knives'], m['finished_weight'], m['blank_weight'],
            round(m['coverage'] * len(model.orders))]


def choose_blanks(plan, model):
    ids = {o.oid: i for i, o in enumerate(model.orders)}
    blanks = {b.bid: b for b in model.blanks}
    changes = 0
    for batch in plan:
        best = sum(batch['blank_counts']) * blanks[batch['blank_type']].weight
        for blank in model.blanks:
            if blank.bid == batch['blank_type']:
                continue
            rows = []
            for s, p in zip(batch['length_scheme'], batch['counts']):
                ii = tuple(ids[n] for n in s)
                ks = tuple(round(L / model.orders[i].size) for L, i in zip(s.values(), ii))
                rows.append(model.make_round(ii, ks, p, blank))
            if any(r is None for r in rows):
                continue
            raw = sum(r.raw for r in rows)
            if raw < best - 1e-8:
                batch['blank_type'] = blank.bid
                batch['blank_counts'] = [r.blanks for r in rows]
                best = raw
                changes += 1
    return changes


def refine_rows(plan, model, capped=True, seconds=30):
    ids = {o.oid: i for i, o in enumerate(model.orders)}
    delivered = defaultdict(int)
    for b in plan:
        for s, p in zip(b['length_scheme'], b['counts']):
            for n, L in s.items():
                delivered[n] += round(L / model.orders[ids[n]].size) * p
    total = totals(merge_compatible(plan, model.orders, model.cfg.max_rounds), model)
    cfg = model.cfg
    deadline = time.perf_counter() + seconds
    changed = visits = 0
    for batch in plan:
        blank = next(b for b in model.blanks if b.bid == batch['blank_type'])
        for row, (s, op) in enumerate(zip(batch['length_scheme'], batch['counts'])):
            if time.perf_counter() >= deadline:
                return changed, visits
            visits += 1
            names = list(s)
            ii = tuple(ids[n] for n in names)
            oo = [model.orders[i] for i in ii]
            oldks = tuple(round(s[n] / o.size) for n, o in zip(names, oo))
            old = model.make_round(ii, oldks, op, blank)
            other = [delivered[n] - k * op for n, k in zip(names, oldks)]
            best, best_v = old, total
            for p in range(model.parallel_limit(ii), 0, -1):
                lo = [max(0, math.ceil((o.pieces - q) / p)) for o, q in zip(oo, other)]
                hi = [(model.caps[i] - q) // p for i, q in zip(ii, other)]
                if any(a > b for a, b in zip(lo, hi)):
                    continue
                cap = min(cfg.bed_length, cfg.bed_weight / (p * oo[0].linear_weight)) - cfg.round_trim
                length = sum(k * o.size for k, o in zip(lo, oo))
                if length > cap + 1e-8:
                    continue
                hi = [min(h, k + max(0, math.floor((cap - length + 1e-8) / o.size)))
                      for h, k, o in zip(hi, lo, oo)]
                candidates = set()
                # Exhaustive local enumeration when small; otherwise enumerate
                # monotone completions in contrasting size orders.
                count = math.prod(h - l + 1 for l, h in zip(lo, hi))
                if count <= 500:
                    candidates.update(itertools.product(*(range(l, h + 1) for l, h in zip(lo, hi))))
                else:
                    for reverse in [True, False]:
                        ks = lo.copy()
                        L = length
                        candidates.add(tuple(ks))
                        for j in sorted(range(len(oo)), key=lambda j: oo[j].size, reverse=reverse):
                            for _ in range(hi[j] - ks[j]):
                                if L + oo[j].size > cap + 1e-8:
                                    break
                                ks[j] += 1
                                L += oo[j].size
                                candidates.add(tuple(ks))
                for ks in candidates:
                    r = model.make_round(ii, ks, p, blank)
                    if r is None:
                        continue
                    candidate = [total[0] - old.knives + r.knives,
                                 total[1] - old.finished + r.finished,
                                 total[2] - old.raw + r.raw, total[3]]
                    if score(candidate, capped) > score(best_v, capped) + 1e-11:
                        best, best_v = r, candidate
            if best is not old:
                for n, k, q in zip(names, best.ks, other):
                    delivered[n] = q + k * best.parallel
                batch['counts'][row] = best.parallel
                batch['blank_counts'][row] = best.blanks
                batch['length_scheme'][row] = {n: round(k * o.size, 9)
                                              for n, k, o in zip(names, best.ks, oo) if k}
                total = best_v
                changed += 1
    return changed, visits


def subset_between(counts, sizes_mm, lower, upper):
    """Recover a bounded integer subset in a length interval using bitsets."""
    if lower > upper:
        return None
    bits = 1
    history = []
    mask = (1 << (upper + 1)) - 1
    for count, length in zip(counts, sizes_mm):
        history.append(bits)
        out = bits
        for k in range(1, min(count, upper // length) + 1):
            out |= bits << (k * length)
        bits = out & mask
    allowed = bits >> lower
    if not allowed:
        return None
    target = lower + (allowed & -allowed).bit_length() - 1
    recovered = [0] * len(counts)
    for j in range(len(counts)-1, -1, -1):
        length = sizes_mm[j]
        for k in range(min(counts[j], target // length)+1):
            if (history[j] >> (target-k*length)) & 1:
                recovered[j] = k
                target -= k*length
                break
        else:
            raise ValueError('Subset reconstruction failed')
    assert target == 0
    return recovered


def redistribute_pairs(plan, model, seconds, seed=20260916):
    """Move existing production between equal-parallel rows at billet edges.

    Production, knife count, number of rounds and each order's scheme are
    unchanged. This is an exact feasibility search for each chosen row pair.
    """
    ids = {o.oid: i for i, o in enumerate(model.orders)}
    deadline = time.perf_counter()+seconds
    rng = random.Random(seed)
    attempts = changes = 0
    while time.perf_counter() < deadline:
        options = []
        for bi, b in enumerate(plan):
            by_p = defaultdict(list)
            blank = next(v for v in model.blanks if v.bid == b['blank_type'])
            linear = model.orders[ids[b['orders'][0]]].linear_weight
            for j, (s, p, nb) in enumerate(zip(b['length_scheme'], b['counts'], b['blank_counts'])):
                slack = nb*blank.weight-(sum(s.values())+model.cfg.round_trim)*p*linear
                by_p[p].append((slack, j))
            for p, rows in by_p.items():
                rows.sort(reverse=True)
                for a in range(len(rows)):
                    for z in range(a+1, len(rows)):
                        if rows[a][0]+rows[z][0] < blank.weight-1e-6:
                            break
                        options.append((rng.random(), bi, rows[a][1], rows[z][1]))
        if not options:
            break
        options.sort()
        changed_this = 0
        for _, bi, a, z in options:
            if time.perf_counter() >= deadline:
                break
            b = plan[bi]
            blank = next(v for v in model.blanks if v.bid == b['blank_type'])
            p = b['counts'][a]
            left, right = b['length_scheme'][a], b['length_scheme'][z]
            names = list(dict.fromkeys(list(left)+list(right)))
            ii = tuple(ids[n] for n in names)
            oo = [model.orders[i] for i in ii]
            ks = [round(left.get(n,0)/o.size)+round(right.get(n,0)/o.size)
                  for n,o in zip(names,oo)]
            sizes = [round(o.size*1000) for o in oo]
            # Integer millimetres are exact for these source lengths.
            if any(abs(s/1000-o.size)>1e-10 for s,o in zip(sizes,oo)):
                continue
            L = sum(k*s for k,s in zip(ks,sizes))
            linear = oo[0].linear_weight
            physical = (L/1000+2*model.cfg.round_trim)*p*linear
            old = b['blank_counts'][a]+b['blank_counts'][z]
            if physical > (old-1)*blank.weight+1e-8:
                continue
            attempts += 1
            cap = min(model.cfg.bed_length, model.cfg.bed_weight/(p*linear))-model.cfg.round_trim
            minL = math.ceil((model.cfg.min_bed_length-model.cfg.round_trim)*1000-1e-7)
            maxL = math.floor(cap*1000+1e-7)
            best = None
            for total_blank in range(max(2,math.ceil(physical/blank.weight-1e-10)), old):
                for nb1 in range(1,total_blank):
                    nb2 = total_blank-nb1
                    cap1 = min(maxL,math.floor((nb1*blank.weight/(p*linear)-model.cfg.round_trim)*1000+1e-7))
                    cap2 = min(maxL,math.floor((nb2*blank.weight/(p*linear)-model.cfg.round_trim)*1000+1e-7))
                    low = max(minL,L-cap2)
                    high = min(cap1,L-minL)
                    if low>high:
                        continue
                    first = subset_between(ks,sizes,low,high)
                    if first is None:
                        continue
                    second = [k-v for k,v in zip(ks,first)]
                    r1 = model.make_round(ii,first,p,blank)
                    r2 = model.make_round(ii,second,p,blank)
                    if r1 is not None and r2 is not None and r1.blanks+r2.blanks < old:
                        best = r1,r2
                        break
                if best:
                    break
            if best:
                for j,r in zip([a,z],best):
                    b['length_scheme'][j] = {n:round(k*o.size,9)
                                            for n,k,o in zip(names,r.ks,oo) if k}
                    b['blank_counts'][j] = r.blanks
                changes += 1
                changed_this += 1
        if not changed_this:
            break
    return dict(attempts=attempts,changes=changes)


def repack_parallel(plan, model, seconds=60, seed=20260916, capped=True):
    """Repack all same-parallel production inside one existing scheme.

    Each piece occurrence retains its order, parallel count and scheme. Target
    capacities include whole-billet boundaries as well as bed constraints.
    """
    ids = {o.oid:i for i,o in enumerate(model.orders)}
    rng = random.Random(seed)
    deadline = time.perf_counter()+seconds
    total = totals(plan,model)
    attempted = changed = removed = 0
    groups = []
    for bi,b in enumerate(plan):
        by_p = defaultdict(list)
        for j,p in enumerate(b['counts']):
            by_p[p].append(j)
        for p,jj in by_p.items():
            if len(jj)>=2:
                groups.append((bi,p,jj))
    # Prioritize largest independent subproblems; repeat randomized orders.
    groups.sort(key=lambda g:-len(g[2]))
    while time.perf_counter()<deadline:
        any_change = False
        for bi,p,_ in groups:
            if time.perf_counter()>=deadline:
                break
            b = plan[bi]
            jj = [j for j,x in enumerate(b['counts']) if x==p]
            if len(jj)<2:
                continue
            blank = next(x for x in model.blanks if x.bid==b['blank_type'])
            names = list(dict.fromkeys(n for j in jj for n in b['length_scheme'][j]))
            ii = tuple(ids[n] for n in names)
            oo = [model.orders[i] for i in ii]
            count = [sum(round(b['length_scheme'][j].get(n,0)/o.size) for j in jj)
                     for n,o in zip(names,oo)]
            sizes = [round(o.size*1000) for o in oo]
            if any(abs(s/1000-o.size)>1e-10 for s,o in zip(sizes,oo)):
                continue
            items = [(sizes[t],t) for t,k in enumerate(count) for _ in range(k)]
            minL = math.ceil((model.cfg.min_bed_length-model.cfg.round_trim)*1000-1e-7)
            linear = oo[0].linear_weight
            cap = math.floor((min(model.cfg.bed_length,model.cfg.bed_weight/(p*linear))-model.cfg.round_trim)*1000+1e-7)
            capacities = {cap}
            for nb in range(1,1+math.ceil(model.cfg.bed_weight/blank.weight)):
                boundary = math.floor((nb*blank.weight/(p*linear)-model.cfg.round_trim)*1000+1e-7)
                if minL<=boundary<=cap:
                    capacities.add(boundary)
            old_k = sum(count)+len(jj)
            old_raw = sum(b['blank_counts'][j] for j in jj)*blank.weight
            best_rows = None
            best_v = total
            # Long first gives the strong deterministic baseline; randomized
            # piece and order rankings allow different remainder combinations.
            ranked = sorted(items,reverse=True)
            shuffled = items.copy();rng.shuffle(shuffled)
            order_noise = [rng.random() for _ in names]
            ordered = sorted(items,key=lambda x:(-order_noise[x[1]],-x[0]))
            for capacity in sorted(capacities,reverse=True):
                if math.ceil(sum(sizes[t]*k for t,k in enumerate(count))/capacity)>len(jj):
                    continue
                for sequence in [ranked,shuffled,ordered]:
                    bins = []
                    lengths = []
                    for length,t in sequence:
                        fitting = [j for j,L in enumerate(lengths) if L+length<=capacity]
                        if fitting:
                            target = max(fitting,key=lambda j:lengths[j])
                        else:
                            target = len(bins);bins.append(defaultdict(int));lengths.append(0)
                        bins[target][t]+=1;lengths[target]+=length
                    if len(bins)>len(jj):
                        continue
                    # Repair short terminal bins by moving single pieces from
                    # bins that remain legal; total production stays fixed.
                    repair_ok = True
                    for j in sorted(range(len(bins)),key=lambda j:lengths[j]):
                        while lengths[j]<minL:
                            candidates = [(sizes[t],donor,t) for donor,counts in enumerate(bins)
                                          if donor!=j for t,k in counts.items()
                                          if k and lengths[donor]-sizes[t]>=minL
                                          and lengths[j]+sizes[t]<=capacity]
                            if not candidates:
                                repair_ok = False;break
                            enough = [v for v in candidates if lengths[j]+v[0]>=minL]
                            length,donor,t = min(enough) if enough else max(candidates)
                            bins[donor][t]-=1;lengths[donor]-=length
                            bins[j][t]+=1;lengths[j]+=length
                        if not repair_ok:
                            break
                    if not repair_ok:
                        continue
                    rows = [model.make_round(ii,tuple(counts.get(t,0) for t in range(len(names))),p,blank)
                            for counts in bins]
                    attempted += 1
                    if any(r is None for r in rows):
                        continue
                    candidate = [total[0]-old_k+sum(r.knives for r in rows),total[1],
                                 total[2]-old_raw+sum(r.raw for r in rows),total[3]]
                    if score(candidate,capped)>score(best_v,capped)+1e-10:
                        best_rows,best_v=rows,candidate
            if best_rows is not None:
                removed += len(jj)-len(best_rows)
                for field in ['length_scheme','counts','blank_counts']:
                    for j in reversed(jj):
                        b[field].pop(j)
                for r in best_rows:
                    b['counts'].append(p);b['blank_counts'].append(r.blanks)
                    b['length_scheme'].append({n:round(k*o.size,9) for n,k,o in zip(names,r.ks,oo) if k})
                total=best_v;changed+=1;any_change=True
        if not any_change:
            break
    verified = totals(plan,model)
    if any(not math.isclose(a,b,rel_tol=1e-9,abs_tol=1e-5) for a,b in zip(total,verified)):
        raise ValueError('Repacking changed production unexpectedly')
    return dict(attempted=attempted,changed=changed,removed_rounds=removed,after=verified,
                estimated_score=score(verified,capped))


def repack_mixed_capacities(plan,model,seconds=90,capped=True):
    """Use a mixture of physical-limit and whole-billet-limit bins."""
    ids={o.oid:i for i,o in enumerate(model.orders)}
    rng=random.Random(260916)
    total=totals(plan,model);deadline=time.perf_counter()+seconds
    attempted=changed=removed=0
    groups=[]
    for bi,b in enumerate(plan):
        by_p=defaultdict(list)
        for j,p in enumerate(b['counts']):by_p[p].append(j)
        for p,jj in by_p.items():
            if len(jj)>2:groups.append((bi,p,len(jj)))
    groups.sort(key=lambda g:-g[2])
    for bi,p,_ in groups:
        if time.perf_counter()>=deadline:break
        b=plan[bi];jj=[j for j,x in enumerate(b['counts']) if x==p]
        blank=next(x for x in model.blanks if x.bid==b['blank_type'])
        names=list(dict.fromkeys(n for j in jj for n in b['length_scheme'][j]));ii=tuple(ids[n] for n in names)
        oo=[model.orders[i] for i in ii]
        count=[sum(round(b['length_scheme'][j].get(n,0)/o.size) for j in jj) for n,o in zip(names,oo)]
        sizes=[round(o.size*1000) for o in oo]
        if any(abs(s/1000-o.size)>1e-10 for s,o in zip(sizes,oo)):continue
        items=[(sizes[t],t) for t,k in enumerate(count) for _ in range(k)]
        minL=math.ceil((model.cfg.min_bed_length-model.cfg.round_trim)*1000-1e-7)
        linear=oo[0].linear_weight
        cap=math.floor((min(model.cfg.bed_length,model.cfg.bed_weight/(p*linear))-model.cfg.round_trim)*1000+1e-7)
        capacity_by_nb={}
        for nb in range(1,1+math.ceil(model.cfg.bed_weight/blank.weight)):
            capacity=min(cap,math.floor((nb*blank.weight/(p*linear)-model.cfg.round_trim)*1000+1e-7))
            if capacity>=minL:capacity_by_nb[nb]=capacity
            if capacity==cap:break
        if not capacity_by_nb:continue
        nbmax=max(capacity_by_nb);net=sum(sizes[t]*k for t,k in enumerate(count))
        old_k=sum(count)+len(jj);old_raw=sum(b['blank_counts'][j] for j in jj)*blank.weight
        best_rows=None;best_v=total
        layouts=[]
        for nr in range(math.ceil(net/cap),len(jj)+1):
            for base,basecap in capacity_by_nb.items():
                if base==nbmax:continue
                needed=max(0,math.ceil((net-nr*basecap)/(cap-basecap)))
                for nhigh in range(needed,min(nr,needed+3)+1):
                    capacities=[cap]*nhigh+[basecap]*(nr-nhigh)
                    raw=(nhigh*nbmax+(nr-nhigh)*base)*blank.weight
                    trial=[total[0]-old_k+sum(count)+nr,total[1],total[2]-old_raw+raw,total[3]]
                    if score(trial,capped)>score(best_v,capped)+1e-10:
                        layouts.append((score(trial,capped),capacities))
        layouts.sort(reverse=True,key=lambda x:x[0])
        sequences=[sorted(items,reverse=True),sorted(items)]
        for _ in range(2):
            seq=items.copy();rng.shuffle(seq);sequences.append(seq)
        for optimistic,capacities in layouts:
            if time.perf_counter()>=deadline:break
            if optimistic<score(best_v,capped)-1e-10:continue
            for sequence in sequences:
                bins=[defaultdict(int) for _ in capacities];lengths=[0]*len(bins)
                feasible=True
                for length,t in sequence:
                    fitting=[j for j,(L,C) in enumerate(zip(lengths,capacities)) if L+length<=C]
                    if not fitting:feasible=False;break
                    target=min(fitting,key=lambda j:capacities[j]-lengths[j])
                    bins[target][t]+=1;lengths[target]+=length
                if not feasible:continue
                for j in sorted(range(len(bins)),key=lambda j:lengths[j]):
                    while lengths[j]<minL:
                        candidates=[(sizes[t],donor,t) for donor,counts in enumerate(bins) if donor!=j
                                    for t,k in counts.items() if k and lengths[donor]-sizes[t]>=minL
                                    and lengths[j]+sizes[t]<=capacities[j]]
                        if not candidates:feasible=False;break
                        enough=[v for v in candidates if lengths[j]+v[0]>=minL]
                        length,donor,t=min(enough) if enough else max(candidates)
                        bins[donor][t]-=1;lengths[donor]-=length;bins[j][t]+=1;lengths[j]+=length
                    if not feasible:break
                if not feasible:continue
                rows=[model.make_round(ii,tuple(counts.get(t,0) for t in range(len(names))),p,blank) for counts in bins]
                attempted+=1
                if any(r is None for r in rows):continue
                candidate=[total[0]-old_k+sum(r.knives for r in rows),total[1],total[2]-old_raw+sum(r.raw for r in rows),total[3]]
                if score(candidate,capped)>score(best_v,capped)+1e-10:best_rows,best_v=rows,candidate
        if best_rows is not None:
            removed+=len(jj)-len(best_rows)
            for field in ['length_scheme','counts','blank_counts']:
                for j in reversed(jj):b[field].pop(j)
            for r in best_rows:
                b['counts'].append(p);b['blank_counts'].append(r.blanks)
                b['length_scheme'].append({n:round(k*o.size,9) for n,k,o in zip(names,r.ks,oo) if k})
            total=best_v;changed+=1
    verified=totals(plan,model)
    if any(not math.isclose(a,b,rel_tol=1e-9,abs_tol=1e-5) for a,b in zip(total,verified)):
        raise ValueError('Mixed capacities changed production unexpectedly')
    return dict(attempted=attempted,changed=changed,removed_rounds=removed,after=verified,estimated_score=score(verified,capped))


def run(args):
    started = time.perf_counter()
    source = Path(args.initial)
    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=True)
    config = json.loads((source.parent / 'config.json').read_text())
    cfg = Config(**config)
    orders = load_orders('data/orders.normalized.csv', cfg)
    model = Model(orders, cfg, load_blanks('data/blanks.normalized.csv'))
    original = json.loads(source.read_text())
    before = totals(original, model)
    plan = components(original)
    split_count = len(plan)
    history = []
    for iteration in range(args.passes):
        switches = choose_blanks(plan, model)
        plan = merge_compatible(plan, orders, cfg.max_rounds)
        changed, visits = refine_rows(plan, model, not args.uncapped, args.seconds / max(1, args.passes))
        plan = merge_compatible(components(plan), orders, cfg.max_rounds)
        current = totals(plan, model)
        if score(current, not args.uncapped) + 1e-8 < score(before, not args.uncapped):
            raise ValueError('Refinement decreased complete validated objective')
        history.append(dict(pass_index=iteration, switches=switches, row_changes=changed,
                            visited_rows=visits, totals=current,
                            capped_score=score(current), uncapped_score=score(current, False)))
        print(json.dumps(history[-1]), flush=True)
        atomic_json(root / 'checkpoint.json', plan)
        plan = components(plan)
        if switches == 0 and changed == 0:
            break
    plan = merge_compatible(plan, orders, cfg.max_rounds)
    pair_report = redistribute_pairs(plan, model, args.pair_seconds)
    if pair_report['changes']:
        plan = components(plan)
        choose_blanks(plan, model)
        plan = merge_compatible(plan, orders, cfg.max_rounds)
    repack_report = repack_parallel(plan,model,args.repack_seconds,capped=not args.uncapped)
    mixed_report = repack_mixed_capacities(plan,model,args.mixed_seconds,capped=not args.uncapped)
    metrics = validate_plan(plan, orders, cfg, model.blanks)
    independent = check(plan)
    if not independent['passed']:
        raise ValueError(independent['error_counts'])
    atomic_json(root / 'result.json', plan)
    atomic_json(root / 'config.json', config)
    atomic_json(root / 'data_audit.json', json.loads((source.parent / 'data_audit.json').read_text(encoding='utf-8')))
    atomic_json(root / 'report.json', dict(before=before, after=metrics, history=history,
                split_components=split_count, elapsed_seconds=time.perf_counter()-started,
                pair_redistribution=pair_report,
                parallel_repacking=repack_report,
                mixed_capacity_repacking=mixed_report,
                capped_knife_score=not args.uncapped, independent_check=independent,
                official_acceptance_verified=False))
    print(json.dumps(metrics), flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--initial', default='runs/continued_triples/result.json')
    ap.add_argument('--output', default='runs/material_refine')
    ap.add_argument('--seconds', type=float, default=60)
    ap.add_argument('--passes', type=int, default=3)
    ap.add_argument('--pair-seconds', type=float, default=0)
    ap.add_argument('--repack-seconds', type=float, default=0)
    ap.add_argument('--mixed-seconds', type=float, default=0)
    ap.add_argument('--uncapped', action='store_true')
    run(ap.parse_args())
