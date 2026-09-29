"""Whole-plan metrics -- K, M, D, H, S -- in one pure implementation.

Why this module exists: the chain search used to reach its whole-plan number
through `runs/_requant_report.py`, which is experiment code that is not in the
repo.  A clean checkout therefore could not reproduce a search result at all --
importing it failed, because it read `sys.argv` and ran a whole-plan statistic at
import time.  That is a reproducibility hole, not a documentation gap.

So: the same arithmetic, as a pure function with no import-time side effects, and
with the score itself taken from `platform_score.evaluate` -- the authoritative
scorer -- rather than recomputed in parallel.  Experiments and the packager then
rank on the identical number; `metrics()` asserts that the closed form it prints
agrees with that scorer, so a divergence fails loudly instead of drifting.

K and M are additive over batches; D and S are not.  Compose first, score once.
"""
from collections import Counter, defaultdict
from fractions import Fraction as F
from pathlib import Path

from platform_check import load_orders_and_blanks
from platform_score import evaluate, load_scoring_data

ROOT = Path(__file__).resolve().parents[1]

_TABLES = {}
_SCORING = {}


def default_data(round_name='semi'):
    """The CSV directory for a round: `data/semi` or `data/prelim`."""
    return ROOT / 'data' / round_name


def tables(data_dir=None, round_name='semi'):
    """`(orders, blanks, excluded, spec)` for a round, parsed once per process."""
    key = (str(data_dir or default_data(round_name)), round_name)
    if key not in _TABLES:
        _TABLES[key] = load_orders_and_blanks(Path(key[0]), round_name)
    return _TABLES[key]


def scoring(data_dir=None, round_name='semi'):
    """The scorer's own parsed context, cached.

    Without this `evaluate` re-reads and re-parses both CSVs on every call, which
    would make `metrics()` slower than the import-time load it replaces -- and it
    is called once per candidate in a sweep.
    """
    key = (str(data_dir or default_data(round_name)), round_name)
    if key not in _SCORING:
        _SCORING[key] = load_scoring_data(Path(key[0]), round_name)
    return _SCORING[key]


def metrics(plan, data_dir=None, round_name='semi'):
    """Every headline number for one plan, from one call.

    The score is `platform_score.evaluate`'s `score_capped`; the bill decomposition
    and the two blank floors are exact `Fraction` arithmetic.  Keys match
    `runs/_requant_report.py`, so this is a drop-in replacement for it.
    """
    data_dir = Path(data_dir or default_data(round_name))
    orders, blanks, _excluded, _spec = tables(data_dir, round_name)
    scored = evaluate(plan, data=data_dir, round_name=round_name,
                      scoring_data=scoring(data_dir, round_name))

    demand_kg = sum(F(str(orders[o]['weight'])) for o in orders)
    demand_geom = sum(F(str(orders[o]['pieces'])) * F(str(orders[o]['size']))
                      * F(str(orders[o]['linear'])) for o in orders)

    billed = mass = trim = F(0)
    floor_cur = floor_min = F(0)
    k_total = rounds = 0
    deliv = defaultdict(int)
    used = Counter()
    shared = set()
    for batch in plan:
        names = batch['orders']
        used.update(names)
        lin = F(str(orders[names[0]]['linear']))
        w = F(str(blanks[batch['blank_type']]))
        m_b = trim_b = F(0)
        for scheme, count in zip(batch['length_scheme'], batch['counts']):
            rounds += 1
            net = sum(F(str(v)) for v in scheme.values())
            m = (net + 2) * int(count) * lin
            m_b += m
            trim_b += 2 * int(count) * lin
            billed += _bill(m, w)
            for oid, length in scheme.items():
                p = int(F(str(length)) / F(str(orders[oid]['size'])))
                k_total += p
                deliv[oid] += p * int(count)
            if len(scheme) > 1:
                shared.update(scheme)
        mass += m_b
        trim += trim_b
        floor_cur += _bill(m_b, w)
        floor_min += _bill(sum(F(str(orders[o]['weight'])) for o in names) + trim_b, w)

    delivered = sum(F(str(orders[o]['size'])) * F(str(orders[o]['linear'])) * deliv[o]
                    for o in orders)
    over = sum(F(str(orders[o]['size'])) * F(str(orders[o]['linear']))
               * (deliv[o] - int(orders[o]['pieces']))
               for o in orders if deliv[o] > int(orders[o]['pieces']))
    # The order table's declared `weight` is not pieces * size * linear -- every one
    # of the 9,999 rows differs.  `weight` is what the platform's yield reads, so it
    # is the demand term; everything else below is built from the geometry, and
    # `table_gap` carries the difference so the decomposition closes.
    table_gap = demand_geom - demand_kg
    short_kg = demand_geom + over - delivered
    waste = billed - mass
    assert billed == demand_kg + table_gap + over - short_kg + trim + waste, \
        'the decomposition must close, or the terms are in two conventions'

    result = dict(
        plan=plan, schemes=len(plan), rounds=rounds, k=k_total, knives=k_total + rounds,
        billed=billed, mass=mass, trim=trim, od=over, waste=waste,
        table_gap=table_gap, short_kg=short_kg, floor_cur=floor_cur, floor_min=floor_min,
        yield_pct=100.0 * scored['finished_weight'] / scored['raw_weight'],
        cov_pct=100.0 * scored['coverage'], score=scored['score_capped'],
        score_uncapped=scored['score_uncapped'], shared=len(shared),
        short=sum(1 for o in orders if deliv[o] < int(orders[o]['pieces'])),
        violation_count=scored['violation_count'], violations=scored['violations'],
        finished_weight=scored['finished_weight'], raw_weight=scored['raw_weight'],
        occ=dict(Counter(used.values())))
    _check_closed_form(result, scored)
    return result


def _bill(m, w):
    """Declared kg for material `m` at blank weight `w` (ROUND_CEILING, half-open)."""
    x = (m - F(1, 10 ** 7)) / w
    return -((-x.numerator) // x.denominator) * w


def _check_closed_form(s, scored):
    """The printed score must be the scorer's, not a parallel arithmetic.

    The identity only holds above the knife baseline with no violations: at or
    below it `evaluate` caps the knife subscore at 100, and a violation carries
    its own penalty term -- either would make an honest disagreement look like a
    bug, so both cases are skipped rather than asserted.
    """
    base = scored['rules']['baseline_knives']
    if s['knives'] <= base or s['violation_count']:
        return
    closed = 0.4 * (100.0 * base / s['knives']) + 0.4 * s['yield_pct'] + 0.2 * s['cov_pct']
    assert abs(closed - s['score']) < 1e-9, (
        f'closed form {closed!r} != evaluate() {s["score"]!r}; the decomposition '
        'and the scorer have diverged')
