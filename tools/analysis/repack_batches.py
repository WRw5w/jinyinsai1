"""Re-group the plan's orders into fuller batches, then cut each one from scratch.

The trim passes leave the plan's batch *composition* alone, and that is where the
rounds are: a batch's chain is cut into `ceil(chain / cap(c))` rounds and its mass
into `ceil(mass / 59.2 t)`, so a group of orders whose chain is a little over five
rounds' worth still pays six, and a batch whose orders were never meant to share
a chain pays a round boundary apiece.  Nothing pins which same-steel, same-
diameter orders share a batch -- rule 10 only forbids an order appearing in two
of them -- so the composition is free and this pass re-chooses it.

The re-grouping is first-fit-decreasing by chain length: each group's orders,
heaviest first, into a batch that can still hold them inside six rounds (888 m of
bar, 355 t over six beds).  Each batch is then cut by `lay_pieces`, the shortest-
path laying that already minimises knives plus bill for a given count -- but the
count is now free, and the two ends of its range pull the score opposite ways:
a taller count needs fewer pieces per bar (`ceil(P / c)`), and those are knives,
while a shorter one lengthens the bar a round may carry (`cap(c)` falls as `c`
grows, because the bed holds `(net + 2) * count * lin`), which costs rounds, also
knives, and a fatter trim.  A scan of the whole range in the score's own unit --
one knife ~ `knife_kg` of declared mass -- picks the counts worth laying, and
only those are laid for real.

A batch packed to the last metre of the cap has no laying at all -- the cap is
888 m and the pieces are 4-11 m, so six rounds of 148 cannot tile it -- which is
why every packed batch is offered to the laying before it closes, and one that
has no laying hands its lightest order to the next batch.  A group whose rounds
stop sharing is kept as it was, and so is a group the re-cut does not make
cheaper: coverage and the plan's own cost are both worth more than a round.

The result is a whole plan, offered up for the same strict check as any other --
it is not applied in place.
"""
import argparse
import json
import math
import sys
import time
from collections import defaultdict
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shift_cuts as sc                                        # noqa: E402

CAP_ROUNDS = 6
FULL_BED_KG = 59200.0   # a full round's bar, at a 60 t bed and the 2 m trim
TOP_COUNTS = 5          # counts the scan shortlists for the real laying


def groups_of(plan, orders):
    """(steel, diameter) -> [oid], in the order the plan first shows them."""
    groups, seen = defaultdict(list), set()
    for batch in plan:
        for oid in batch['orders']:
            if oid not in seen:
                seen.add(oid)
                groups[(str(orders[oid]['steel']), float(orders[oid]['dia']))].append(oid)
    return groups


def width_cap(orders, oid):
    """The tallest count the roller admits for this order's diameter."""
    return max(1, int(2000 / float(orders[oid]['dia'])))


def batch_items(oids, orders):
    """`(chain length, oid)` per order at its own tallest count, longest first."""
    items = []
    for oid in oids:
        count = width_cap(orders, oid)
        units = -(-int(orders[oid]['pieces']) // count)
        items.append((units * float(orders[oid]['size']), oid))
    items.sort(key=lambda t: -t[0])
    return items


def scan_counts(pieces, sizes, count_max, lin, weight, knife_kg, max_rounds=CAP_ROUNDS):
    """Counts worth laying this chain at, cheapest first by the score's own unit.

    A count fixes the chain: `q_o = ceil(pieces_o / c)` units of `size_o` each, so
    the laying carries `sum(q_o * size_o)` metres and `sum(q_o)` pieces, and a
    round may hold `cap(c) = min(148, 60000 / (c * lin) - 2)` of it.  Knives are
    `sum(q_o)` pieces plus one per round, priced at `knife_kg` of declared mass
    each -- the strict scorer's own rate -- and the rounds are billed as if they
    were even, `ceil((chain / rounds + 2) * c * lin / w) * w` apiece.  The shortest
    path then decides among the counts this likes best, so the estimate only has
    to rank them; it must not rank one whose chain cannot fill a single round,
    which is the trap at the tall end -- fewest pieces and no laying at all.
    """
    out = []
    for count in range(1, count_max + 1):
        cap = min(sc.NET_MAX, sc.BED_KG / (count * lin) - 2.0)
        if cap < sc.NET_MIN - 1e-9:
            break
        units = 0
        chain = 0.0
        for pieces_o, size_o in zip(pieces, sizes):
            q = -(-pieces_o // count)
            units += q
            chain += q * size_o
        if chain < sc.NET_MIN - 1e-9:
            continue
        rounds = int(math.ceil(chain / cap - 1e-9))
        if rounds > max_rounds or chain < rounds * sc.NET_MIN - 1e-9:
            continue
        bill = rounds * math.ceil((chain / rounds + 2.0) * count * lin / weight - 1e-9) * weight
        out.append((knife_kg * (units + rounds) + bill, count))
    out.sort()
    return out[:TOP_COUNTS]


def cut_batch(sequence, orders, blanks, max_rounds=CAP_ROUNDS, knife_kg=sc.KNIFE_KG):
    """The cheapest rounds carrying this chain's demand, or None if none fit."""
    first = sequence[0]
    lin = float(orders[first]['linear'])
    lin_d = orders[first]['linear']
    sizes = {oid: orders[oid]['size'] for oid in sequence}
    want = {oid: orders[oid]['pieces'] for oid in sequence}
    pieces = [int(want[oid]) for oid in sequence]
    size_list = [float(sizes[oid]) for oid in sequence]
    best = None
    for blank_type in sorted(blanks, key=lambda t: blanks[t]):
        weight_d = blanks[blank_type]
        weight = float(weight_d)
        for _, count in scan_counts(pieces, size_list, width_cap(orders, first), lin,
                                    weight, knife_kg, max_rounds):
            cap = min(sc.NET_MAX, sc.BED_KG / (count * lin) - 2.0)
            laid = sc.lay_pieces(sequence, sizes, want, count, lin, weight, cap,
                                 max_rounds, knife_kg)
            if laid is None:
                continue
            if best is None or laid[0] < best[0]:
                best = (laid[0], blank_type, count, laid)
    if best is None:
        return None
    _, blank_type, count, (_, oids, cuts) = best
    weight_d = blanks[blank_type]
    rounds, start = [], 0
    for end in cuts:
        rounds.append(sc.to_lengths(sc.span_counts(oids, start, end), sizes))
        start = end
    return dict(orders=list(sequence), length_scheme=rounds, counts=[count] * len(rounds),
                blank_type=blank_type,
                blank_counts=[
                    int((((sum((D(str(v)) for v in scheme.values()), D(0)) + 2) * count * lin_d
                          - D('1e-7')) / weight_d).to_integral_value(rounding='ROUND_CEILING'))
                    for scheme in rounds])


def cut_packed(oids, orders, blanks, max_rounds=CAP_ROUNDS, knife_kg=sc.KNIFE_KG):
    """Packed batches for one group, each closed only once a laying can cut it."""
    items, made, pending = batch_items(oids, orders), [], []
    while items or pending:
        pool = sorted(items + pending, key=lambda t: -t[0])
        bag, length, rest = [], 0.0, []
        for chain, oid in pool:
            if length + chain <= max_rounds * sc.NET_MAX:
                bag.append((chain, oid))
                length += chain
            else:
                rest.append((chain, oid))
        cut = None
        while bag and cut is None:
            cut = cut_batch([oid for _, oid in bag], orders, blanks, max_rounds, knife_kg)
            if cut is None:
                rest.insert(0, bag.pop())       # the lightest order leaves first
        while cut is None and rest:
            # an order whose chain is too short to fill a round on its own needs
            # company: the lightest order still unplaced joins it until one laying
            # covers both
            bag.append(rest.pop())
            if sum(chain for chain, _ in bag) > max_rounds * sc.NET_MAX:
                return None
            cut = cut_batch([oid for _, oid in bag], orders, blanks, max_rounds, knife_kg)
        if cut is None:
            return None                          # a lone order no laying can carry
        made.append(cut)
        items, pending = [], rest
    return made


def cost_of(batches, orders, blanks, knife_kg=sc.KNIFE_KG):
    """`knife_kg * knives + declared - COVER_BONUS * covered`: a proxy objective.

    The same prices `reshape_plan` searches on: a knife is worth `knife_kg` kg of
    declared mass, a kilogram is a kilogram, and an order that stops sharing a
    round is worth `COVER_BONUS_KG` of it.  These are NOT the score's marginal
    rates at this plan's point: the default `knife_kg = sc.KNIFE_KG = 2,830`
    over-prices a knife by ~6.7% against the score's own 2,651.92 kg, and
    `COVER_BONUS_KG = 30,000` against the score's ~28,186.  A group that is cheaper
    here is therefore *usually* better on the board but not always -- the exact
    whole-plan score is what decides (see docs/CURRENT.md).
    """
    knives = declared = 0
    for batch in batches:
        weight = int(blanks[batch['blank_type']])
        for scheme in batch['length_scheme']:
            for oid, length in scheme.items():
                knives += sc.pieces_of(length, orders[oid]['size'])
            knives += 1                          # one knife per round
        declared += sum(int(c) * weight for c in batch['blank_counts'])
    return knife_kg * knives + declared - sc.COVER_BONUS_KG * len(covered(batches))


def covered(batches):
    """Orders in these batches that share a round with another order."""
    out = set()
    for batch in batches:
        out |= sc.covered_orders(batch['length_scheme'])
    return out


def repack(plan, orders, blanks, max_rounds=CAP_ROUNDS, knife_kg=sc.KNIFE_KG, verbose=False):
    """A plan with the same orders, re-batched and re-cut.  Returns (plan, stats)."""
    by_group = defaultdict(list)
    for batch in plan:
        first = batch['orders'][0]
        by_group[(str(orders[first]['steel']), float(orders[first]['dia']))].append(batch)
    built, kept = [], 0
    stats = dict(batches_before=len(plan), groups=len(by_group), groups_kept=0,
                 rounds_before=sum(len(b['length_scheme']) for b in plan), rounds_after=0)

    for key, oids in groups_of(plan, orders).items():
        old = by_group[key]
        made = cut_packed(oids, orders, blanks, max_rounds, knife_kg)
        if made is not None:
            cost_old = cost_of(old, orders, blanks, knife_kg)
            cost_new = cost_of(made, orders, blanks, knife_kg)
            if cost_new > cost_old:
                if verbose:
                    print(f'  {key[0]:>6s} d{key[1]:<5.1f}  {cost_new - cost_old:+,.0f} kg '
                          f'at the score\'s rate, kept the plan\'s batches')
                made = None
        if made is None:
            made, kept = old, kept + 1
        built.extend(made)
        stats['rounds_after'] += sum(len(b['length_scheme']) for b in made)
        if verbose:
            print(f'  {key[0]:>6s} d{key[1]:<5.1f}  {len(oids):5d} orders  '
                  f'{len(old):4d} -> {len(made):4d} batches  '
                  f'{sum(len(b["length_scheme"]) for b in old):5d} -> '
                  f'{sum(len(b["length_scheme"]) for b in made):5d} rounds')
    stats['batches_after'] = len(built)
    stats['groups_kept'] = kept
    stats['orders_after'] = sum(len(b['orders']) for b in built)
    return built, stats


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('input', help='plan JSON whose batches are re-grouped')
    ap.add_argument('--output', required=True)
    ap.add_argument('--stats')
    ap.add_argument('--data', default='data/semi')
    ap.add_argument('--round', default='semi')
    ap.add_argument('--verbose', action='store_true')
    args = ap.parse_args()

    sys.path.insert(0, 'src')
    from platform_check import check, load_orders_and_blanks, read_plan
    from platform_score import evaluate
    orders, blanks, _, _ = load_orders_and_blanks(Path(args.data), args.round)
    plan = read_plan(args.input)
    started = time.time()
    out, stats = repack(plan, orders, blanks, verbose=args.verbose)
    stats['seconds'] = round(time.time() - started, 1)
    Path(args.output).write_text(
        json.dumps(out, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    report = check(out, data=Path(args.data), weight_mode='strict', round=args.round)
    stats['strict_passed'] = report['passed']
    stats['strict_errors'] = report.get('error_counts') or {}
    after = evaluate(out, Path(args.data), round_name=args.round)
    before = evaluate(plan, Path(args.data), round_name=args.round)
    stats['knives_before'] = before['knives']
    stats['knives_after'] = after['knives']
    stats['score_before'] = round(before['score_capped'], 4)
    stats['score_after'] = round(after['score_capped'], 4)
    if args.stats:
        Path(args.stats).write_text(json.dumps(stats, ensure_ascii=False, indent=2) + '\n',
                                    encoding='utf-8')
    print(json.dumps(stats, ensure_ascii=False))
    if not report['passed'] or after['score_capped'] < before['score_capped'] - 1e-9:
        raise SystemExit('the re-grouped plan is not an improvement: keep the input')


if __name__ == '__main__':
    main()
