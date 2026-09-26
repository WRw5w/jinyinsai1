"""Solve the whole semi-final drop, one (steel, diameter) group at a time.

Why this exists rather than a bigger `--limit` on `smoke_semi.py`: the drop has
9,999 valid orders across 36 groups, and the two largest hold 3,532 and 2,205.
`solver.search_10s` seeds one *bucket* at a time, and a bucket is a whole group,
so a group of 3,532 orders would enter `_seed_group` in one call and never
return -- the annealing phase the budget is meant for would never start.

So each group is chunked.  A chunk is solved as an independent sub-problem and
the chunks are concatenated into the group's plan.  Concatenation is sound
because the platform treats each scheme independently: every constraint (round
count per scheme, continuity per order within its scheme, blank accounting) is
scoped to one scheme, and no scheme spans two chunks.  Coverage is the one
metric that is NOT additive -- it needs two different orders in the same ROUND,
which `_pack_group` already arranges inside a chunk, and this driver measures
the assembled plan with the real `platform_score.evaluate` rather than summing
per-chunk numbers.

Checkpointing: each chunk writes `runs/<output>/chunks/<group>/<index>.json` and
the driver skips any chunk whose file already exists.  A run killed by the
sandbox therefore resumes instead of restarting, which matters because a full
pass is measured in hours, not minutes.

Usage:
    python -X utf8 solve_semi.py --seconds-per-chunk 60 --chunk 60
    python -X utf8 solve_semi.py --groups "NP01:43" "C60:26.5" --chunk 40
"""
from __future__ import annotations

import argparse
import json
import time
import traceback
from pathlib import Path

from platform_check import check
from platform_score import evaluate
from solver import Config, load_blanks, load_orders, search_10s, validate_plan

# Fraction of the drop that must already be solved before the per-order
# extrapolation in `DropAccounting.offsets` is trusted over its priors.
SAMPLE_FRACTION = 0.10


def pick_group(orders, spec):
    steel, _, dia = spec.partition(':')
    dia = float(dia)
    return [o for o in orders if o.steel == steel and abs(o.diameter - dia) < 1e-9]


def group_keys(orders):
    seen = []
    for o in orders:
        key = (o.steel, o.diameter)
        if key not in seen:
            seen.append(key)
    return seen


class DropAccounting:
    """Score one chunk as the whole drop, using the chunks already solved.

    A chunk is an independent sub-problem and its plan is concatenated into the
    group's, but the SCORE is not additive: knives, raw mass, delivered mass and
    coverage all enter the final formula as totals.  Scored on its own numbers a
    chunk prices all three terms wrongly, in three different ways -- the knife term
    saturates (`min(1, 160000/800) = 1`, no gradient), the yield term explodes
    (`30 * D_chunk / R_chunk^2` is 218x the drop's `30 * D_tot / R_tot^2`), and one
    covered order is worth `20/60` instead of `20/9999`.  Together they let the
    annealer buy yield with knives and look good doing it: measured over the full
    drop, +9917 knives for +0.300 pp of yield, -1.85 points.

    So each chunk is handed the metrics of everything it cannot see.  Two of those
    totals are anchored on an EXACT constant rather than an estimate, because a guess
    here is not a neutral error -- it rescales a shadow price:

      * the delivered-mass numerator is pinned at `sum of every order's demand
        ceiling`.  The semi-final caps each order at its demand and the plan must meet
        demand (clause 12), so the capped numerator cannot be more than that sum and
        is exactly that sum for any plan that delivers.  Verified on the seeding-only
        drop: 500,935,598.6 both ways.
      * `raw` is then `numerator / yield`, with the yield ratio taken from the chunks
        already solved and CLAMPED to [0.85, 0.95].  A clamp rather than a free
        estimate because the failure is asymmetric: an under-estimated `raw_offset`
        makes `30 * D / R^2` too LARGE, i.e. raw looks more expensive than it is, and
        the search will spend knives past break-even.  Both clamps bracket the drop's
        measured 0.9078 with room to spare.

    Knives and coverage keep the measured per-order extrapolation, with a floor for
    knives because re-binding `min(1, .)` would restore the flat objective outright.

    This is the FALLBACK.  It is only as good as its prior on the first chunks, and the
    prior has to be quoted in the same accounting convention as the offsets it feeds.
    `AnchorOffsets` below avoids the question by reading the offsets out of a plan that
    was actually measured; prefer it whenever a full plan exists.
    """

    YIELD_CLAMP = (0.85, 0.95)

    def __init__(self, total_orders, numerator_whole, knife_prior, knife_floor,
                 covered_prior):
        self.total_orders = total_orders
        self.numerator_whole = float(numerator_whole)
        self.knife_prior = float(knife_prior)
        self.knife_floor = float(knife_floor)
        self.covered_prior = float(covered_prior)
        self.done_orders = 0
        self.done = dict(knives=0.0, numerator=0.0, raw=0.0, covered=0.0)

    @property
    def enough(self):
        return self.done_orders >= SAMPLE_FRACTION * self.total_orders

    def add(self, plan, info, blank_weights, platform_floor):
        self.done['knives'] += internal_knives(plan, info, platform_floor)
        self.done['numerator'] += plan_capped_numerator(plan, info)
        self.done['raw'] += plan_raw_mass(plan, blank_weights)
        self.done['covered'] += plan_shared_coverage(plan)
        self.done_orders += sum(len(b['orders']) for b in plan)

    def _yield_estimate(self):
        if self.enough and self.done['raw'] > 0 and self.done['numerator'] > 0:
            measured = self.done['numerator'] / self.done['raw']
        else:
            measured = sum(self.YIELD_CLAMP) / 2
        low, high = self.YIELD_CLAMP
        return min(max(measured, low), high)

    def offsets(self, chunk):
        """`Config` offsets for `chunk` (a list of orders)."""
        chunk_orders = len(chunk)
        share = 1.0 - chunk_orders / self.total_orders
        if self.enough:
            knives_whole = self.done['knives'] * self.total_orders / self.done_orders
            covered_whole = self.done['covered'] * self.total_orders / self.done_orders
        else:
            knives_whole, covered_whole = self.knife_prior, self.covered_prior
        knives_whole = max(knives_whole, self.knife_floor)
        raw_whole = self.numerator_whole / self._yield_estimate()
        return dict(knives=max(knives_whole * share, 0.0),
                    numerator=max(self.numerator_whole * share, 0.0),
                    raw=max(raw_whole * share, 0.0),
                    covered=max(covered_whole * share, 0.0))


class AnchorOffsets:
    """Per-chunk offsets read straight out of a reference plan.

    `DropAccounting` extrapolates the per-order rate of the chunks already solved, so
    the first chunks of a run have nothing to extrapolate from and quote a prior.  That
    prior is the weak link, and it fails quietly: measured against the seeding-only
    drop, a cold share-scaled estimate puts the knife offset at 188,860 where the true
    background is 174,980 (+7.9%).  `knife_offset` enters
    `40 * min(1, 160000 / (K + offset))`, so an 8% error in the offset makes every knife
    15% cheaper (1.78e-4 vs 2.09e-4 points per knife) and the annealer is back to
    buying yield with knives -- the exact failure this repair exists to remove.

    A reference plan removes the guess.  The orders a chunk cannot see are the
    reference's batches that touch none of the chunk's orders, so the offsets ARE that
    subset's metrics -- there is no arithmetic to get wrong, only an index.  (Writing it
    as `whole - subset` inverts it into the chunk's OWN few hundred knives, which is how
    this was first written; the check that catches it is that `offset + subset` must
    reproduce `whole` bit for bit.)  Every term is additive over disjoint batches --
    knives and coverage count per scheme, delivered is summed per order -- so the split
    is an identity.  A chunk absent from the reference has the whole reference as its
    background, i.e. full offsets, which is the right answer.

    It is a warm-start device: the chunk is still solved from scratch, the reference
    supplies only the shadow prices.  The reference must therefore be a plan for the
    same drop, in the same knife convention.
    """

    def __init__(self, plan, info, blank_weights):
        self.plan = plan
        self.info = info
        self.blank_weights = blank_weights
        self.whole = self.metrics(plan)
        self.index = {}
        for pos, batch in enumerate(plan):
            for oid in batch['orders']:
                self.index.setdefault(oid, []).append(pos)

    def metrics(self, batches):
        return dict(knives=float(internal_knives(batches, self.info, True)),
                    numerator=plan_capped_numerator(batches, self.info),
                    raw=plan_raw_mass(batches, self.blank_weights),
                    covered=float(plan_shared_coverage(batches)))

    def background(self, chunk):
        """The reference batches that share no order with `chunk`."""
        touched = {pos for o in chunk for pos in self.index.get(o.oid, ())}
        return [b for pos, b in enumerate(self.plan) if pos not in touched]

    def offsets(self, chunk):
        """What everything but `chunk` contributes to the drop's totals."""
        return self.metrics(self.background(chunk))

    def add(self, plan, info, blank_weights, platform_floor):
        """No-op: the anchor is fixed, so the estimator never learns from a run."""


def order_info(orders):
    """`oid -> (size_m, scored_linear_weight_kg_per_m, demand_pieces)`.

    The weight is `linear_weight_int` -- the diameter truncated to whole millimetres --
    because that is the weight `platform_score` credits in the yield numerator and in
    the per-order demand ceiling.  Using the exact diameter instead over-states the
    numerator by 1.1% on this drop.
    """
    return {o.oid: (o.size, o.linear_weight_int, o.pieces) for o in orders}


def _segments(scheme, info):
    """`k` per order for one exported row: `length = k * size` under `net_shared_trim`."""
    return {oid: int(round(scheme[oid] / info[oid][0])) for oid in scheme}


def internal_knives(plan, info, platform_floor=True):
    """`Σ segments + 1` per round -- the knife count `Batch.metrics` reports.

    Recomputed from the exported JSON because a cache hit returns the plan without
    going through `search_10s`.  `platform_floor` must match `Config.knife_convention`
    or the offsets would be expressed in a different unit than the count `_key` adds
    them to -- the two conventions differ by 6% and not monotonically, so mixing them
    is worse than using either consistently.
    """
    total = 0
    for b in plan:
        for scheme in b['length_scheme']:
            if platform_floor:
                total += sum(int(scheme[oid] // info[oid][0]) for oid in scheme) + 1
            else:
                total += sum(_segments(scheme, info).values()) + 1
    return total


def plan_capped_numerator(plan, info):
    """Delivered mass with every order capped at its demand, as the semi-final scores it."""
    delivered = {}
    for b in plan:
        for scheme, parallel in zip(b['length_scheme'], b['counts']):
            for oid, k in _segments(scheme, info).items():
                delivered[oid] = delivered.get(oid, 0) + k * parallel
    return sum(min(delivered[oid], info[oid][2]) * info[oid][0] * info[oid][1]
               for oid in delivered)


def plan_raw_mass(plan, blank_weights):
    """`Σ blank_counts x blank weight` -- `platform_score`'s raw denominator."""
    return sum(sum(b['blank_counts']) * blank_weights[b['blank_type']] for b in plan)


def plan_shared_coverage(plan):
    """Orders that share a cold-bed round with another order -- the semi coverage rule."""
    covered = set()
    for b in plan:
        for scheme in b['length_scheme']:
            if len(scheme) > 1:
                covered.update(scheme)
    return len(covered)


def score_fingerprint(cfg):
    """The part of `cfg` that changes what `search_10s` returns, as a compact string."""
    return (f"K{cfg.knife_offset:.1f}|D{cfg.numerator_offset:.1f}|R{cfg.raw_offset:.1f}|"
            f"C{cfg.covered_offset:.1f}|N{cfg.coverage_total or 0:.0f}|"
            f"{int(cfg.cap_yield_numerator)}|{cfg.knife_convention}")


def solve_chunk(chunk, cfg, blanks, seconds, seed, cache_path, attempts=3, initial_plan=None):
    """Solve one chunk, memoising the result next to `cache_path`.

    Retries exist because failures here were observed to be TRANSIENT rather than
    deterministic: a chunk that never wrote its cache file (so the driver's
    `except` swallowed it) solved cleanly in 8.35 s when replayed standalone with
    the same budget.  The cause is outside the solver -- the sandbox reclaims long
    runs -- but the *consequence* is ours to fix: an unplaced chunk is exactly the
    order coverage the platform scores, so a one-shot `continue` silently forfeits
    points.  Each retry perturbs the seed, so a genuinely marginal chunk gets a
    different annealing trajectory instead of repeating a doomed one.

    Only exceptions are retried.  An annealed-out budget is NOT an exception:
    `search_10s` catches `SearchTimeout` itself and returns the incumbent, which
    seeding guarantees to be a complete, legal plan -- so the retry loop only ever
    sees real defects (`ModelError`, `validate_plan` violations), which is what
    makes three attempts the right order of magnitude rather than a band-aid.

    `initial_plan` (from `--refine`) skips seeding and starts the annealer from a
    plan that already covers the chunk.  `search_10s` then tracks its best over an
    incumbent that BEGINS at that plan, so refining cannot return something worse
    than what it was handed -- the pass is monotone by construction, not by hope.
    """
    fingerprint = score_fingerprint(cfg)
    if cache_path.exists():
        try:
            data = json.loads(cache_path.read_text(encoding='utf-8'))
            # The cache is keyed on the OBJECTIVE as well as on the chunk.  A plan
            # solved with all offsets at zero is not the answer to the same chunk
            # scored as part of the drop, so replaying it would mix two objectives
            # inside one plan -- the exact defect the offsets remove.  A cache written
            # before this field existed reads as the all-zero fingerprint, so
            # pre-existing runs stay valid under `--offset-mode none` and are
            # correctly re-solved under `auto`.
            if data.get('complete') and data.get('score_fingerprint') == fingerprint:
                return data['plan'], 'cached'
        except (json.JSONDecodeError, KeyError):
            pass
    last = None
    for attempt in range(attempts):
        started = time.perf_counter()
        try:
            plan = search_10s(chunk, cfg, seconds=seconds, seed=seed + attempt * 9973,
                              blanks=blanks, initial_plan=initial_plan)
        except Exception as exc:                                        # noqa: BLE001
            last = exc
            continue
        elapsed = time.perf_counter() - started
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(dict(complete=True, elapsed=round(elapsed, 3),
                                              attempts=attempt + 1,
                                              score_fingerprint=fingerprint,
                                              plan=plan),
                                         ensure_ascii=False,
                                         separators=(',', ':')) + '\n', encoding='utf-8')
        return plan, round(elapsed, 3)
    raise RuntimeError(f'chunk unsolved after {attempts} attempts: {last!r}')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data', default='data/semi')
    ap.add_argument('--config', default='data/semi/competition.config.json')
    ap.add_argument('--output', default='runs/semi_full')
    ap.add_argument('--chunk', type=int, default=60,
                    help='orders per solved sub-problem')
    ap.add_argument('--seconds-per-chunk', type=float, default=20.0,
                    help='ANNEALING budget per chunk; seeding is not counted '
                         'against it because it must run to completion')
    ap.add_argument('--budget', type=float, default=3600.0,
                    help='wall-clock budget for the whole run, seconds')
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--groups', nargs='*', default=None,
                    help='restrict to these "steel:diameter" specs')
    ap.add_argument('--offset-mode', choices=('anchor', 'share', 'none'), default='anchor',
                    help="how a chunk is priced as part of the whole drop (they differ by "
                         "1.85 points on the full semi drop, so A/B them rather than "
                         "guessing).  'anchor' (default) reads the four offsets out of "
                         "--offset-anchor, exact by construction.  'share' extrapolates "
                         "them from the chunks already solved and falls back to a prior "
                         "on the first ones, so it is cold-start weak.  'none' reproduces "
                         "the pre-2026-09-22 objective, in which a chunk was scored on its "
                         "OWN numbers: the knife term saturated flat, the yield term priced "
                         "raw 218x too high, and one covered order was worth 167x too much")
    ap.add_argument('--offset-anchor', default='runs/seed_fixed_all.json',
                    help='reference plan for --offset-mode anchor: a full plan for the '
                         'same drop, in the same knife convention')
    ap.add_argument('--refine', default=None,
                    help='warm-start every chunk from this full plan and anneal on top, '
                         'instead of seeding from scratch.  Monotone: a chunk it cannot '
                         'improve comes back unchanged.  Use a FRESH --output -- the chunk '
                         'cache is keyed on the objective, not on the initial plan, so '
                         'refined and from-scratch results would share cache entries')
    ap.add_argument('--skip-physical', action='store_true',
                    help='skip the per-chunk physical check (much faster)')
    args = ap.parse_args()

    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=True)
    settings = json.loads(Path(args.config).read_text(encoding='utf-8'))
    settings.update(continuity=True, coverage_shared=True, enforce_order_mass_floor=True,
                    cap_yield_numerator=True, knife_convention='platform_floor',
                    objective='platform_score', baseline_knives=160000)
    cfg = Config(**settings)
    all_orders = load_orders(str(Path(args.data) / 'orders.normalized.csv'), cfg, skip_invalid=True)
    blanks = load_blanks(str(Path(args.data) / 'blanks.normalized.csv'))
    # Record the offset policy next to the plan: `result.json` alone cannot say which
    # objective produced it, and the two are not interchangeable (1.85 points apart).
    settings['offset_mode'] = args.offset_mode
    settings['refine'] = args.refine
    (root / 'config.json').write_text(json.dumps(settings, ensure_ascii=False, indent=2) + '\n',
                                      encoding='utf-8')
    keys = group_keys(all_orders)
    if args.groups:
        wanted = set()
        for spec in args.groups:
            steel, _, dia = spec.partition(':')
            wanted.add((steel, float(dia)))
        keys = [k for k in keys if k in wanted]

    started = time.perf_counter()
    deadline = started + args.budget
    assembled = []
    per_group = []
    sizes = {o.oid: o.size for o in all_orders}
    info = order_info(all_orders)
    blank_weights = {b.bid: b.weight for b in blanks}
    platform_floor = cfg.knife_convention == 'platform_floor'
    # `numerator_whole` is not a guess: the semis cap every order's yield credit at its
    # demand, so the whole-drop numerator is exactly the sum of the per-order demand
    # ceilings for any plan that delivers (verified: 500,935,598.6 both ways).  It is
    # used by both estimators -- the anchored one as a cross-check, the share-scaled one
    # because its `raw` offset is derived from it.
    numerator_whole = sum(o.size * o.linear_weight_int * o.pieces for o in all_orders)
    if args.offset_mode == 'anchor':
        anchor_path = Path(args.offset_anchor)
        accounting = AnchorOffsets(json.loads(anchor_path.read_text(encoding='utf-8')),
                                   info, blank_weights)
        whole = accounting.whole
        print(f'[offsets] anchored on {anchor_path.name}: whole '
              f'K={whole["knives"]:.0f} D={whole["numerator"]:.1f} '
              f'R={whole["raw"]:.1f} C={whole["covered"]:.0f} '
              f'(demand ceiling {numerator_whole:.1f})', flush=True)
    elif args.offset_mode == 'share':
        # Same convention as the offsets it feeds: the `platform_floor` counts measured
        # on the seeding-only drop, in the solver's own `Σk + 1` accounting.
        accounting = DropAccounting(
            len(all_orders),
            numerator_whole=numerator_whole,
            knife_prior=175731.0 if platform_floor else 186376.0,
            knife_floor=1.05 * cfg.baseline_knives,
            covered_prior=len(all_orders))
    else:
        accounting = None
    # Warm-start material, sliced per chunk.  Only batches that sit ENTIRELY inside a
    # chunk are usable: a batch straddling a boundary names orders the chunk's `Model`
    # does not contain, and `_import_plan` would index off the end of it.
    refine_chunks = {}
    if args.refine:
        refine_plan = json.loads(Path(args.refine).read_text(encoding='utf-8'))
        for key in keys:
            members = pick_group(all_orders, f'{key[0]}:{key[1]}')
            for offset in range(0, len(members), args.chunk):
                oids = {o.oid for o in members[offset:offset + args.chunk]}
                inside = [b for b in refine_plan
                          if set(b['orders']) and set(b['orders']) <= oids]
                if inside:
                    refine_chunks[(key, offset)] = inside
        print(f'[refine] {len(refine_chunks)} chunks warm-started from '
              f'{Path(args.refine).name}', flush=True)
    for key in keys:
        if time.perf_counter() >= deadline:
            print(f'[budget] stopping before {key[0]}:{key[1]}', flush=True)
            break
        members = pick_group(all_orders, f'{key[0]}:{key[1]}')
        group_dir = root / 'chunks' / f'{key[0]}_{key[1]}'
        group_plan = []
        failed_offsets = []
        failed_orders = []
        missing_offsets = []
        group_offsets = None
        for offset in range(0, len(members), args.chunk):
            if time.perf_counter() >= deadline:
                missing_offsets.append(offset)
                continue
            chunk = members[offset:offset + args.chunk]
            cache_path = group_dir / f'{offset:06d}.json'
            if args.offset_mode == 'none':
                # Pre-fix objective: the chunk scored on its own numbers, with the
                # `intended` knife convention the pre-fix `Batch.metrics` used.
                cfg.knife_offset = cfg.numerator_offset = cfg.raw_offset = 0.0
                cfg.covered_offset = 0.0
                cfg.coverage_total = None
                cfg.knife_convention = 'intended'
                cfg.cap_yield_numerator = False
            else:
                offsets = accounting.offsets(chunk)
                cfg.knife_offset = offsets['knives']
                cfg.numerator_offset = offsets['numerator']
                cfg.raw_offset = offsets['raw']
                cfg.covered_offset = offsets['covered']
                cfg.coverage_total = float(len(all_orders))
            group_offsets = (cfg.knife_offset, cfg.raw_offset)
            try:
                plan, info_done = solve_chunk(chunk, cfg, blanks, args.seconds_per_chunk,
                                              args.seed + offset, cache_path,
                                              initial_plan=refine_chunks.get((key, offset)))
            except Exception as exc:                                  # noqa: BLE001
                # A chunk that fails must not lose the chunks around it, but it also
                # must not be silently dropped: unplaced orders are exactly the
                # coverage the platform scores.  The offsets and order ids are
                # written to disk so a follow-up pass can re-attack exactly those
                # chunks -- which is what makes the retry inside `solve_chunk`
                # meaningful rather than decorative.
                failed_offsets.append(offset)
                failed_orders.extend(o.oid for o in chunk)
                print(f'  [{key[0]}:{key[1]}] chunk @{offset} FAILED: '
                      f'{type(exc).__name__}: {exc}', flush=True)
                traceback.print_exc()
                continue
            group_plan.extend(plan)
            if accounting is not None:
                accounting.add(plan, info, blank_weights,
                               cfg.knife_convention == 'platform_floor')
        if failed_offsets or missing_offsets:
            # `mkdir` first: a group whose FIRST chunk fails has never written a
            # cache file, so `group_dir` does not exist yet and the write below blew
            # up with `FileNotFoundError` -- which REPLACED the real error in the
            # traceback and killed the whole run instead of recording the failure.
            # Hit while A/B-ing the solver with a zero annealing budget, where every
            # chunk failed and the driver died on the bookkeeping for it.
            group_dir.mkdir(parents=True, exist_ok=True)
            (group_dir / '_failed.json').write_text(
                json.dumps(dict(group=f'{key[0]}:{key[1]}', failed=failed_offsets,
                                unrun=missing_offsets, orders=failed_orders),
                           ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        elif (group_dir / '_failed.json').exists():
            # The group is now complete; retire the stale failure note so the next pass
            # does not re-attempt work that already succeeded, and so `watch_semi.py`
            # -- which reads the note's mere EXISTENCE as "this group has failures" --
            # stops reporting it.
            #
            # RENAMED, not deleted.  Deleting is the obvious semantics and it killed
            # three islands on 2026-09-23: the sandbox's safe-delete guard answered
            # `SAFE_DELETE_BULK_GUARD_ERROR state lock timeout` for a single 400-byte
            # file, and it TERMINATES the process instead of raising, so no `except`
            # can catch it.  Three resumes retried the same delete at the same second,
            # contended on the guard's state lock, and all three died at exactly
            # 59/183 -- a full group of work stranded over bookkeeping.  A rename does
            # not trip the guard (verified), and `watch_semi.py` only ever looks for
            # the `_failed.json` name, so moving it aside is equivalent.
            try:
                (group_dir / '_failed.json').replace(group_dir / '_failed.retired.json')
            except OSError as exc:                                    # noqa: BLE001
                print(f'  [{key[0]}:{key[1]}] could not retire _failed.json '
                      f'({type(exc).__name__}: {exc}); the group IS complete, the note '
                      f'is stale', flush=True)
        per_group.append(dict(group=f'{key[0]}:{key[1]}', orders=len(members),
                              plans=len(group_plan), failed_chunks=len(failed_offsets),
                              unrun_chunks=len(missing_offsets),
                              unplaced_orders=len(failed_orders)))
        assembled.extend(group_plan)
        print(f'[{key[0]}:{key[1]}] {len(members)} orders -> {len(group_plan)} schemes, '
              f'{len(failed_offsets)} failed / {len(missing_offsets)} unrun chunks, '
              f'K_off {group_offsets[0]:.0f} R_off {group_offsets[1]:.0f}, '
              f'{time.perf_counter() - started:.0f}s elapsed', flush=True)
        (root / 'result.partial.json').write_text(
            json.dumps(assembled, ensure_ascii=False, separators=(',', ':')) + '\n',
            encoding='utf-8')
        (root / 'progress.json').write_text(
            json.dumps(dict(groups=per_group, orders=len(assembled),
                            elapsed=round(time.perf_counter() - started, 1)),
                       ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    (root / 'result.json').write_text(
        json.dumps(assembled, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')

    # The assembled plan is scored against the WHOLE drop, not the chunks, so a
    # group left unsolved shows up as missing coverage rather than disappearing.
    summary = dict(groups=per_group)
    placed = {oid for batch in assembled for oid in batch['orders']}
    # `check` reads the round's raw CSVs (`orders_semi.csv`, `blank_used_finals.csv`)
    # from the directory it is handed, so it needs `--data` itself.  It also counts
    # every order in those files as required, so a run that solved a subset would
    # report the remainder as `order_coverage` / `short_delivery`.  Materialise a
    # scoped directory holding only the rows this run placed -- the same device
    # `smoke_semi.py` uses -- so the physical verdict describes the plan rather than
    # the scope.  Blanks are copied verbatim: they are a five-row catalogue indexed
    # by blank_type and must stay complete.
    if not args.skip_physical:
        scope = root / 'scoped_data'
        scope.mkdir(parents=True, exist_ok=True)
        src = Path(args.data) / 'orders_semi.csv'
        kept = []
        with src.open(encoding='gbk', errors='replace') as fh:
            kept.append(fh.readline())
            for line in fh:
                if line.split(',', 1)[0].strip() in placed:
                    kept.append(line)
        (scope / 'orders_semi.csv').write_text(''.join(kept), encoding='gbk')
        for name in ('blank_used_finals.csv', 'constraints.txt'):
            source = Path(args.data) / name
            if source.exists():
                (scope / name).write_bytes(source.read_bytes())
        physical = check(assembled, scope, round='semi')
        summary['physical_passed'] = physical['passed']
        summary['physical_errors'] = physical['error_counts']
    # `validate_plan` demands a scheme for every order it is given (clause 12), so it
    # is handed only the orders this run actually placed.  Passing `all_orders` would
    # raise "Missing orders" for every unsolved group, which is a statement about the
    # run's scope rather than a defect in the plan.  Coverage is measured by
    # `platform_score.evaluate` against the full drop and reported separately below.
    scope_orders = [o for o in all_orders if o.oid in placed]
    metrics = validate_plan(assembled, scope_orders, cfg, blanks)
    summary.update(plans=len(assembled), orders_placed=len(placed),
                   orders_total=len(all_orders), rounds=metrics['rounds'], knives=metrics['knives'],
                   yield_rate=round(metrics['yield_rate'], 6),
                   coverage=round(metrics['coverage'], 6))
    try:
        scoring = evaluate(assembled, Path(args.data), round_name='semi', baseline_knives=160000)
        summary.update(semi_coverage=round(scoring['coverage'], 6),
                       semi_violation_count=scoring['violation_count'],
                       semi_score_capped=round(scoring['score_capped'], 4))
    except Exception as exc:                                          # noqa: BLE001
        summary['score_error'] = f'{type(exc).__name__}: {exc}'


    (root / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n',
                                       encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
