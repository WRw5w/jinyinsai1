"""chain_probe -- route B's acceptance sample: does changing the CHAIN pay?

Route B's variable is the order sequence -- which order straddles which round
boundary, and in what order the rounds fill.  `chain_layout.layout` already lays
any sequence out with zero errors; this tool holds the production polish fixed
and varies only the chain, so a win cannot be explained by spending more CPU.

Three arms, one batch:

  A0 control    the batch exactly as the plan has it, then the production polish
  A1 re-lay     the SAME order sequence, cut fresh by `chain_layout.layout`, then
                the same polish
  A2 perturbed  the sequence perturbed (adjacent swap / short insert / 3-reorder),
                re-laid by the same `layout`, then the same polish

A1 is the whole point of having three arms.  `layout` is a constructive cut, not
the descent the plan was built by, so it may lose on its own; if A1 already loses
to A0 then a *relay* costs, and an A2 win would still have to beat A1 to be
attributable to the chain rather than to luck in the polish.

Acceptance (the plan's section 4 sample) needs ALL THREE:
  1. val(A2) < val(A0) AND val(A2) < val(A1)       strictly cheaper, same budget
  2. the whole plan still passes `platform_check`  (strict weights, delivery)
  3. the round boundaries or seam identity actually changed vs A0's polish

(3) is what keeps an improvement from being claimed as structural when it only
moved numbers: if the winning state's round partition equals the control's, the
chain did not change anything and the win belongs to route A.  A sample that
fails any of the three is reported as "no chain win" and B closes per the review's
stage gate -- a bounded negative, not an open question, provided every arm ran.
"""
import argparse
import copy
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools' / 'analysis'))
sys.path.insert(0, str(ROOT / 'runs'))

import blank_step as BS                                            # noqa: E402
import chain_layout as CL                                          # noqa: E402
import requant_batches as RQ                                       # noqa: E402
from platform_check import load_orders_and_blanks, read_plan, check as plan_check  # noqa: E402


def chain_of(batch):
    """The batch's order sequence, as the rounds lay it out (first appearance)."""
    seq = []
    for rnd in batch['length_scheme']:
        for o in rnd:
            if o not in seq:
                seq.append(o)
    return seq


def perturbations(seq, max_arms=400):
    """(label, sequence) pairs for the three moves the review names, deduped.

    All adjacent swaps, every single-order move to another position (the short
    insert), and every permutation of each consecutive triple.  Bounded by
    `max_arms` because a big batch is O(n^2) here and the sample is time-boxed.
    """
    out, seen = [], {tuple(seq)}

    def offer(label, cand):
        if len(out) >= max_arms:
            return
        key = tuple(cand)
        if key in seen:
            return
        seen.add(key)
        out.append((label, list(cand)))

    for i in range(len(seq) - 1):                              # adjacent swap
        cand = list(seq)
        cand[i], cand[i + 1] = cand[i + 1], cand[i]
        offer(f'swap {i}<->{i + 1}', cand)
    for i in range(len(seq)):                                  # short-block insert
        for j in range(len(seq)):
            if j in (i, i + 1):
                continue
            cand = list(seq)
            o = cand.pop(i)
            cand.insert(j if j < i else j, o)
            offer(f'move {i}->{j}', cand)
    for i in range(len(seq) - 2):                              # three-order reorder
        triple = seq[i:i + 3]
        for p in ((0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)):
            cand = list(seq)
            cand[i:i + 3] = [triple[p[0]], triple[p[1]], triple[p[2]]]
            offer(f'reorder {i}+{p}', cand)
    return out


def rounds_to_state(rounds, sizes):
    """`chain_layout.layout` output as [pieces-per-bar per round], or None off-grid.

    Only the pieces; the count is `clearing_count`'s business, because the layout
    knows nothing about the delivery floors the count has to clear.
    """
    st = []
    for rnd in rounds:
        kd = {}
        for o, length in rnd.items():
            n = round(length / sizes[o])
            if n < 1 or abs(n * sizes[o] - length) > 1e-6:
                return None
            kd[o] = n
        if kd:
            st.append(kd)
    return st


def clearing_count(st, dem):
    """Smallest side-by-side count that delivers every order's piece floor."""
    held = {}
    for kd in st:
        for o, n in kd.items():
            held[o] = held.get(o, 0) + n
    count = 1
    for o, need in dem.items():
        if held.get(o, 0) <= 0:
            return None                       # an order the chain dropped entirely
        count = max(count, -(-need // held[o]))
    return count


def state_from_layout(batch, sizes_f, lin, dem, cap_c, order):
    """Re-lay `batch` in `order` and return a legal state, or None.

    `sizes_f` is a FLOAT map -- `chain_layout.layout` divides lengths by it and its
    own loader hands it floats, whereas `requant_batches` keeps 定尺 as Decimals.

    `layout` derives its length ceiling from the count it is handed, and at a big
    count the bed's 60 t tightens that ceiling hard: batch 1478's own count is 46,
    which caps a round at 89.1 m against a 440.8 m chain -- so the cut is done
    twice, raw first to learn the SMALLEST count that clears the piece floors
    (chain_layout's own rule: take the smallest clearing count, then derive the
    ceiling from it), then again under that count's ceiling.

    The second cut also tries a few round counts.  `layout` balances rounds to
    total/k, and the half-piece it rounds up per round is what pushes a cut past a
    tight ceiling -- batch 1478's own plan runs 73.5 m rounds against a 89.1 m
    ceiling, so equal halves are the wrong shape for it, not the chain.  The
    batch's own round count is the most defensible fallback.  A chain that lays
    out under none of them is reported as None, not silently skipped.
    """
    rounds = CL.layout(dict(batch), sizes_f, order=order)
    if rounds is None:
        return None
    st = rounds_to_state(rounds, sizes_f)
    if st is None:
        return None
    count = clearing_count(st, dem)
    if count is None or count > cap_c:
        return None
    ks = []
    for k in (len(rounds), len(batch['length_scheme']), len(rounds) + 1,
              len(rounds) + 2):
        if 1 <= k <= CL.MAX_ROUNDS and k not in ks:
            ks.append(k)
    for k in ks:
        cut = CL.layout(dict(batch, counts=[count] * k), sizes_f, linear=lin,
                        order=order, rounds=k)
        if cut is None:
            continue
        st = rounds_to_state(cut, sizes_f)
        if st is None:
            continue
        c2 = clearing_count(st, dem)
        if c2 is None or c2 > cap_c:
            continue
        return [(dict(kd), c2) for kd in st], c2
    return None


def polish_arm(st, w, sizes, dem, lin, cap_c, kicks, seed):
    """`iterated_polish` at a fixed budget: the production polish, every arm."""
    rng = random.Random(seed)
    val = RQ.evaluate(st, w, sizes, dem, lin, cap_c)
    if val[3] > 0:                            # repair a legal-ish start, or reject
        rep = RQ.polish(st, w, sizes, dem, lin, cap_c, allow_illegal=True)
        if rep is None or rep[0][3] > 0:
            return None
        st = rep[1]
    got = RQ.iterated_polish(st, w, sizes, dem, lin, cap_c, kicks, rng)
    return got


def boundary_sig(state):
    """(round count, per-round order sets, ordered seam pairs) for comparing shape."""
    rounds = [frozenset(kd) for kd, _ in state]
    seams = []
    for left, right in zip(rounds, rounds[1:]):
        seams.append(tuple(sorted(left & right)))
    return (len(rounds), tuple(tuple(sorted(r)) for r in rounds), tuple(seams))


def splice(plan, bi, state, w, sizes, orders, lin):
    """A copy of `plan` with batch `bi` replaced by `state`'s fields."""
    fields = RQ.state_to_fields(state, w, sizes, orders, lin)
    out = copy.deepcopy(plan)
    batch = out[bi]
    batch.update(fields)
    seq = []
    for rnd in fields['length_scheme']:
        for o in rnd:
            if o not in seq:
                seq.append(o)
    batch['orders'] = seq
    return out


def relay_candidate(val0, val1, slack):
    """Should arm A1's own state be scored as a whole-plan candidate?

    `val` is this tool's batch objective, and it prices a saw cut at ~2,710
    kg-equivalent; the score's knife term is worth only ~490 kg at this plan.  So
    `val` over-prices cuts by ~5.5x, and an arm that `val` calls *worse* can still
    be the better PLAN (and vice versa).  Judging the re-lay on `val` alone would
    drop real improvements, so `val` is used here only as a loose gate --
    `val1 - val0 <= slack` -- and `--out-relay` lets the whole-plan score decide.
    """
    return val1 - val0 <= slack


def report_result(path, plan, data, rnd):
    """Check the plan in memory; score the bytes on disk, exactly as the judge does.

    `plan_check` takes the plan object, `true_score` takes a path -- passing the
    path to the checker is what "Solution must be an array" means.  `true_score`
    imports `_requant_report`, which scores `sys.argv[1]` at import time, so the
    argv contract has to be satisfied before the first call, same as `window_ils`.
    """
    chk = plan_check(plan, data, check_delivery=True, weight_mode='strict', round=rnd)
    sys.argv = ['_requant_report', str(path)]
    scored = BS.true_score(path)
    return chk, scored


def pick_batch(plan, orders, blanks, min_orders=3, top=8):
    """Multi-order batches whose OWN chain lays out, worst material loss first.

    A batch is only a usable sample if `chain_layout` can lay its CURRENT chain
    out: otherwise arm A1 does not exist, and a treatment win could not be told
    apart from the plain re-lay.  That filter matters -- 1,707 of 1,796
    multi-order batches pass it, but the single batch with the most orders (1478,
    13 orders at count 46) does not, so picking by order count alone picks the one
    batch the experiment cannot run on.

    Among the usable ones the review's guidance is to diagnose the batches with
    the most material to lose, so rank by what the batch bills over what its
    orders actually need.  Returns [(loss_kg, bi, seq, n_orders)].
    """
    cands = []
    for bi, b in enumerate(plan):
        seq = chain_of(b)
        if len(seq) < min_orders:
            continue
        lin, cap_c, sizes, dem = RQ.batch_ctx(orders, b)
        sf = {o: float(v) for o, v in sizes.items()}
        if CL.layout(dict(b), sf, order=seq) is None:
            continue
        w = RQ.F(str(blanks[b['blank_type']]))
        billed = RQ.evaluate(RQ.load_state(b, sizes), w, sizes, dem, lin, cap_c)[1]
        need = sum(RQ.F(str(orders[o]['pieces'])) * sizes[o] * lin for o in dem)
        cands.append((float(billed - need), bi, seq, len(seq)))
    cands.sort(reverse=True, key=lambda r: r[0])
    return cands[:top]


def screen(plan, orders, blanks, min_orders=3, top=None):
    """Per-batch count of chain perturbations that even LAY OUT -- the census denominator.

    "No chain win" has two very different meanings and the verdict line cannot tell
    them apart.  Batch 89 is the cautionary case: its orders pin the count at 44 and
    the bed width caps it at 44 too, so the ceiling is 84.957 m against a 507.1 m
    chain -- 99.5% bed utilisation, and every perturbation overflows and is refused.
    Its chain neighbourhood is EMPTY, which is not evidence that B does not pay.

    So measure the denominator before quoting a win rate: how many batches have a
    non-empty chain neighbourhood at all.  Cheap on purpose -- `layout` only, no
    polish -- which is what makes it affordable over every batch instead of eight.
    Returns [(bi, n_orders, loss_kg, legal, screened, a1)].
    """
    rows = []
    for loss, bi, seq, n in pick_batch(plan, orders, blanks, min_orders=min_orders,
                                       top=top or len(plan)):
        b = plan[bi]
        lin, cap_c, sizes, dem = RQ.batch_ctx(orders, b)
        sf = {o: float(v) for o, v in sizes.items()}
        cands = perturbations(seq, max_arms=400)
        legal = sum(1 for _l, c in cands
                    if state_from_layout(b, sf, lin, dem, cap_c, c) is not None)
        a1 = state_from_layout(b, sf, lin, dem, cap_c, seq) is not None
        rows.append((bi, n, loss, legal, len(cands), a1))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--plan', required=True)
    ap.add_argument('--data', default='data/semi')
    ap.add_argument('--round', default='semi')
    ap.add_argument('--batch-i', type=int, help='defaults to the widest multi-order batch')
    ap.add_argument('--kicks', type=int, default=40,
                    help='production-polish budget, identical for every arm')
    ap.add_argument('--seed', type=int, default=20260928)
    ap.add_argument('--max-arms', type=int, default=400)
    ap.add_argument('--max-polish', type=int, default=80,
                    help='cap on perturbations polished in full (best pre-polish first)')
    ap.add_argument('--screen', action='store_true',
                    help='census the chain neighbourhood of every batch instead of probing one')
    ap.add_argument('--screen-top', type=int,
                    help='with --screen, only the N worst material-loss batches')
    ap.add_argument('--out', help='write the winning plan here (only on acceptance)')
    ap.add_argument('--out-relay', help='write the plan here when the plain RE-LAY '
                    '(arm A1) beats the plan on the whole-plan score; route A, not B')
    ap.add_argument('--relay-slack', type=float, default=20000.0,
                    help='with --out-relay, score A1 even if its `val` exceeds the '
                    "control's by up to this much (see `relay_candidate`)")
    ap.add_argument('--json', help='per-arm report')
    args = ap.parse_args()

    data = Path(args.data)
    orders, blanks, _, _ = load_orders_and_blanks(data, args.round)
    plan = read_plan(ROOT / args.plan)

    if args.screen:
        rows = screen(plan, orders, blanks, top=args.screen_top)
        empty = [r for r in rows if r[3] == 0]
        relaid = [r for r in rows if r[5]]
        print(f'{len(rows)} multi-order batches whose own chain lays out')
        print(f'  chain neighbourhood EMPTY (nothing can be re-laid): {len(empty)}')
        print(f'  own chain survives `layout`+count (A1 exists):       {len(relaid)}')
        for bi, n, loss, legal, tot, a1 in rows[:40]:
            print(f'  bi={bi:<5} {n:>2} orders loss={loss:>12,.0f} kg '
                  f'legal={legal:>3}/{tot:<3} A1={"y" if a1 else "n"}')
        if args.json:
            Path(args.json).write_text(json.dumps(
                [dict(bi=r[0], orders=r[1], loss=r[2], legal=r[3], screened=r[4], a1=r[5])
                 for r in rows], ensure_ascii=False, indent=1), encoding='utf-8')
            print(f'wrote {args.json}')
        return

    bi = args.batch_i
    if bi is None:
        ranked = pick_batch(plan, orders, blanks)
        if not ranked:
            print('no multi-order batch whose chain lays out; nothing to probe')
            return
        print(f'{len(ranked)} usable batches, worst material loss first:')
        for loss, i, _s, n in ranked:
            print(f'  bi={i:<5} {n:>2} orders  loss={loss:>12,.0f} kg')
        bi = ranked[0][1]
        print(f'picked batch {bi}')
    b = plan[bi]
    lin, cap_c, sizes, dem = RQ.batch_ctx(orders, b)
    sizes_f = {o: float(v) for o, v in sizes.items()}     # layout divides by these
    w = RQ.F(str(blanks[b['blank_type']]))
    seq = chain_of(b)
    print(f'batch {bi}: {len(seq)} orders, {len(b["length_scheme"])} rounds, '
          f'blank_type={b["blank_type"]} w={w} cap_c={cap_c}')

    t0 = time.time()
    # --- control arm: the plan's own chain, production polish -----------------
    st0 = RQ.load_state(b, sizes)
    a0 = polish_arm(st0, w, sizes, dem, lin, cap_c, args.kicks, args.seed)
    if a0 is None:
        print('control arm could not be polished -- refusing to judge')
        return
    sig0, val0 = boundary_sig(a0[1]), a0[0]
    print(f'A0 control   val={float(val0[0]):,.1f} knife={val0[2]} '
          f'bill={float(val0[1]):,.0f} rounds={sig0[0]} ({time.time() - t0:.1f}s)')

    # --- re-lay arm: same sequence, fresh cut ---------------------------------
    a1 = None
    laid = state_from_layout(b, sizes_f, lin, dem, cap_c, seq)
    if laid is not None:
        a1 = polish_arm(laid[0], w, sizes, dem, lin, cap_c, args.kicks, args.seed)
    if a1 is None:
        print('A1 re-lay    the same chain will not lay out legally -- '
              'a relay costs here, so any A2 win must beat A0 alone')
        sig1, val1 = None, None
    else:
        sig1, val1 = boundary_sig(a1[1]), a1[0]
        print(f'A1 re-lay    val={float(val1[0]):,.1f} knife={val1[2]} '
              f'bill={float(val1[1]):,.0f} rounds={sig1[0]} ({time.time() - t0:.1f}s)')

    # --- route A: the plain re-lay itself is cheaper than the plan ------------
    if args.out_relay and a1 is not None and sig1 != sig0 \
            and relay_candidate(float(val0[0]), float(val1[0]), args.relay_slack):
        relay_plan = splice(plan, bi, a1[1], w, sizes, orders, lin)
        rp = ROOT / args.out_relay
        rp.write_text(json.dumps(relay_plan, ensure_ascii=False), encoding='utf-8')
        rchk, rsc = report_result(rp, relay_plan, data, args.round)
        _, bsc = report_result(ROOT / args.plan, plan, data, args.round)
        gain = rsc['score'] - bsc['score']
        if rchk['passed'] and gain > 0:
            print(f'RELAY WIN   whole-plan {bsc["score"]:.4f} -> {rsc["score"]:.4f} '
                  f'({gain:+.4f}), knives {bsc["knives"]} -> {rsc["knives"]}')
            print(f'wrote {args.out_relay}')
        else:
            rp.unlink()
            print(f'RELAY no    whole-plan {bsc["score"]:.4f} -> {rsc["score"]:.4f} '
                  f'({gain:+.4f}), check_passed={rchk["passed"]}')

    # --- treatment arms: perturbed chains -------------------------------------
    cands = perturbations(seq, max_arms=args.max_arms)
    print(f'{len(cands)} chain perturbations to lay out')
    legal, illegal = [], 0
    for label, cand_seq in cands:
        laid = state_from_layout(b, sizes_f, lin, dem, cap_c, cand_seq)
        if laid is None:
            illegal += 1
            continue
        val = RQ.evaluate(laid[0], w, sizes, dem, lin, cap_c)
        legal.append(dict(label=label, state=laid[0], pre=float(val[0]),
                          pre_viol=float(val[3])))
    print(f'laid out legally: {len(legal)}/{len(cands)} '
          f'({illegal} chains will not lay out at this count)')
    if not legal:
        print('VERDICT: no chain win -- no perturbation could be laid out legally')
        return
    legal.sort(key=lambda r: r['pre'])
    todo = legal[:args.max_polish]
    if len(todo) < len(legal):
        print(f'polishing the best {len(todo)} of {len(legal)} legal chains')

    bestA2 = None
    for row in todo:
        t = time.time()
        got = polish_arm(row['state'], w, sizes, dem, lin, cap_c, args.kicks, args.seed)
        if got is None:
            print(f'A2 {row["label"]:<18} polish refused (illegal start)')
            continue
        sig, val = boundary_sig(got[1]), got[0]
        tag = 'same-shape' if sig == sig0 else 'NEW-SHAPE'
        print(f'A2 {row["label"]:<18} val={float(val[0]):,.1f} knife={val[2]} '
              f'bill={float(val[1]):,.0f} rounds={sig[0]} {tag} ({time.time() - t:.1f}s)')
        if bestA2 is None or float(val[0]) < bestA2[0]:
            bestA2 = (float(val[0]), row['label'], got, sig)

    # --- verdict --------------------------------------------------------------
    print(f'\ntotal {time.time() - t0:.0f}s; arms polished at kicks={args.kicks} '
          f'seed={args.seed}')
    if bestA2 is None:
        print('VERDICT: no chain win -- every perturbation was illegal or worse')
        return
    v2, label, got, sig = bestA2
    beats_a0 = v2 < float(val0[0])
    beats_a1 = val1 is None or v2 < float(val1[0])
    changed = sig != sig0
    print(f'best A2: {label} val={v2:,.1f}  beats A0={beats_a0} beats A1={beats_a1} '
          f'shape-changed={changed}')
    if not (beats_a0 and beats_a1 and changed):
        why = []
        if not beats_a0:
            why.append('not cheaper than the control')
        if not beats_a1:
            why.append('not cheaper than the plain re-lay (the win is the re-lay, not the chain)')
        if not changed:
            why.append('same round partition -- this is a number move, route A not B')
        print('VERDICT: no chain win -- ' + '; '.join(why))
        return

    # A win on the batch objective -- now the whole plan has to carry it.
    if args.out:
        out_plan = splice(plan, bi, got[1], w, sizes, orders, lin)
        out = ROOT / args.out
        out.write_text(json.dumps(out_plan, ensure_ascii=False), encoding='utf-8')
        chk, scored = report_result(out, out_plan, data, args.round)
        print(f'wrote {out}')
        print(f'whole-plan check_passed={chk["passed"]} errors={chk["error_counts"]}')
        print(f'whole-plan score={scored["score"]:.4f} knives={scored["knives"]} '
              f'bill={float(scored["billed"]):,.0f}')
        if not chk['passed']:
            print('VERDICT: rejected -- the batch win does not survive the whole-plan check')
        else:
            print(f'VERDICT: CHAIN WIN -- {label}; whole plan legal, '
                  f'shape changed, cheaper at equal budget')
    else:
        print(f'VERDICT: CHAIN WIN on the batch objective -- rerun with --out to '
              f'check the whole plan')

    if args.json:
        Path(args.json).write_text(json.dumps(dict(
            batch=bi, orders=len(seq), kicks=args.kicks, seed=args.seed,
            a0=dict(val=float(val0[0]), knife=val0[2], rounds=sig0[0]),
            a1=None if val1 is None else dict(val=float(val1[0]), knife=val1[2],
                                              rounds=sig1[0]),
            best_a2=dict(label=label, val=v2, rounds=sig[0], shape_changed=changed),
            screened=len(cands), legal=len(legal), polished=len(todo)),
            ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'wrote {args.json}')


if __name__ == '__main__':
    main()
