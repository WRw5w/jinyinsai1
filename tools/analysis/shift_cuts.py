"""Re-cut a certified scheme's rounds so their declared blank mass rounds up less.

THE RULE.  A round of `net` metres is billed at `ceil((net + 2) * count * lin /
weight) * weight` kg of declared blank mass, so the bill is a sawtooth in `net`:
period `weight`, and up to one whole blank of waste where the round sits just
past a multiple.  Moving a round boundary by one piece adds `size * count * lin`
kg to one round and removes it from its neighbour.  The piece stays cut, the
knife total is untouched, the chain stays a chain (so predicate B cannot notice
a cut that moved), and every order's delivered pieces are unchanged -- unless
the two rounds carry different counts, which the delivery check below covers.
What changes is where the two rounds sit on their sawteeth: when the receiving
round does not cross its next blank and the donor drops to the previous one, the
pair's declared mass falls by exactly one blank.

THE SECOND WIN.  A round holding pieces of a single order contributes nothing to
coverage, and a cut moved into the neighbour's run turns it into a shared round.
At the current yield a covered order is worth 0.2/9999 of a point ~ 30 t of
declared mass, so the search pays up to a blank for one.

THE THIRD WIN.  The delivered pieces run 220,141 (13.5 M kg) past demand, and
`count` is what multiplies them: a round hands `k * count` pieces to each order
on it.  Dropping a pattern piece costs `size * count * lin` kg of material at a
`count`-piece delivery cost and saves a knife; lowering a count costs
`(net + 2) * lin` kg of delivery and saves the same kg; moving a count from a
long round to a shorter round of the same scheme costs only the difference of
their piece totals and saves `(net_long - net_short) * lin` kg.  All of it spends
the same per-order slack budget at the same kg-per-piece rate, so drops (which
also buy a knife) go first, then `repack_counts` spends what is left in the
finest lumps the counts can take.  `drop_pieces`, `repack_counts`.

WHAT IT MAY CHANGE: cut positions (hence the lengths per (round, order)),
`counts`, `blank_counts`, `blank_type`.  WHAT IT MUST NOT: the orders of a
scheme, the number of rounds, the piece total, or any order's delivery.  The
final assertions re-derive all of that from the raw CSVs and the strict checker
rather than trusting this paragraph.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from platform_check import check, load_orders_and_blanks, read_plan   # noqa: E402
from platform_score import evaluate                                    # noqa: E402

NET_MIN = 48.0
NET_MAX = 148.0
BED_KG = 60000.0
EPS = 1e-6                      # the checker's 1e-7, widened for float scanning
COVER_BONUS_KG = 30000.0        # ~0.2/9999 point at the current yield level


def pieces_of(length, size):
    """Pieces in one written length; refuses anything off the 定尺 grid."""
    ratio = D(str(length)) / size
    k = int(ratio.to_integral_value())
    if k <= 0 or abs(ratio - k) > D('1e-8'):
        raise ValueError(f'length {length!r} is not a multiple of {size}')
    return k


def blank_bill(mass, weight):
    """Declared kg a round of material `mass` carries, as the checker reads it."""
    return int(((mass - D('1e-7')) / weight).to_integral_value(rounding='ROUND_CEILING')) * weight


def piece_index(batch, sizes):
    """(piece oids, cumulative length).  Seam runs are merged; a broken chain raises.

    The seam order's entries -- last on the left, first on the right -- are one
    contiguous run of pieces, which is what makes a cut a single number.  An
    order appearing twice without being adjacent is not a chain (that is the
    coverage rule itself), so it is refused here.
    """
    oids, lengths = [], [0.0]
    seen, last = set(), None
    for scheme in batch['length_scheme']:
        for oid, length in scheme.items():
            k = pieces_of(length, sizes[oid])
            if oid != last:
                if oid in seen:
                    raise ValueError(f'order {oid} is not contiguous in the scheme')
                seen.add(oid)
                last = oid
            oids.extend([oid] * k)
            lengths.extend(lengths[-1] + float(sizes[oid]) for _ in range(k))
    return oids, lengths


def cut_positions(batch, sizes):
    cuts, acc = [], 0
    for scheme in batch['length_scheme']:
        for oid, length in scheme.items():
            acc += pieces_of(length, sizes[oid])
        cuts.append(acc)
    return cuts


def span_counts(oids, start, end):
    """{oid: pieces} for piece indices [start, end), in first-appearance order."""
    counts = {}
    for oid in oids[start:end]:
        counts[oid] = counts.get(oid, 0) + 1
    return counts


def to_lengths(counts, sizes):
    return {oid: float(D(k) * sizes[oid]) for oid, k in counts.items()}


def sweep_cut(cuts, j, oids, lengths, sizes, counts, lin, weight, shared, delivery,
              demand, window, bonus):
    """Best position for cut `j`: (objective, position, left, right, cov, gain) or None."""
    lo = cuts[j - 1] if j > 0 else 0
    hi = cuts[j + 1]
    old_l = span_counts(oids, lo, cuts[j])
    old_r = span_counts(oids, cuts[j], hi)
    c_l, c_r = counts[j], counts[j + 1]

    def bill(net, count):
        return math.ceil(((net + 2.0) * count * lin - EPS) / weight) * weight

    base = (bill(lengths[cuts[j]] - lengths[lo], c_l)
            + bill(lengths[hi] - lengths[cuts[j]], c_r))
    lo_p = max(lo + 1, cuts[j] - window)
    hi_p = min(hi - 1, cuts[j] + window)
    # seed the counters at the leftmost candidate, then sweep upward one piece at
    # a time; `left`/`right` always describe position `p` when the body runs
    left = Counter(span_counts(oids, lo, lo_p))
    right = Counter(span_counts(oids, lo_p, hi))
    best = None
    for p in range(lo_p, hi_p + 1):
        if p > lo_p:
            moved = oids[p - 1]
            left[moved] += 1
            right[moved] -= 1
            if right[moved] == 0:
                del right[moved]
        net_l = lengths[p] - lengths[lo]
        net_r = lengths[hi] - lengths[p]
        if not (NET_MIN <= net_l <= NET_MAX and NET_MIN <= net_r <= NET_MAX):
            continue
        if (net_l + 2.0) * c_l * lin > BED_KG + EPS or (net_r + 2.0) * c_r * lin > BED_KG + EPS:
            continue
        gain = base - (bill(net_l, c_l) + bill(net_r, c_r))
        cov = 0
        for old, new in ((old_l, left), (old_r, right)):
            if (len(old) > 1) != (len(new) > 1):
                for oid in set(old) | set(new):
                    was_in = oid in old and len(old) > 1
                    now_in = oid in new and len(new) > 1
                    if now_in and not was_in and shared[oid] == 0:
                        cov += 1
                    elif was_in and not now_in and shared[oid] == 1:
                        cov -= 1
        if c_l != c_r:
            bad = False
            for oid in set(old_l) | set(old_r) | set(left) | set(right):
                old_p = old_l.get(oid, 0) * c_l + old_r.get(oid, 0) * c_r
                new_p = left.get(oid, 0) * c_l + right.get(oid, 0) * c_r
                if delivery[oid] - old_p + new_p < demand[oid]:
                    bad = True
                    break
            if bad:
                continue
        objective = gain + bonus * cov
        if objective > 1e-9 and (best is None or objective > best[0]):
            best = (objective, p, dict(left), dict(right), cov, gain)
    return best


def reshape_plan(plan, orders, blanks, window=32, passes=4, coverage=True, bonus=COVER_BONUS_KG):
    sizes = {oid: o['size'] for oid, o in orders.items()}
    demand = {oid: o['pieces'] for oid, o in orders.items()}
    shared = Counter()                     # order -> rounds shared with another order
    delivery = defaultdict(int)            # order -> delivered pieces
    schemes, skipped = [], 0
    for batch in plan:
        lin = float(orders[batch['orders'][0]]['linear'])
        try:
            oids, lengths = piece_index(batch, sizes)
        except ValueError:
            skipped += 1
            continue
        if len(set(batch['orders'])) < 2:
            skipped += 1
            continue
        schemes.append([batch, oids, lengths, cut_positions(batch, sizes), lin])
        for scheme, count in zip(batch['length_scheme'], batch['counts']):
            if len(scheme) > 1:
                for oid in scheme:
                    shared[oid] += 1
            for oid, length in scheme.items():
                delivery[oid] += pieces_of(length, sizes[oid]) * count
    covered_before = sum(1 for oid in shared if shared[oid] > 0)

    moves = cov_fixes = 0
    for _pass in range(passes):
        improved = 0
        for batch, oids, lengths, cuts, lin in schemes:
            counts = batch['counts']
            weight = float(blanks[batch['blank_type']])
            for j in range(len(cuts) - 1):
                res = sweep_cut(cuts, j, oids, lengths, sizes, counts, lin, weight, shared,
                                delivery, demand, window, bonus if coverage else 0.0)
                if res is None:
                    continue
                _, p, left, right, cov, _gain = res
                lo = cuts[j - 1] if j > 0 else 0
                hi = cuts[j + 1]
                old_l = span_counts(oids, lo, cuts[j])
                old_r = span_counts(oids, cuts[j], hi)
                for old, new, count in ((old_l, left, counts[j]), (old_r, right, counts[j + 1])):
                    if len(old) > 1:
                        for oid in old:
                            shared[oid] -= 1
                    if len(new) > 1:
                        for oid in new:
                            shared[oid] += 1
                    for oid in set(old) | set(new):
                        delivery[oid] += (new.get(oid, 0) - old.get(oid, 0)) * count
                batch['length_scheme'][j] = to_lengths(left, sizes)
                batch['length_scheme'][j + 1] = to_lengths(right, sizes)
                cuts[j] = p
                moves += 1
                cov_fixes += cov
                improved += 1
        if not improved:
            break
    covered_after = sum(1 for oid in shared if shared[oid] > 0)
    return dict(moves=moves, coverage_up=cov_fixes, schemes_left_alone=skipped,
                covered_before=covered_before, covered_after=covered_after)


def produced_by_order(plan, sizes):
    produced = defaultdict(int)
    for batch in plan:
        for scheme, count in zip(batch['length_scheme'], batch['counts']):
            for oid, length in scheme.items():
                produced[oid] += pieces_of(length, sizes[oid]) * count
    return produced


def drop_pieces(plan, orders):
    """Drop one piece per entry wherever the round can spare it; each drop is a knife.

    A dropped piece costs `size` metres of its round's length and buys exactly one
    knife; it also removes `size * count * lin` kg of material at a delivery cost of
    `count` pieces.  Per piece of slack that is the same rate as lowering a count
    -- `size * lin` kg per piece -- plus the knife, so drops go first.  The bed
    floor (`net >= 48`) and the order's slack bound each entry; smallest sizes
    first, because metres are the scarce resource.
    """
    sizes = {oid: o['size'] for oid, o in orders.items()}
    produced = produced_by_order(plan, sizes)
    demand = {oid: o['pieces'] for oid, o in orders.items()}
    slack_round = {}
    for a, batch in enumerate(plan):
        for j, scheme in enumerate(batch['length_scheme']):
            net = sum((D(str(v)) for v in scheme.values()), D(0))
            slack_round[a, j] = net - D(48)
    entries = []
    for a, batch in enumerate(plan):
        for j, (scheme, count) in enumerate(zip(batch['length_scheme'], batch['counts'])):
            for oid, length in scheme.items():
                entries.append((sizes[oid], a, j, oid))
    entries.sort(key=lambda e: (e[0], e[1], e[2], e[3]))
    dropped = 0
    for size, a, j, oid in entries:
        scheme, count = plan[a]['length_scheme'][j], plan[a]['counts'][j]
        k = pieces_of(scheme[oid], size)
        slack = produced[oid] - demand[oid]
        drop = min(k - 1, slack // count, int(slack_round[a, j] / size))
        if drop <= 0:
            continue
        scheme[oid] = float(D(k - drop) * size)
        produced[oid] -= drop * count
        slack_round[a, j] -= drop * size
        dropped += drop
    return dropped


def repack_counts(plan, orders, blanks):
    """Spend each order's leftover delivery budget in the finest lumps the counts allow.

    A round delivers `k * count` pieces to every order on it, so `count` is what
    multiplies the over-production: with the pieces, lengths and round count all
    untouched, lowering one round's count lowers exactly what it delivers and what
    it declares.  Each order's excess over its demand is the budget; one count
    taken off a round costs `(net + 2) * lin` kg of delivery and saves the same kg
    of material, so the over-production converts at par.  Knives do not move: the
    piece total and the round count are what the knife rule reads.

    The budgets are per order and a unit taken off a round spends from every order
    on it at once.  An order's budget stretches over about one count per round, so
    a round that takes two in a row starves the order's other rounds -- and a
    budget that no longer covers a round's piece total is stranded for good.  One
    count per round per pass, smallest piece total first, spreads it instead; a
    count moved from a long round to a shorter round of the same scheme costs only
    the difference of their piece totals, which is the lump that fits in what is
    left over.  A step is only taken when its own blank bill says so -- the
    receiving round of a move is where the sawtooth can jump up a whole blank --
    except that a drop is taken with no gain at all when nothing else crosses,
    because that is the step towards the next blank.
    """
    sizes = {oid: o['size'] for oid, o in orders.items()}
    produced = produced_by_order(plan, sizes)
    demand = {oid: o['pieces'] for oid, o in orders.items()}
    slack = {oid: produced[oid] - demand[oid] for oid in produced}
    moves_count = dropped = transferred = 0
    billed_gain = 0.0

    for batch in plan:
        head = orders[batch['orders'][0]]
        lin = float(head['linear'])
        weight = float(blanks[batch['blank_type']])
        width_cap = int(2000 // head['dia'])

        def bill(net, count):
            return math.ceil((net + 2.0) * count * lin / weight - 1e-9) * weight

        rounds = []
        for j, scheme in enumerate(batch['length_scheme']):
            net = sum(float(v) for v in scheme.values())
            k = {oid: pieces_of(length, sizes[oid]) for oid, length in scheme.items()}
            rounds.append([net, k, batch['counts'][j]])
        tables = [[bill(net, c) for c in range(width_cap + 1)]
                  for net, _k, _c in rounds]
        def fire(kind, r, s):
            nonlocal moves_count, dropped, transferred, billed_gain
            net_r, k_r, c_r = rounds[r]
            tab = tables[r]
            if kind == 'drop':
                gain = tab[c_r] - tab[c_r - 1]
                for oid, ko in k_r.items():
                    slack[oid] -= ko
                rounds[r][2] = c_r - 1
                dropped += 1
            else:
                net_s, k_s, c_s = rounds[s]
                gain = (tab[c_r] - tab[c_r - 1]) - (tables[s][c_s + 1] - tables[s][c_s])
                for oid, ko in k_r.items():
                    need = ko - k_s.get(oid, 0)
                    if need > 0:
                        slack[oid] -= need
                rounds[r][2] = c_r - 1
                rounds[s][2] = c_s + 1
                transferred += 1
            moves_count += 1
            billed_gain += gain
            return gain

        for _pass in range(64):
            # how many more times each round can afford to give a count away, and
            # how often the order that runs out first can afford it; the tightest
            # rounds go first, so a thin budget is not left behind by its own round
            todo = []
            for r, (net_r, k_r, c_r) in enumerate(rounds):
                if c_r <= 1:
                    continue
                room = min(slack[oid] // ko for oid, ko in k_r.items())
                if room > 0:
                    todo.append((room, sum(k_r.values()), 'drop', r, None))
                for s, (net_s, k_s, c_s) in enumerate(rounds):
                    if s == r or net_s >= net_r or c_s >= width_cap:
                        continue
                    if (net_s + 2.0) * (c_s + 1) * lin > BED_KG:
                        continue
                    room = 10 ** 9
                    lump = 0
                    for oid, ko in k_r.items():
                        need = ko - k_s.get(oid, 0)
                        lump += need
                        if need > 0:
                            room = min(room, slack[oid] // need)
                    if room > 0:
                        todo.append((room, lump, 'xfer', r, s))
            todo.sort()
            fired = set()
            # a step that already crosses a blank pays now; the tightest first
            for _room, _lump, kind, r, s in todo:
                if r in fired:
                    continue
                c_r = rounds[r][2]
                if c_r <= 1:
                    continue
                if kind == 'drop':
                    if any(slack[oid] < ko for oid, ko in rounds[r][1].items()):
                        continue
                    if tables[r][c_r] - tables[r][c_r - 1] <= 0:
                        continue
                else:
                    c_s = rounds[s][2]
                    if c_s >= width_cap or (rounds[s][0] + 2.0) * (c_s + 1) * lin > BED_KG:
                        continue
                    if any(slack[oid] < ko - rounds[s][1].get(oid, 0)
                           for oid, ko in rounds[r][1].items()):
                        continue
                    if ((tables[r][c_r] - tables[r][c_r - 1])
                            - (tables[s][c_s + 1] - tables[s][c_s])) <= 0:
                        continue
                fire(kind, r, s)
                fired.add(r)
            # nothing crosses right now, so walk a drop towards the next blank
            for _room, _lump, kind, r, _s in todo:
                if r in fired:
                    continue
                c_r = rounds[r][2]
                if c_r <= 1:
                    continue
                if kind == 'drop':
                    if any(slack[oid] < ko for oid, ko in rounds[r][1].items()):
                        continue
                    fire('drop', r, None)
                    fired.add(r)
            if not fired:
                break
        batch['counts'] = [c for _net, _k, c in rounds]
    return moves_count, dropped, transferred, round(billed_gain, 1), sum(slack.values())


def retype_and_requant(plan, orders, blanks):
    """Pick each batch's cheapest blank type, then write the checker's ceil counts."""
    for batch in plan:
        lin = orders[batch['orders'][0]]['linear']
        mats = [(sum((D(str(v)) for v in scheme.values()), D(0)) + 2) * count * lin
                for scheme, count in zip(batch['length_scheme'], batch['counts'])]
        batch['blank_type'] = min(blanks, key=lambda t: sum(blank_bill(m, blanks[t]) for m in mats))
        weight = blanks[batch['blank_type']]
        batch['blank_counts'] = [int(((m - D('1e-7')) / weight).to_integral_value(
            rounding='ROUND_CEILING')) for m in mats]


def coarsen(plan, orders, blanks, max_rounds=6):
    """Re-cut each uniform-count scheme into the fewest rounds, then the cheapest.

    Knives are `pieces + rounds`, and a re-cut moves no piece: every cut position
    keeping each round inside the bed band is a new plan with the same piece total
    and the same delivery (one count per scheme), so fewer rounds is strictly
    fewer knives.  The DP takes (rounds, declared mass) lexicographically over
    piece positions, which also lands each round on the cheapest side of its
    blank sawtooth before the shift pass below gets to it.
    """
    sizes = {oid: o['size'] for oid, o in orders.items()}
    coarsened = 0
    for batch in plan:
        if len(set(batch['counts'])) != 1:
            continue                    # per-round counts need a richer DP; leave alone
        lin = float(orders[batch['orders'][0]]['linear'])
        try:
            oids, lengths = piece_index(batch, sizes)
        except ValueError:
            continue
        if len(set(batch['orders'])) < 2:
            continue
        n = len(oids)
        count = batch['counts'][0]
        cap = min(NET_MAX, BED_KG / (count * lin) - 2.0)
        if cap < NET_MIN - 1e-9:
            continue
        weight = float(blanks[batch['blank_type']])
        rows = [(0, 0.0)] + [(10 ** 9, 0.0)] * n
        back = [-1] * (n + 1)
        for b in range(1, n + 1):
            for a in range(b - 1, -1, -1):
                net = lengths[b] - lengths[a]
                if net < NET_MIN - 1e-9:
                    continue
                if net > cap + 1e-9:
                    break
                if rows[a][0] >= 10 ** 9:
                    continue
                cand = (rows[a][0] + 1,
                        rows[a][1] + math.ceil(((net + 2.0) * count * lin - EPS) / weight) * weight)
                if cand < rows[b]:
                    rows[b] = cand
                    back[b] = a
        if rows[n][0] > max_rounds or rows[n][0] >= len(batch['length_scheme']):
            continue
        cuts, at = [], n
        while at > 0:
            cuts.append(at)
            at = back[at]
        cuts.reverse()
        start = 0
        rounds = []
        for end in cuts:
            rounds.append(to_lengths(span_counts(oids, start, end), sizes))
            start = end
        batch['length_scheme'] = rounds
        batch['counts'] = [count] * len(rounds)
        coarsened += 1
    return coarsened


def declared_total(plan, blanks):
    return sum((D(c) * D(str(blanks[b['blank_type']])) for b in plan for c in b['blank_counts']), D(0))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('input', type=Path, help='plan JSON or submission ZIP to re-cut')
    ap.add_argument('--data', type=Path, default=ROOT / 'data/semi')
    ap.add_argument('--round', default='semi')
    ap.add_argument('--window', type=int, default=32, help='pieces a cut may travel')
    ap.add_argument('--passes', type=int, default=4)
    ap.add_argument('--no-coverage', action='store_true', help='declared mass only')
    ap.add_argument('--no-retype', action='store_true', help='keep each batch blank_type')
    ap.add_argument('--bonus', type=float, default=COVER_BONUS_KG,
                    help='declared kg a newly covered order is worth in the search')
    ap.add_argument('--min-rounds', action='store_true',
                    help='re-cut each uniform-count scheme into the fewest rounds first')
    ap.add_argument('--no-drop-pieces', action='store_true',
                    help='keep the over-produced pattern pieces (each is a knife)')
    ap.add_argument('--no-lower-counts', action='store_true',
                    help='keep the counts, i.e. keep the over-production billed')
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--stats', type=Path)
    args = ap.parse_args()

    orders, blanks, excluded, spec = load_orders_and_blanks(args.data, args.round)
    plan = read_plan(args.input)
    before = evaluate(plan, args.data, round_name=args.round)
    declared_before = declared_total(plan, blanks)
    shapes_before = [len(b['length_scheme']) for b in plan]

    stats = {}
    if args.min_rounds:
        stats['coarsened'] = coarsen(plan, orders, blanks)
        stats['rounds_after_coarsen'] = sum(len(b['length_scheme']) for b in plan)

    def shift(tag, passes):
        out = reshape_plan(plan, orders, blanks, window=args.window, passes=passes,
                           coverage=not args.no_coverage, bonus=args.bonus)
        stats[tag] = out
        return out

    shift('reshape', args.passes)
    if not args.no_drop_pieces:
        stats['pieces_dropped'] = drop_pieces(plan, orders)
        shift('reshape2', 2)                # re-align what the drops disturbed
    if not args.no_lower_counts:
        moved, dropped, transferred, gain, slack_left = repack_counts(plan, orders, blanks)
        stats['count_moves'] = dict(total=moved, dropped=dropped, transferred=transferred,
                                    billed_gain=gain)
        stats['delivery_slack_left'] = slack_left
        shift('reshape3', 2)                # slack is spent; waste alignment only
    if not args.no_retype:
        retype_and_requant(plan, orders, blanks)
    else:
        for batch in plan:
            lin = orders[batch['orders'][0]]['linear']
            weight = blanks[batch['blank_type']]
            batch['blank_counts'] = [
                int(((((sum((D(str(v)) for v in scheme.values()), D(0)) + 2) * count * lin)
                      - D('1e-7')) / weight).to_integral_value(rounding='ROUND_CEILING'))
                for scheme, count in zip(batch['length_scheme'], batch['counts'])]
    stats['moves'] = sum(s.get('moves', 0) for k, s in stats.items() if k.startswith('reshape'))
    stats['coverage_up'] = sum(s.get('coverage_up', 0)
                               for k, s in stats.items() if k.startswith('reshape'))

    report = check(plan, data=args.data, weight_mode='strict', round=args.round)
    if not report['passed']:
        args.output.write_text(json.dumps(plan, ensure_ascii=False, separators=(',', ':')) + '\n',
                               encoding='utf-8')
        raise SystemExit(f're-cut plan fails the strict checker: {report["error_counts"]}')
    for batch in plan:
        for scheme, count in zip(batch['length_scheme'], batch['counts']):
            net = sum((D(str(v)) for v in scheme.values()), D(0))
            if not D(48) <= net <= D(148):
                raise SystemExit(f'round net {net} outside [48, 148]')
    after = evaluate(plan, args.data, round_name=args.round)
    if after['knives'] > before['knives']:
        raise SystemExit(f'knives rose: {before["knives"]} -> {after["knives"]}')
    if after['coverage'] < before['coverage']:
        raise SystemExit(f'coverage fell: {before["coverage"]} -> {after["coverage"]}')
    if after['included_order_count'] != before['included_order_count']:
        raise SystemExit('included order count moved')
    if any(len(b['length_scheme']) > k for b, k in zip(plan, shapes_before)):
        raise SystemExit('round count rose')

    args.output.write_text(json.dumps(plan, ensure_ascii=False, separators=(',', ':')) + '\n',
                           encoding='utf-8')
    keys = ('yield_percent_rounded', 'coverage_percent_rounded', 'score_capped_display', 'violation_count')
    out = dict(input=str(args.input), output=str(args.output), excluded=len(excluded), **stats,
               declared_before=float(declared_before),
               declared_after=float(declared_total(plan, blanks)),
               before=dict(knives=before['knives'], **{k: before[k] for k in keys if k in before}),
               after=dict(knives=after['knives'], **{k: after[k] for k in keys if k in after}))
    text = json.dumps(out, ensure_ascii=False, indent=2, default=str)
    if args.stats:
        args.stats.write_text(text + '\n', encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
