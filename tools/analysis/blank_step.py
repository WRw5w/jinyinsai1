"""blank_step -- the "少一根坯" reverse probe: joint count/piece moves over a round window.

Existing operators each cover half of this:
  * `shift_cuts`' transfer only moves pieces between rounds (counts fixed);
  * `requant_batches`' (k, count) sweep only re-solves ONE round at a time.
Neither can ask the question that decides whether material is worth another push:
**can two rounds jointly shed a whole billet?**  A round's bill is a staircase in its
net length (`bill(m, w) = ceil((m - 1e-7)/w) * w`), so two rounds parked near a tread
can sometimes be re-balanced into two nearer treads and shed one billet between them
without touching the round count.  The audit's counter-example is the whole idea:
102 + 82 -> 300, but 92 + 92 -> 200, same two rounds.

What is FIXED (so the round stays legal by construction, not by re-checking):
  batch membership, round count, round order, and which orders sit in each round --
  hence the seam structure and the coverage set are untouched, so dH = 0.
What VARIES jointly:
  the window rounds' counts, and every order's segment count in them.  (The seam
  rule is what makes this hard: only ONE order may be shared across a boundary, so
  the shared order's piece load is the only delivery coupling between two adjacent
  rounds -- exactly the lever a single-round operator cannot reach.)

The verdict is the user's exact score delta, not a kg/knife rate:

    dS = 6400000*(1/(K0+dK) - 1/K0) + 40*D*(1/(M0+dM) - 1/M0) + 20*dH/9999

with K0/M0/D the latest baseline's knives, bill and demand.  Every window ends in one
of three classes: IMPROVED (a legal config with dS > 0), INFEASIBLE-IN-DOMAIN (the
enumerated domain is exhausted with no dS > 0 -- a bounded proof, not a timeout), or
BUDGET (the time/state cap cut the enumeration short -- undetermined, NOT a negative).

Every IMPROVED config is re-scored against the whole plan with the platform-verified
local scorer (`runs/_requant_report.py`), so a bug in this file's local model can
only cost a missed candidate, never a false one.
"""
import argparse
import itertools
import json
import math
import sys
import time
from collections import defaultdict
from copy import deepcopy
from fractions import Fraction as F
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools' / 'analysis'))
sys.path.insert(0, str(ROOT / 'runs'))
from platform_check import load_orders_and_blanks, read_plan      # noqa: E402
import requant_batches as RQ                                      # noqa: E402

# The latest local baseline: plan_deep1 (see docs/CURRENT.md).  dS is measured from it.
K0 = 184531                 # knives
M0 = 536301947              # bill kg
DEM = 506157994             # order-table demand, the yield numerator
N_ORDERS = 9999             # coverage denominator
MASS_CAP = 60000.0          # bed weight, kg
BASE_PLAN = 'artifacts/runs/requant/plan_deep1.json'


def delta_s(dK, dM, dH=0):
    """The exact acceptance test.  Retires the old kg/knife exchange rate."""
    return (6400000.0 * (1.0 / (K0 + dK) - 1.0 / K0)
            + 40.0 * DEM * (1.0 / (M0 + dM) - 1.0 / M0)
            + 20.0 * dH / N_ORDERS)


def round_cap_net(c, lin):
    """Net-length ceiling for a round at count `c`: the 150 m bed less the 2 m
    trim, or the 60 t bed, whichever binds."""
    return min(148.0, MASS_CAP / (c * lin) - 2.0)


def _round_bill(net, c, lin, w):
    """The platform's per-round declared kg (exact)."""
    return RQ.bill((net + F(2)) * c * lin, w)


def coupled_windows(st, max_size=2):
    """Windows whose rounds are delivery-coupled: a pair (or triple) sharing >=1 order.

    Two rounds sharing no order have no joint lever -- changing each on its own is
    exactly the existing single-round operator, so they are excluded by design.
    """
    memb = [set(kd) for kd, _ in st]
    n = len(memb)
    out = []
    for i in range(n):
        for j in range(i + 1, n):
            if memb[i] & memb[j]:
                out.append((i, j))
    if max_size >= 3:
        for i in range(n):
            for j in range(i + 1, n):
                for k in range(j + 1, n):
                    if (memb[i] & memb[j]) and (memb[j] & memb[k]):
                        out.append((i, j, k))
    return out


def solve_window(batch, orders, blanks, win, spread, slack, cap_states, seconds):
    """Enumerate the window's legal joint configs; return (results, status, meta).

    `results` is one dict per count tuple, kept only where it beats the incumbent
    (sorted by dS descending).  `status` is 'ok' when the domain was fully
    enumerated, else 'budget'.
    """
    lin_f, cap_c, sizes_f, dem = RQ.batch_ctx(orders, batch)
    lin = float(lin_f)
    st = RQ.load_state(batch, sizes_f)
    w = F(str(blanks[batch['blank_type']]))
    m = len(win)
    memb = [set(st[i][0]) for i in win]
    touched = sorted(set().union(*memb))
    where = {o: [j for j in range(m) if o in memb[j]] for o in touched}
    size = {o: float(sizes_f[o]) for o in touched}
    # canonical (order, slot) processing order -- fixes the assignment tuple's layout
    slots_flat = [(o, j) for o in touched for j in where[o]]

    fixed = {o: 0 for o in touched}                 # delivery already booked outside
    for i, (kd, c) in enumerate(st):
        if i in win:
            continue
        for o, k in kd.items():
            if o in fixed:
                fixed[o] += k * c

    base_counts = [st[i][1] for i in win]
    base_nets = [sum(F(st[i][0][o]) * sizes_f[o] for o in st[i][0]) for i in win]
    base_bill = sum((_round_bill(base_nets[j], base_counts[j], lin_f, w)
                     for j in range(m)), F(0))
    base_ktot = sum(sum(st[i][0].values()) for i in win)

    grid = [range(max(1, c - spread), min(cap_c, c + 2) + 1) for c in base_counts]
    t0 = time.time()
    truncated = False
    pairs_done = 0
    results = []
    for counts in itertools.product(*grid):
        if time.time() - t0 > seconds:
            truncated = True
            break
        caps = [round_cap_net(counts[j], lin) for j in range(m)]
        if any(cap < 48.0 for cap in caps):
            continue
        states = {(0.0,) * m: (0, ())}
        feasible = True
        for o in touched:
            need = dem[o] - fixed[o]
            slots = where[o]
            sz = size[o]
            kcap = [min(int(caps[j] / sz), math.ceil(max(0, need) / counts[j]) + slack)
                    for j in slots]
            if any(k < 1 for k in kcap):
                feasible = False
                break
            opts = []
            if len(slots) == 1:
                j = slots[0]
                kmin = 1 if need <= 0 else math.ceil(need / counts[j])
                for k in range(kmin, kcap[0] + 1):
                    d = [0.0] * m
                    d[j] = k * sz
                    opts.append((tuple(d), k, (k,)))
            else:
                last = slots[-1]
                for pre in itertools.product(*[range(1, kcap[t] + 1)
                                               for t in range(len(slots) - 1)]):
                    got = sum(pre[t] * counts[slots[t]] for t in range(len(pre)))
                    kmin_l = 1 if need - got <= 0 else math.ceil((need - got) / counts[last])
                    if kmin_l > kcap[-1]:
                        continue
                    for kl in range(kmin_l, kcap[-1] + 1):
                        d = [0.0] * m
                        for t, j in enumerate(slots[:-1]):
                            d[j] = pre[t] * sz
                        d[last] = kl * sz
                        opts.append((tuple(d), sum(pre) + kl, tuple(pre) + (kl,)))
            if not opts:
                feasible = False
                break
            nxt = {}
            for key, (cost, assign) in states.items():
                for d, dk, kv in opts:
                    nk = tuple(key[j] + d[j] for j in range(m))
                    if any(nk[j] > caps[j] for j in range(m)):
                        continue
                    nc = cost + dk
                    cur = nxt.get(nk)
                    if cur is None or cur[0] > nc:
                        nxt[nk] = (nc, assign + kv)
            if not nxt:
                feasible = False
                break
            if len(nxt) > cap_states:
                truncated = True
                feasible = False
                break
            states = nxt
        if not feasible:
            continue
        pairs_done += 1
        best = None
        for key, (cost, assign) in states.items():
            if any(key[j] < 48.0 - 1e-9 for j in range(m)):
                continue
            nets = [F(str(key[j])) for j in range(m)]
            bill = sum((_round_bill(nets[j], counts[j], lin_f, w) for j in range(m)), F(0))
            dK = cost - base_ktot
            dM = bill - base_bill
            dS = delta_s(dK, float(dM))
            if best is None or dS > best['dS']:
                best = dict(win=list(win), counts=list(counts), ktot=cost, dK=dK,
                            nets=[float(x) for x in nets], bill=float(bill),
                            dM=float(dM), blanks=float((bill - base_bill) / w),
                            dS=dS, assign=assign)
        if best is not None:
            results.append(best)
    results.sort(key=lambda r: -r['dS'])
    meta = dict(base_counts=base_counts, base_ktot=base_ktot, base_bill=float(base_bill),
                base_nets=[float(x) for x in base_nets],
                base_blanks=[float(_round_bill(base_nets[j], base_counts[j], lin_f, w) / w)
                             for j in range(m)],
                w=float(w), pairs=len(list(itertools.product(*grid))), pairs_done=pairs_done,
                slots_flat=slots_flat, secs=time.time() - t0)
    return results, ('budget' if truncated else 'ok'), meta


def assignment_of(res, meta, st, win):
    """{round index -> {oid: k}} out of a result's flat assignment tuple."""
    out = {i: {} for i in win}
    for (o, j), k in zip(meta['slots_flat'], res['assign']):
        out[win[j]][o] = k
    return out


def report_window(bi, win, res, status, meta, top=3, only_improved=False):
    cls = 'BUDGET'
    if res:
        cls = 'IMPROVED' if res[0]['dS'] > 0 else 'INFEASIBLE-IN-DOMAIN'
    if status == 'budget':
        cls = 'BUDGET'
    if only_improved and cls != 'IMPROVED':
        return cls
    print(f'bi={bi} win={list(win)} counts={meta["base_counts"]} k='
          f'{meta["base_ktot"]} bill={meta["base_bill"]:,.0f} '
          f'blanks={meta["base_blanks"]} w={meta["w"]:,.0f}')
    for r in (res or [])[:top]:
        print(f'  dS={r["dS"]:+.6f}  dK={r["dK"]:+4d}  dM={r["dM"]:+12,.0f} kg '
              f'({r["blanks"]:+.3f} blank)  counts={r["counts"]} ktot={r["ktot"]} '
              f'nets={[round(x, 3) for x in r["nets"]]}')
    if status == 'budget':
        print(f'  [BUDGET] enumeration cut short ({meta["pairs_done"]}/{meta["pairs"]} '
              f'count pairs, {meta["secs"]:.1f}s) -- UNDETERMINED, not a negative')
    print(f'  -> {cls}   ({meta["pairs_done"]}/{meta["pairs"]} pairs, {meta["secs"]:.1f}s)')
    return cls


def true_score(path):
    """Whole-plan score from the platform-verified local scorer."""
    import _requant_report as RR              # runs stats(BASE) at import; argv set below
    return RR.stats(str(path))


def patch_plan(plan, bi, orders, blanks, st, win, res, meta):
    """Deep-copy `plan` with batch `bi`'s window re-cut to the result's config."""
    sizes_f = {o: F(str(orders[o]['size'])) for o in plan[bi]['orders']}
    lin_f = F(str(orders[plan[bi]['orders'][0]]['linear']))
    w = F(str(blanks[plan[bi]['blank_type']]))
    amap = assignment_of(res, meta, st, win)
    new_state = list(st)
    for j, i in enumerate(win):
        new_state[i] = ({o: amap[i][o] for o in st[i][0]}, res['counts'][j])
        missing = set(new_state[i][0]) - set(st[i][0])
        if missing:
            raise AssertionError(f'config invented orders {missing}')
    fields = RQ.state_to_fields(new_state, w, sizes_f, orders, lin_f)
    out = deepcopy(plan)
    out[bi]['length_scheme'] = fields['length_scheme']
    out[bi]['counts'] = fields['counts']
    out[bi]['blank_counts'] = fields['blank_counts']
    return out


def cmd_probe(args):
    from platform_check import check as plan_check
    orders, blanks, _, _ = load_orders_and_blanks(Path(args.data), args.round)
    plan = read_plan(args.plan)
    base = true_score(ROOT / BASE_PLAN)
    print(f'base K0={K0} M0={M0:,} DEM={DEM:,} | scorer says score={base["score"]:.4f} '
          f'knives={base["knives"]} bill={float(base["billed"]):,.0f}')
    print(f'budget spread={args.spread} slack={args.slack} window<={args.window} '
          f'seconds/window={args.seconds} cap_states={args.cap_states}')
    batches = _parse_batches(args.batches, plan)
    out_dir = Path(args.emit) if args.emit else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    classes = defaultdict(int)
    records = []
    t_start = time.time()
    sweep_done = True
    for bi in batches:
        if args.total_seconds and time.time() - t_start > args.total_seconds:
            sweep_done = False
            print(f'[total budget] stopped after {bi} batches '
                  f'({time.time() - t_start:.0f}s of {args.total_seconds:.0f}s)')
            break
        batch = plan[bi]
        sizes_f = {o: F(str(orders[o]['size'])) for o in batch['orders']}
        st = RQ.load_state(batch, sizes_f)
        wins = coupled_windows(st, args.window)
        if not wins:
            continue
        for win in wins:
            res, status, meta = solve_window(batch, orders, blanks, win, args.spread,
                                             args.slack, args.cap_states, args.seconds)
            cls = report_window(bi, list(win), res, status, meta, args.top,
                                args.only_improved)
            classes[cls] += 1
            rec = dict(bi=bi, win=list(win), cls=cls, status=status,
                       base=dict(counts=meta['base_counts'], ktot=meta['base_ktot'],
                                 bill=meta['base_bill'], blanks=meta['base_blanks'],
                                 w=meta['w']),
                       pairs=meta['pairs'], pairs_done=meta['pairs_done'],
                       secs=round(meta['secs'], 3))
            if res:
                r = res[0]
                rec['best'] = dict(dS=r['dS'], dK=r['dK'], dM=r['dM'],
                                   blanks=r['blanks'], counts=r['counts'], ktot=r['ktot'])
            if out_dir and res and res[0]['dS'] > 0:
                out = patch_plan(plan, bi, orders, blanks, st, win, res[0], meta)
                p = out_dir / f'bi{bi}_win{"-".join(map(str, win))}.json'
                p.write_text(json.dumps(out, ensure_ascii=False), encoding='utf-8')
                chk = plan_check(out, Path(args.data), check_delivery=True,
                                 weight_mode='strict', round=args.round)
                t = true_score(p)
                dK = t['knives'] - base['knives']
                dM = float(t['billed'] - base['billed'])
                dS = t['score'] - base['score']
                rec['emit'] = dict(path=str(p), check_passed=chk['passed'],
                                   check_errors=chk['error_counts'],
                                   dS=dS, dK=dK, dM=dM, score=t['score'])
                print(f'  [emit] {p}')
                print(f'  [check] passed={chk["passed"]} errors={chk["error_counts"]}')
                print(f'  [verify] local dS={res[0]["dS"]:+.6f} | whole-plan dS={dS:+.5f} '
                      f'(dK={dK:+d} dM={dM:+,.0f} kg) -> '
                      f'{"CONFIRMED" if dS > 0 and chk["passed"] else "REFUTED"}')
            records.append(rec)
    print('classes:', dict(classes), 'complete' if sweep_done else 'TRUNCATED')
    if args.json:
        Path(args.json).write_text(json.dumps(dict(complete=sweep_done, records=records),
                                              ensure_ascii=False, indent=1),
                                   encoding='utf-8')
        print(f'wrote {args.json} ({len(records)} window records)')


def _parse_batches(spec, plan):
    if spec in (None, '', 'all'):
        return list(range(len(plan)))
    if '-' in spec and ',' not in spec:
        a, b = spec.split('-')
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in spec.split(',') if x.strip()]


def cmd_select(args):
    """Screen batches for the window a two-round rebalance can actually shed a billet in.

    The bill model says exactly what to look for.  Round i sits `ex_i = used_i mod w`
    above its tread; move `d` kg out of it and it drops a whole tread as soon as
    `d >= ex_i`, while the receiver j stays on its tread as long as `d <= w - ex_j`.
    So a pair can shed one billet when `ex_i + ex_j <= w`, and the room it has to
    find such a `d` is `w - ex_i - ex_j`.  Rank by that room, and report how many
    minimal segment moves (`size_o * c`) it takes to span it: few moves = legal.
    """
    orders, blanks, _, _ = load_orders_and_blanks(Path(args.data), args.round)
    plan = read_plan(args.plan)
    rows = []
    for bi, batch in enumerate(plan):
        if not batch.get('length_scheme'):
            continue
        sizes_f = {o: F(str(orders[o]['size'])) for o in batch['orders']}
        st = RQ.load_state(batch, sizes_f)
        lin_f = F(str(orders[batch['orders'][0]]['linear']))
        w = F(str(blanks[batch['blank_type']]))
        nets = [sum(F(k) * sizes_f[o] for o, k in kd.items()) for kd, _ in st]
        used = [(nets[i] + F(2)) * st[i][1] * lin_f for i in range(len(st))]
        tot = sum((_round_bill(nets[i], st[i][1], lin_f, w) for i in range(len(st))), F(0))
        best_room, best_moves, seams = None, None, 0
        for i in range(len(st) - 1):
            j = i + 1
            shared = set(st[i][0]) & set(st[j][0])
            if not shared:
                continue
            seams += len(shared)
            ex = used[i] % w + used[j] % w
            room = w - ex
            units = [sizes_f[o] * st[i][1] for o in shared] + \
                    [sizes_f[o] * st[j][1] for o in shared]
            unit = min(units)
            if room >= 0 and (best_room is None or room > best_room):
                best_room, best_moves = room, room / unit
        if best_room is None:
            continue
        # also record the best pair even when no pair has room (how far off it is)
        rows.append(dict(bi=bi, rounds=len(st), orders=len(batch['orders']),
                         blank=float(w), room_kg=float(best_room),
                         moves=float(best_moves), bill=float(tot), shared=seams))
    rows.sort(key=lambda r: -r['room_kg'])
    print(f'{"bi":>6s} {"rounds":>6s} {"orders":>6s} {"room_kg":>11s} {"moves":>7s} '
          f'{"bill":>14s} {"shared":>6s}')
    for r in rows[:args.top]:
        print(f'{r["bi"]:>6d} {r["rounds"]:>6d} {r["orders"]:>6d} {r["room_kg"]:>11,.0f} '
              f'{r["moves"]:>7.1f} {r["bill"]:>14,.0f} {r["shared"]:>6d}')
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as fh:
            for r in rows:
                fh.write(json.dumps(r) + '\n')
        print(f'wrote {args.out} ({len(rows)} batches)')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('probe', help='run the joint two-round probe on chosen batches')
    p.add_argument('--plan', default='artifacts/runs/requant/plan_deep1.json')
    p.add_argument('--data', default='data/semi')
    p.add_argument('--round', default='semi')
    p.add_argument('--batches', default='all', help='all | a-b | i,j,k')
    p.add_argument('--window', type=int, default=2, choices=[2, 3])
    p.add_argument('--spread', type=int, default=4, help='counts grid: [c-spread, c+2]')
    p.add_argument('--slack', type=int, default=3, help='extra segments explored per order')
    p.add_argument('--top', type=int, default=3)
    p.add_argument('--cap-states', type=int, default=40000)
    p.add_argument('--seconds', type=float, default=120.0, help='per-window time cap')
    p.add_argument('--emit', help='write patched plans for IMPROVED windows here')
    p.add_argument('--json', help='write the per-window records here')
    p.add_argument('--only-improved', action='store_true',
                   help='print only IMPROVED windows (for a full sweep)')
    p.add_argument('--total-seconds', type=float, default=0.0,
                   help='stop the whole sweep after this many seconds (0 = no cap)')
    p.set_defaults(func=cmd_probe)

    s = sub.add_parser('select', help='rank batches by two-round rebalance potential')
    s.add_argument('--plan', default='artifacts/runs/requant/plan_deep1.json')
    s.add_argument('--data', default='data/semi')
    s.add_argument('--round', default='semi')
    s.add_argument('--top', type=int, default=40)
    s.add_argument('--out')
    s.set_defaults(func=cmd_select)

    args = ap.parse_args()
    # `_requant_report` reads sys.argv at import; give it a valid path up front.
    sys.argv = ['_requant_report', str(ROOT / BASE_PLAN)]
    args.func(args)


if __name__ == '__main__':
    main()
