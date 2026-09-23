"""Assemble the best per-chunk plan across independent islands.

`solve_semi.py` solves each 60-order chunk on its own, and the chunks of a group partition
its member list, so two runs of the same drop differ ONLY chunk by chunk -- nothing
couples them.  That makes per-chunk selection sound: take whichever island found the
better plan for that chunk.

Candidates are compared with `_key`, the very function the runs optimised, handed the
offset set for that chunk.  Comparing WITHIN a chunk is exact because every run received
the same offsets for it (same anchor, same order slice), so the offsets cancel and `_key`
orders the candidates by the single global objective.  Comparing ACROSS chunks would be
invalid -- the offsets differ -- which is why the merge is per chunk and never global.

A chunk no run finished is backfilled from the anchor, so a partial island costs quality
on those chunks instead of coverage over the whole drop: unplaced orders are exactly the
coverage the platform scores.  Backfill only reuses anchor batches whose orders all fall
inside the chunk; a batch straddling a boundary would put an order in two schemes, so it
is reported instead of smuggled in.

    python diagnostics/merge_chunks.py \
        --runs runs/semi_anchor_v1 runs/semi_anchor_s2 runs/semi_anchor_60 \
        --output runs/semi_merged
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import platform_score as PS  # noqa: E402
import solver as S  # noqa: E402
from solve_semi import AnchorOffsets, group_keys, load_blanks, load_orders, order_info, pick_group  # noqa: E402


def semi_cfg(config_path):
    """The same `Config` `solve_semi.main` builds -- the offsets are filled in per chunk."""
    settings = json.loads(Path(config_path).read_text(encoding='utf-8'))
    settings.update(continuity=True, coverage_shared=True, enforce_order_mass_floor=True,
                    cap_yield_numerator=True, knife_convention='platform_floor',
                    objective='platform_score', baseline_knives=160000)
    settings.pop('offset_mode', None)
    return S.Config(**settings)


def read_candidates(run_dir, chunk):
    """`{(steel, dia, offset): plan}` for every COMPLETE chunk cache under `run_dir`."""
    found = {}
    chunks_dir = Path(run_dir) / 'chunks'
    if not chunks_dir.is_dir():
        return found
    for group_dir in sorted(chunks_dir.iterdir()):
        if not group_dir.is_dir():
            continue
        steel, _, dia = group_dir.name.rpartition('_')
        try:
            key = (steel, float(dia))
        except ValueError:
            continue
        for path in sorted(group_dir.glob('[0-9]*.json')):
            try:
                cached = json.loads(path.read_text(encoding='utf-8'))
            except json.JSONDecodeError:
                continue                       # truncated by a kill mid-write
            if cached.get('complete'):
                found[(key[0], key[1], int(path.stem))] = cached['plan']
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data', default='data/semi')
    ap.add_argument('--config', default='data/semi/competition.config.json')
    ap.add_argument('--anchor', default='runs/seed_fixed_all.json')
    ap.add_argument('--runs', nargs='+', required=True)
    ap.add_argument('--output', default='runs/semi_merged')
    ap.add_argument('--chunk', type=int, default=60, help='must match the runs --chunk')
    args = ap.parse_args()

    cfg = semi_cfg(args.config)
    data = Path(args.data)
    orders = load_orders(str(data / 'orders.normalized.csv'), cfg, skip_invalid=True)
    blanks = load_blanks(str(data / 'blanks.normalized.csv'))
    info = order_info(orders)
    blank_weights = {b.bid: b.weight for b in blanks}
    anchor_plan = json.loads(Path(args.anchor).read_text(encoding='utf-8'))
    anchor = AnchorOffsets(anchor_plan, info, blank_weights)

    cands = {run: read_candidates(run, args.chunk) for run in args.runs}
    print(f'anchor {args.anchor}: {len(anchor_plan)} batches')
    for run, found in cands.items():
        print(f'  {run}: {len(found)} complete chunks')
    print()

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    merged = []
    wins = Counter()
    gains = []
    backfilled = 0
    straddled = []
    for key in group_keys(orders):
        members = pick_group(orders, f'{key[0]}:{key[1]}')
        for offset in range(0, len(members), args.chunk):
            chunk = members[offset:offset + args.chunk]
            oids = {o.oid for o in chunk}
            got = anchor.offsets(chunk)
            cfg.knife_offset = got['knives']
            cfg.numerator_offset = got['numerator']
            cfg.raw_offset = got['raw']
            cfg.covered_offset = got['covered']
            cfg.coverage_total = float(len(orders))
            # Score candidates with the same `Model` + `_totals` + `_key` triple that
            # `search_10s` optimised, so "best" means best under the runs' own arithmetic
            # rather than under a reimplementation of it.  `_totals` takes `Batch`
            # objects, not the exported dicts, hence `_import_plan`.  Checked against
            # `AnchorOffsets.metrics` on the whole anchor: knives 175,731 and coverage
            # 9,998 agree exactly, and numerator/raw agree to float associativity.
            model = S.Model(chunk, cfg, blanks)
            anchor_inside = [b for b in anchor_plan
                             if set(b['orders']) and set(b['orders']) <= oids]
            anchor_chunk_key = -S._key(
                S._totals(S._import_plan(anchor_inside, model), model), cfg, len(chunk))[0]

            best = None
            best_score = None
            winner = None
            for run, found in cands.items():
                plan = found.get((key[0], key[1], offset))
                if not plan:
                    continue
                score = -S._key(S._totals(S._import_plan(plan, model), model), cfg, len(chunk))[0]
                if best_score is None or score > best_score:
                    best, best_score, winner = plan, score, run
            if best is not None:
                wins[winner] += 1
                gains.append(best_score - anchor_chunk_key)
                merged.extend(best)
                continue
            # Nothing solved this chunk: reuse the anchor, but only the batches that sit
            # entirely inside it -- a straddling batch would double-book an order.
            merged.extend(anchor_inside)
            backfilled += 1
            if any(set(b['orders']) & oids for b in anchor_plan
                   if not set(b['orders']) <= oids):
                straddled.append((key, offset))

    (out / 'result.json').write_text(
        json.dumps(merged, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')

    placed = {oid for batch in merged for oid in batch['orders']}
    report = dict(plans=len(merged), orders_placed=len(placed), orders_total=len(orders),
                  chunk_wins=dict(wins), chunks_backfilled=backfilled,
                  chunks_with_straddling_anchor_batches=len(straddled))
    if gains:
        report['mean_key_gain_vs_anchor'] = round(sum(gains) / len(gains), 6)
        report['sum_key_gain_vs_anchor'] = round(sum(gains), 4)
    try:
        scoring = PS.evaluate(merged, data, round_name='semi', baseline_knives=160000)
        report.update(semi_score_capped=round(scoring['score_capped'], 4),
                      semi_knives=scoring['knives'],
                      semi_coverage=round(scoring['coverage'], 6),
                      semi_violation_count=scoring['violation_count'])
    except Exception as exc:                                          # noqa: BLE001
        report['score_error'] = f'{type(exc).__name__}: {exc}'
    (out / 'merge_report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
