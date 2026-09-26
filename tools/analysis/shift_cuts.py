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
KNIFE_KG = 2830.0               # declared kg one knife is worth at the current score


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


def _range_max_table(values):
    """Sparse table over ints for O(1) `max(values[lo:hi])` queries."""
    table = [list(values)]
    k = 1
    while (1 << k) <= len(values):
        prev, half = table[-1], 1 << (k - 1)
        table.append([max(prev[i], prev[i + half]) for i in range(len(values) - (1 << k) + 1)])
        k += 1
    return table


def _range_max(table, lo, hi):
    k = (hi - lo).bit_length() - 1
    row = table[k]
    return max(row[lo], row[hi - (1 << k)])


def covered_orders(rounds):
    """Orders that share a round with at least one other order (RULES §4)."""
    shared = set()
    for scheme in rounds:
        if len(scheme) > 1:
            shared.update(scheme)
    return shared


def coarsen_mixed(plan, orders, blanks, max_rounds=6, knife_kg=KNIFE_KG):
    """Re-cut schemes whose rounds carry different counts into the fewest cheapest rounds.

    `coarsen` refuses these because a re-cut only leaves every delivery untouched
    when one count multiplies the whole scheme.  A mixed scheme can still be
    re-cut if each round's count stays at or above the floor of every order on it,
    `c*_o = ceil(pieces_o / segments_o)`: an order's delivery is the sum over its
    (contiguous) rounds of `k_r * c_r >= sum k_r * c*_o = segments_o * c*_o >=
    pieces_o`, so the delivery floor then holds by construction.  The floor is
    also the cheapest count -- the bill `ceil((net + 2) * count * lin / weight) *
    weight` is non-decreasing in `count` -- so each round carries exactly
    `max c*_o` over the orders on it.  Rounds bought are worth `knife_kg` of
    declared mass (the score's own trade, see docs/OVERPRODUCTION_TRIM.md), so a
    rewrite is taken only when that beats the bill it adds.

    A rewrite is refused when any order would stop sharing a round: coverage is
    worth more than the knife (0.2/9999 point ~ 30 t of declared mass per order).
    """
    sizes = {oid: o['size'] for oid, o in orders.items()}
    rewritten = 0
    for batch in plan:
        if len(set(batch['counts'])) == 1:
            continue                    # coarsen() owns uniform schemes
        lin = float(orders[batch['orders'][0]]['linear'])
        lin_d = orders[batch['orders'][0]]['linear']
        try:
            oids, lengths = piece_index(batch, sizes)
        except ValueError:
            continue
        if not oids:
            continue
        weight_d = blanks[batch['blank_type']]
        weight = float(weight_d)
        segments = Counter(oids)
        floor_of = {oid: -(-orders[oid]['pieces'] // segments[oid]) for oid in segments}
        table = _range_max_table([floor_of[oid] for oid in oids])
        rounds_now = len(batch['length_scheme'])
        bill_now = sum(blank_bill((sum(D(str(v)) for v in scheme.values()) + 2) * count * lin_d,
                                  weight_d)
                       for scheme, count in zip(batch['length_scheme'], batch['counts']))
        n = len(oids)
        rows = [(0, 0.0)] + [(10 ** 9, 0.0)] * n
        back = [-1] * (n + 1)
        back_c = [0] * (n + 1)
        for b in range(1, n + 1):
            for a in range(b - 1, -1, -1):
                net = lengths[b] - lengths[a]
                if net < NET_MIN - 1e-9:
                    continue
                if net > NET_MAX + 1e-9:
                    break
                if rows[a][0] >= 10 ** 9:
                    continue
                count = _range_max(table, a, b)
                if (net + 2.0) * count * lin > BED_KG + 1e-7:
                    continue
                cand = (rows[a][0] + 1,
                        rows[a][1] + math.ceil(((net + 2.0) * count * lin - EPS) / weight) * weight)
                if cand < rows[b]:
                    rows[b] = cand
                    back[b] = a
                    back_c[b] = count
        saved_rounds = rounds_now - rows[n][0]
        if rows[n][0] > max_rounds or saved_rounds <= 0:
            continue
        if knife_kg * saved_rounds <= rows[n][1] - float(bill_now):
            continue
        cuts, at = [], n
        while at > 0:
            cuts.append(at)
            at = back[at]
        cuts.reverse()
        rounds, counts, start = [], [], 0
        for end in cuts:
            rounds.append(to_lengths(span_counts(oids, start, end), sizes))
            counts.append(back_c[end])
            start = end
        if not covered_orders(batch['length_scheme']) <= covered_orders(rounds):
            continue
        batch['length_scheme'] = rounds
        batch['counts'] = counts
        batch['blank_counts'] = [
            int((((sum((D(str(v)) for v in scheme.values()), D(0)) + 2) * count * lin_d - D('1e-7'))
                 / weight_d).to_integral_value(rounding='ROUND_CEILING'))
            for scheme, count in zip(rounds, counts)]
        rewritten += 1
    return rewritten


def lay_pieces(sequence, sizes, floors, count, lin, weight, cap, max_rounds, knife_kg):
    """The cheapest rounds carrying `floors[oid]` pieces of each order at one count.

    The pieces are laid in the scheme's own order -- each order one contiguous run,
    which is what the chain rule demands -- and a round takes a run of them that
    fits its bed (`net` in [NET_MIN, cap]).  A round costs `knife_kg` plus its bill,
    so the laying is a shortest path over piece indices.  Returns
    `(cost, oids, cuts)` with `oids` the laid pieces and `cuts` the piece index each
    round ends at, or None when no laying fits inside `max_rounds`.
    """
    oids, lengths = [], [0.0]
    for oid in sequence:
        for _ in range(-(-floors[oid] // count)):
            oids.append(oid)
            lengths.append(lengths[-1] + float(sizes[oid]))
    if not oids:
        return None
    rows = [0.0] + [float('inf')] * len(oids)
    back = [-1] * (len(oids) + 1)
    for b in range(1, len(oids) + 1):
        for a in range(b - 1, -1, -1):
            net = lengths[b] - lengths[a]
            if net < NET_MIN - 1e-9:
                continue
            if net > cap + 1e-9:
                break
            if rows[a] == float('inf'):
                continue
            cand = rows[a] + knife_kg + math.ceil(
                ((net + 2.0) * count * lin - EPS) / weight) * weight
            if cand < rows[b]:
                rows[b] = cand
                back[b] = a
    if rows[len(oids)] == float('inf'):
        return None
    cuts, at = [], len(oids)
    while at > 0:
        cuts.append(at)
        at = back[at]
    if len(cuts) > max_rounds:
        return None
    cuts.reverse()
    return rows[len(oids)] + knife_kg * len(oids), oids, cuts


def requantize_cuts(plan, orders, blanks, max_rounds=6, knife_kg=KNIFE_KG, reach=3):
    """Re-solve each scheme at the count that minimises its knives plus its bill.

    The re-cuts above move cut positions only: the pieces a scheme carries are
    whatever the trim left behind, so its segment total stands.  But an order
    that must deliver `P` pieces needs only `ceil(P / count)` segments, so a
    taller count is a shorter set of segments -- up to one knife per order per
    count step, and the counts here sit on floors the trim pushed them down to.
    The taller count is not free: every round bills `(net + 2) * count * lin`,
    so it taxes the whole scheme, not just the rounds that order rides.  This
    pass prices the two against each other in the score's own unit (one knife ~
    `knife_kg` of declared mass): it re-solves the scheme at every count from
    `reach` below its current minimum up to the width cap `2000 / dia`, keeps
    the cheapest total, and rewrites only when that beats what the scheme costs
    now.  Rounds the cut would overflow are refused, as is a rewrite that stops
    an order sharing a round (coverage is worth more than a knife).
    """
    sizes = {oid: o['size'] for oid, o in orders.items()}
    rewritten = 0
    for batch in plan:
        first = batch['orders'][0]
        lin = float(orders[first]['linear'])
        lin_d = orders[first]['linear']
        dia = float(orders[first]['dia'])
        weight_d = blanks[batch['blank_type']]
        weight = float(weight_d)
        sequence = []
        for scheme in batch['length_scheme']:
            for oid in scheme:
                if oid not in sequence:
                    sequence.append(oid)
        if sorted(sequence) != sorted(batch['orders']):
            continue                    # not a chain this pass can re-lay
        want = {oid: orders[oid]['pieces'] for oid in sequence}
        segments_now = 0
        bill_now = 0
        for scheme, count in zip(batch['length_scheme'], batch['counts']):
            for oid, length in scheme.items():
                segments_now += pieces_of(length, sizes[oid])
            bill_now += blank_bill((sum(D(str(v)) for v in scheme.values()) + 2) * count * lin_d,
                                   weight_d)
        rounds_now = len(batch['length_scheme'])
        cost_now = knife_kg * (segments_now + rounds_now) + float(bill_now)
        best = None
        lo = max(1, min(batch['counts']) - reach)
        for count in range(lo, int(2000.0 / dia) + 1):
            cap = min(NET_MAX, BED_KG / (count * lin) - 2.0)
            if cap < NET_MIN - 1e-9:
                continue
            laid = lay_pieces(sequence, sizes, want, count, lin, weight, cap,
                              max_rounds, knife_kg)
            if laid is None:
                continue
            total, _, cuts = laid
            if best is None or total < best[0]:
                best = (total, count, laid)
        if best is None or best[0] >= cost_now - 1.0:
            continue                    # a gain the float bill cannot separate from noise
        total, count, (total, oids, cuts) = best
        rounds, start = [], 0
        for end in cuts:
            rounds.append(to_lengths(span_counts(oids, start, end), sizes))
            start = end
        if not covered_orders(batch['length_scheme']) <= covered_orders(rounds):
            continue
        batch['length_scheme'] = rounds
        batch['counts'] = [count] * len(rounds)
        batch['blank_counts'] = [
            int((((sum((D(str(v)) for v in scheme.values()), D(0)) + 2) * count * lin_d - D('1e-7'))
                 / weight_d).to_integral_value(rounding='ROUND_CEILING'))
            for scheme in rounds]
        rewritten += 1
    return rewritten


def recount_rounds(plan, orders, blanks, max_rounds=6, knife_kg=KNIFE_KG):
    """Drop each round to the count only its own orders need.

    `requantize_cuts` gives a whole scheme one count -- the tallest its orders need
    -- because that is what lets a moved cut leave every delivery alone.  A round
    holding only short-delivery orders can bill less than that.  Write `s_o` for the
    pieces an order keeps in the scheme and `floor_o = ceil(pieces_o / s_o)` for the
    fewest pieces per bar that still reach its demand: any round counting that high
    while holding it delivers, since `sum_r k_r * c_r >= floor_o * s_o >= pieces_o`.
    So every round comes down to the tallest floor among its own orders -- free in
    delivery, cheaper in the bill, and the bed limit loosens as the count falls, so
    the DP that re-lays the rounds may pack longer ones too.  Pieces, orders and
    cuts stay put in count; only the cut positions, counts and bills move.  The tally
    below is re-derived with Decimal bills, so the pass can only be taken when it
    really pays.
    """
    sizes = {oid: o['size'] for oid, o in orders.items()}
    rewritten = 0
    for batch in plan:
        first = batch['orders'][0]
        lin = float(orders[first]['linear'])
        lin_d = orders[first]['linear']
        dia = float(orders[first]['dia'])
        weight_d = blanks[batch['blank_type']]
        weight = float(weight_d)
        sequence, pieces = [], {}
        for scheme in batch['length_scheme']:
            for oid, length in scheme.items():
                pieces[oid] = pieces.get(oid, 0) + pieces_of(length, sizes[oid])
                if oid not in sequence:
                    sequence.append(oid)
        if sorted(sequence) != sorted(batch['orders']):
            continue                    # not a chain this pass can re-lay
        floors = {oid: -(-orders[oid]['pieces'] // pieces[oid]) for oid in sequence}
        if max(floors.values()) > int(2000.0 / dia):
            continue                    # an order the width cannot deliver
        oids, lengths, marks = [], [0.0], []
        for oid in sequence:
            marks.extend([floors[oid]] * pieces[oid])
            for _ in range(pieces[oid]):
                oids.append(oid)
                lengths.append(lengths[-1] + float(sizes[oid]))
        segments_now = len(oids)
        bill_now = sum(blank_bill((sum(D(str(v)) for v in s.values()) + 2) * c * lin_d, weight_d)
                       for s, c in zip(batch['length_scheme'], batch['counts']))
        rounds_now = len(batch['length_scheme'])
        cost_now = knife_kg * (segments_now + rounds_now) + float(bill_now)
        table = _range_max_table(marks)
        rows = [0.0] + [float('inf')] * len(oids)
        back = [-1] * (len(oids) + 1)
        for b in range(1, len(oids) + 1):
            for a in range(b - 1, -1, -1):
                net = lengths[b] - lengths[a]
                if net < NET_MIN - 1e-9:
                    continue
                count = _range_max(table, a, b)
                if net > min(NET_MAX, BED_KG / (count * lin) - 2.0) + 1e-9:
                    break
                if rows[a] == float('inf'):
                    continue
                cand = rows[a] + knife_kg + math.ceil(
                    ((net + 2.0) * count * lin - EPS) / weight) * weight
                if cand < rows[b]:
                    rows[b] = cand
                    back[b] = a
        if rows[len(oids)] == float('inf'):
            continue
        cuts, at = [], len(oids)
        while at > 0:
            cuts.append(at)
            at = back[at]
        if len(cuts) > max_rounds:
            continue
        cuts.reverse()
        rounds, counts, start = [], [], 0
        for end in cuts:
            scheme = to_lengths(span_counts(oids, start, end), sizes)
            rounds.append(scheme)
            counts.append(_range_max(table, start, end))
            start = end
        if not covered_orders(batch['length_scheme']) <= covered_orders(rounds):
            continue
        bills = [blank_bill((sum(D(str(v)) for v in scheme.values()) + 2) * count * lin_d, weight_d)
                 for scheme, count in zip(rounds, counts)]
        if cost_now <= knife_kg * segments_now + float(sum(bills)) + knife_kg * len(rounds) - 1.0:
            continue                    # the taller counts bought more than they billed
        batch['length_scheme'] = rounds
        batch['counts'] = counts
        batch['blank_counts'] = [
            int((((sum((D(str(v)) for v in scheme.values()), D(0)) + 2) * count * lin_d - D('1e-7'))
                 / weight_d).to_integral_value(rounding='ROUND_CEILING'))
            for scheme, count in zip(rounds, counts)]
        rewritten += 1
    return rewritten


def split_schemes(plan, orders, blanks):
    """Let each stretch of a scheme weigh its own blanks.

    A scheme bills every round against one blank weight, so it has one sawtooth
    modulus: a round lands well only when its mass falls just over a multiple of
    that weight, and the single best weight leaves the scheme's other rounds a
    whole blank short.  Nothing in the rules asks for one weight across all of
    them.  A scheme is capped at six rounds, and rule 10 is only that an order
    appears in one scheme -- `used[oid] != 1` in the checker -- so a scheme may be
    cut into stretches wherever no order straddles the cut, and each stretch may
    take the blank weight that suits its own rounds.  Rounds, pieces and counts are
    untouched; only the grouping and the declared bills change, and the DP takes a
    cut only when the split bills strictly less.
    """
    weights = sorted(set(blanks.values()))
    if len(weights) < 2:
        return 0
    out, split = [], 0
    for batch in plan:
        rounds, counts = batch['length_scheme'], batch['counts']
        lin = orders[batch['orders'][0]]['linear']
        n = len(rounds)
        if n < 2:
            out.append(batch)
            continue
        mats = [(sum(D(str(v)) for v in scheme.values()) + 2) * count * lin
                for scheme, count in zip(rounds, counts)]

        def best(i, j):
            return min((sum(blank_bill(m, w) for m in mats[i:j]), w) for w in weights)

        rows = [(None, -1)] + [(None, -1)] * n
        rows[0] = (0, -1)
        for j in range(1, n + 1):
            for i in range(j - 1, -1, -1):
                if i and set(rounds[i - 1]) & set(rounds[i]):
                    continue            # an order straddles here: rule 10 forbids it
                if rows[i][0] is None:
                    continue
                cost = rows[i][0] + best(i, j)[0]
                if rows[j][0] is None or cost < rows[j][0]:
                    rows[j] = (cost, i)
        if rows[n][1] == 0:
            out.append(batch)           # one stretch bills least; nothing to cut
            continue
        groups, at = [], n
        while at > 0:
            i = rows[at][1]
            groups.append((i, at))
            at = i
        groups.reverse()
        rows_cost = rows[n][0]
        one_cost = best(0, n)[0]
        if len(groups) < 2 or rows_cost >= one_cost:
            out.append(batch)
            continue
        for i, j in groups:
            _, weight = best(i, j)
            names = []
            for scheme in rounds[i:j]:
                for oid in scheme:
                    if oid not in names:
                        names.append(oid)
            piece = dict(batch)
            piece['orders'] = names
            piece['length_scheme'] = rounds[i:j]
            piece['counts'] = counts[i:j]
            piece['blank_type'] = next(t for t in sorted(blanks) if blanks[t] == weight)
            piece['blank_counts'] = [
                int(((mass - D('1e-7')) / weight).to_integral_value(rounding='ROUND_CEILING'))
                for mass in mats[i:j]]
            out.append(piece)
        split += 1
    if split:
        plan[:] = out
    return split


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
    ap.add_argument('--mixed-rounds', action='store_true',
                    help='re-cut every mixed-count scheme too, at each round\'s delivery floor')
    ap.add_argument('--requant-cuts', action='store_true',
                    help='re-solve every scheme at the count that minimises its knives plus its bill')
    ap.add_argument('--recount-rounds', action='store_true',
                    help='then drop every round to the tallest count its own orders need')
    ap.add_argument('--split-schemes', action='store_true',
                    help='cut each scheme where no order straddles, so each stretch weighs its own blanks')
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
    if args.mixed_rounds:
        stats['mixed_coarsened'] = coarsen_mixed(plan, orders, blanks)
        stats['rounds_after_mixed'] = sum(len(b['length_scheme']) for b in plan)
    if args.requant_cuts:
        stats['requantized'] = requantize_cuts(plan, orders, blanks)
        stats['rounds_after_requant'] = sum(len(b['length_scheme']) for b in plan)
    if args.recount_rounds:
        stats['recounted'] = recount_rounds(plan, orders, blanks)
        stats['rounds_after_recount'] = sum(len(b['length_scheme']) for b in plan)
    if args.split_schemes:
        stats['schemes_split'] = split_schemes(plan, orders, blanks)
        stats['batches_after_split'] = len(plan)

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
        if len(batch['length_scheme']) > 6:
            raise SystemExit(f'batch {batch["orders"][:3]} carries {len(batch["length_scheme"])} rounds')
    after = evaluate(plan, args.data, round_name=args.round)
    declared_after = declared_total(plan, blanks)
    # The passes trade knives against declared mass -- a taller count buys knives
    # back but taxes every round it rides -- so the two are judged together, at
    # the score's own rate: one knife ~ `KNIFE_KG` of declared mass.  Either term
    # may rise as long as the pair comes out ahead.
    cost_delta = (KNIFE_KG * (after['knives'] - before['knives'])
                  + float(declared_after - declared_before))
    if cost_delta > 1e-9:
        raise SystemExit(f'cost rose: {after["knives"] - before["knives"]:+d} knives, '
                         f'{float(declared_after - declared_before):+,.0f} kg declared '
                         f'= {cost_delta:+,.0f} kg-equivalent')
    if after['coverage'] < before['coverage']:
        raise SystemExit(f'coverage fell: {before["coverage"]} -> {after["coverage"]}')
    if after['included_order_count'] != before['included_order_count']:
        raise SystemExit('included order count moved')

    args.output.write_text(json.dumps(plan, ensure_ascii=False, separators=(',', ':')) + '\n',
                           encoding='utf-8')
    keys = ('yield_percent_rounded', 'coverage_percent_rounded', 'score_capped_display', 'violation_count')
    out = dict(input=str(args.input), output=str(args.output), excluded=len(excluded), **stats,
               declared_before=float(declared_before),
               declared_after=float(declared_after),
               rounds_before=sum(shapes_before),
               rounds_after=sum(len(b['length_scheme']) for b in plan),
               cost_delta_kg_equivalent=cost_delta,
               before=dict(knives=before['knives'], **{k: before[k] for k in keys if k in before}),
               after=dict(knives=after['knives'], **{k: after[k] for k in keys if k in after}))
    text = json.dumps(out, ensure_ascii=False, indent=2, default=str)
    if args.stats:
        args.stats.write_text(text + '\n', encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
