"""window_ils -- drive blank_step's exact window operator to a fixpoint on a whole plan.

`blank_step.probe` answers one question once: does *this* window, in *this* plan, hold a
legal improvement?  A complete census of plan_merge2 (all 9,144 two-round windows) found
15 improving windows in 15 distinct batches, each worth ~1e-4, and the moves are ones the
production polish never enumerates -- e.g. dK=-1 with dM=0, a billet-free knife save that
needs the joint re-split a single-round operator cannot reach.

Fifteen windows worth 2e-4 is not by itself worth a submission (92.4751 -> 92.478 still
rounds to the official 92.48), but it is the signature of an INCOMPLETE NEIGHBORHOOD
rather than of a plan at its optimum.  So: apply them, re-price the next sweep against
the moved baseline, and repeat until a sweep finds nothing.  Wave H's +0.03595 came from
129 improvements of exactly this size, so the count is what matters, not the per-move dS.

Every sweep ends in one of the plan's three classes:
  APPLIED    -- n windows improved the plan; the sweep moves on.
  FIXPOINT   -- a sweep whose windows were ALL fully enumerated found nothing.
  BUDGET     -- a sweep hit a time/state cap, so that sweep is UNDETERMINED, not a
                negative ("超时不能算'材料没空间'").

Safety is the probe's, unchanged: a config only lands if the whole patched plan still
passes the platform checker (strict weights, delivery anchors), and dS is the exact
score delta.  The local model can therefore cost a missed candidate, never an illegal one.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools' / 'analysis'))
sys.path.insert(0, str(ROOT / 'runs'))

import blank_step as BS                                            # noqa: E402
import requant_batches as RQ                                       # noqa: E402
from platform_check import load_orders_and_blanks, read_plan, check as plan_check  # noqa: E402


def rebind(plan, scratch):
    """Re-price the exchange rate against the plan as it stands now.

    `delta_s` is a rate linearised at (K0, M0).  A sweep applying dozens of small moves
    walks the baseline far enough that holding the entry rate would misprice the late
    windows in the sweep -- the same defect the probe had with a hard-coded baseline.

    Also owns the argv contract: `runs/_requant_report.py` scores `sys.argv[1]` at
    import, and this is the function that triggers that import, so setting it here keeps
    every caller (tests included) from having to know.
    """
    sys.argv = ['_requant_report', str(scratch)]
    scratch.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
    b = BS.true_score(scratch)
    BS.K0, BS.M0 = b['knives'], int(round(float(b['billed'])))
    return b


def best_window(batch, orders, blanks, window, spread, slack, cap_states, seconds):
    """The window with the largest dS > 0 in this batch, or (None, truncated)."""
    sizes_f = {o: BS.F(str(orders[o]['size'])) for o in batch['orders']}
    st = RQ.load_state(batch, sizes_f)
    wins = BS.coupled_windows(st, window)
    best = None
    truncated = False
    for win in wins:
        res, status, meta = BS.solve_window(batch, orders, blanks, win, spread,
                                           slack, cap_states, seconds)
        if status == 'budget':
            truncated = True
        if res and res[0]['dS'] > 0 and (best is None or res[0]['dS'] > best[0]['dS']):
            best = (res[0], win, meta, st)
    return best, truncated


def sweep_once(plan, orders, blanks, rnd, data, window, spread, slack, cap_states,
               seconds, total_seconds, batches=None):
    """One full pass: apply every batch's best improving window, in place.

    `batches` restricts the pass to those indices, which is how a KNOWN set of
    moves is settled into a plan (rebuild the union, check it, score it) without
    paying for a whole-plan sweep.
    """
    applied, truncated, t0 = [], 0, time.time()
    n_batches = len(plan)
    for bi in (range(n_batches) if batches is None else batches):
        if total_seconds and time.time() - t0 > total_seconds:
            print(f'[sweep budget] stopped at batch {bi}/{n_batches} '
                  f'({time.time() - t0:.0f}s of {total_seconds:.0f}s) -- UNDETERMINED')
            return plan, applied, truncated, False
        best, cut = best_window(plan[bi], orders, blanks, window, spread, slack,
                                cap_states, seconds)
        if cut:
            truncated += 1
        if best is None:
            continue
        res, win, meta, st = best
        out = BS.patch_plan(plan, bi, orders, blanks, st, win, res, meta)
        chk = plan_check(out, data, check_delivery=True, weight_mode='strict', round=rnd)
        if not chk['passed']:
            print(f'  [skip] bi={bi} win={list(win)} dS={res["dS"]:+.6f} '
                  f'FAILED the checker: {chk["error_counts"]}')
            continue
        plan = out
        applied.append(dict(bi=bi, win=list(win), dS=res['dS'], dK=res['dK'],
                            dM=res['dM'], counts=res['counts']))
        print(f'  [apply] bi={bi} win={list(win)} dS={res["dS"]:+.6f} '
              f'dK={res["dK"]:+d} dM={res["dM"]:+,.0f}')
    return plan, applied, truncated, True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--plan', required=True)
    ap.add_argument('--data', default='data/semi')
    ap.add_argument('--round', default='semi')
    ap.add_argument('--window', type=int, default=2, choices=(2, 3))
    ap.add_argument('--spread', type=int, default=4)
    ap.add_argument('--slack', type=int, default=3)
    ap.add_argument('--cap-states', type=int, default=40000)
    ap.add_argument('--seconds', type=float, default=120.0, help='per-window time cap')
    ap.add_argument('--iters', type=int, default=0, help='sweep cap (0 = until fixpoint)')
    ap.add_argument('--total-seconds', type=float, default=0.0)
    ap.add_argument('--batches', help='comma list; restrict the sweep to these indices')
    ap.add_argument('--out', required=True, help='final plan (only written if it improved)')
    ap.add_argument('--json', help='per-sweep report')
    args = ap.parse_args()
    sel = [int(x) for x in args.batches.split(',')] if args.batches else None

    data = Path(args.data)
    orders, blanks, _, _ = load_orders_and_blanks(data, args.round)
    plan = read_plan(ROOT / args.plan)
    scratch = Path('tmp/_window_ils_base.json')
    scratch.parent.mkdir(parents=True, exist_ok=True)
    b0 = rebind(plan, scratch)
    print(f'entry: score={b0["score"]:.4f} knives={b0["knives"]} '
          f'bill={float(b0["billed"]):,.0f} | window<={args.window} '
          f'spread={args.spread} slack={args.slack} seconds/window={args.seconds}')

    report, t0, it = [], time.time(), 0
    verdict = 'BUDGET'
    while True:
        it += 1
        print(f'--- sweep {it} ---')
        plan, applied, truncated, done = sweep_once(
            plan, orders, blanks, args.round, data, args.window, args.spread,
            args.slack, args.cap_states, args.seconds, args.total_seconds, sel)
        base = rebind(plan, scratch)
        swept_dS = sum(r['dS'] for r in applied)
        report.append(dict(sweep=it, applied=len(applied), truncated_windows=truncated,
                           complete=done, summed_dS=round(swept_dS, 6),
                           score=round(base['score'], 6), knives=base['knives'],
                           seconds=round(time.time() - t0, 1), moves=applied))
        print(f'sweep {it}: applied={len(applied)} truncated={truncated} '
              f'summed_dS={swept_dS:+.6f} -> score={base["score"]:.6f} '
              f'knives={base["knives"]} ({time.time() - t0:.0f}s)')
        if not done:
            verdict = 'BUDGET'
            break
        if not applied:
            verdict = 'FIXPOINT'
            break
        if args.iters and it >= args.iters:
            verdict = 'BUDGET'
            break

    out = ROOT / args.out
    if any(r['applied'] for r in report):
        out.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
        chk = plan_check(plan, data, check_delivery=True, weight_mode='strict',
                         round=args.round)
        t = BS.true_score(out)
        print(f'wrote {out}')
        print(f'final: score={t["score"]:.4f} (entry {b0["score"]:.4f}, '
              f'delta {t["score"] - b0["score"]:+.4f}) knives={t["knives"]} '
              f'check_passed={chk["passed"]} errors={chk["error_counts"]}')
    else:
        print(f'no improvement in {it} sweep(s); {args.out} not written (input unchanged)')
    print(f'verdict: {verdict} over {it} sweep(s), {time.time() - t0:.0f}s')
    if args.json:
        Path(args.json).write_text(json.dumps(dict(verdict=verdict, sweeps=report),
                                              ensure_ascii=False, indent=1),
                                   encoding='utf-8')
        print(f'wrote {args.json}')


if __name__ == '__main__':
    main()
