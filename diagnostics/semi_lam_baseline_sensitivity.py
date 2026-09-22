"""Sensitivity of the lambda choice to the (unconfirmed) knife baseline.

Why this exists
---------------
`semi_hypothesis_table.py` prices the six `r` values under four *convention*
readings, but it holds **lambda fixed at the r-sweep optimum**, which was chosen
at `B = 90,000` only.  The knife subscore is `40 * min(1, B / K)`, so `B` scales
the *marginal value of a knife* linearly while the yield term is untouched: a
larger `B` buys more knives for the same yield sacrifice.  The optimal point of
the knife/yield trade-off therefore moves with `B`, and the delivered `lambda =
2400` is not automatically right if the semi-final baseline is not 90,000.

This script re-prices the whole lambda curve at several baselines and reports,
for each (convention, baseline) cell, the family optimum and the gap of the
delivered `lambda = 2400`.  No random numbers: the pairing is the same
deterministic `pair_group` the delivered plan used.

Usage:
    python -X utf8 diagnostics/semi_lam_baseline_sensitivity.py
"""
from __future__ import annotations

import dataclasses
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from platform_score import Rules, evaluate, score_components, row_segments  # noqa: E402
from solver import Config, load_blanks, load_orders, Model  # noqa: E402
import build_semi_plan as BSP  # noqa: E402
import round_shaper  # noqa: E402

DATA = ROOT / "data" / "semi"
LAMS = [0.0, 100.0, 200.0, 400.0, 800.0, 1200.0, 1600.0, 2400.0, 3200.0, 4800.0, 6400.0]
BASELINES = [90000.0, 160000.0, 180000.0]
CAP_RATIO = 0.10
ROUND_NAME = "semi"
DELIVERED_LAM = 2400.0


def build_model():
    settings = json.loads((DATA / "competition.config.json").read_text(encoding="utf-8"))
    settings["max_overproduction_ratio"] = CAP_RATIO
    settings.update(continuity=True, coverage_shared=True, enforce_order_mass_floor=True,
                    objective="platform_score", baseline_knives=BASELINES[0])
    cfg = Config(**settings)
    orders = load_orders(str(DATA / "orders.normalized.csv"), cfg, skip_invalid=True)
    blanks = load_blanks(str(DATA / "blanks.normalized.csv"))
    model = Model(orders, cfg, blanks)
    model.lookup = {o.oid: i for i, o in enumerate(orders)}
    return cfg, orders, model, blanks


def knives_nearest(plan, sizes):
    """Same round structure, segments read with `nearest` instead of `INT(L//s)`."""
    total = 0
    for scheme in plan:
        for row in scheme["length_scheme"]:
            total += sum(row_segments(L, sizes[oid], "nearest") for oid, L in row.items()) + 1
    return total


def main():
    cfg, orders, model, blanks = build_model()
    sizes = {o.oid: o.size for o in orders}

    started = time.perf_counter()
    all_schemes = []
    for key in BSP.group_keys(orders):
        ids = [i for i, o in enumerate(orders)
               if o.steel == key[0] and abs(o.diameter - key[1]) < 1e-9]
        schemes, _ = BSP.pair_group(model, cfg, ids, 4)
        all_schemes.extend(schemes)
    print(f"pairing: {len(all_schemes)} schemes in {time.perf_counter() - started:.0f}s",
          flush=True)

    rows = []
    for lam in LAMS:
        plan = [round_shaper._to_batch(ids, model, cfg,
                                       min(cands, key=lambda c: c["declared"] + lam * c["knives"]))
                if cands else None for ids, cands in all_schemes]
        plan = [b for b in plan if b is not None]
        # Price once: everything except the knife term is baseline-independent.
        sc = evaluate(plan, DATA, round_name=ROUND_NAME, baseline_knives=BASELINES[0])
        rows.append(dict(lam=lam, k_floor=sc["knives"], k_nearest=knives_nearest(plan, sizes),
                         finished=sc["finished_weight"], raw=sc["blank_weight"],
                         coverage=sc["coverage"], rounds=sc["rounds"],
                         viol=sc["violation_count"]))
        print(f"  lam={lam:<7g} K_floor={rows[-1]['k_floor']:>7,} "
              f"K_nearest={rows[-1]['k_nearest']:>7,} rounds={sc['rounds']:>6,} "
              f"yield={sc['yield_rate']:.10f}", flush=True)

    print("\n" + "=" * 100)
    print("lambda = 2400 vs the family optimum, per (convention, baseline)")
    print("=" * 100)
    print(f"  {'convention':<10}{'B':>9}  {'S(lam=2400)':>20}{'best lam':>10}"
          f"{'S(best)':>20}{'gap':>11}")
    verdict = []
    for conv, field in (("floor", "k_floor"), ("nearest", "k_nearest")):
        for B in BASELINES:
            rules = dataclasses.replace(Rules.semi(baseline_knives=B),
                                        segment_convention=conv)
            scored = []
            for rec in rows:
                sc = score_components(rec[field], rec["finished"], rec["raw"],
                                      rec["coverage"], rules)
                scored.append((sc["score_capped"], rec["lam"], rec["viol"]))
            scored.sort(reverse=True)
            cur = next(s for s in scored if s[1] == DELIVERED_LAM)
            gap = scored[0][0] - cur[0]
            print(f"  {conv:<10}{B:>9,.0f}  {cur[0]:>20.14f}{scored[0][1]:>10g}"
                  f"{scored[0][0]:>20.14f}{gap:>11.6f}")
            verdict.append((conv, B, scored[0][1], gap))

    print("\nverdict:")
    for conv, B, best_lam, gap in verdict:
        ok = "delivered lam=2400 IS optimal" if best_lam == DELIVERED_LAM \
            else f"delivered lam=2400 is NOT optimal (best {best_lam:g}, gap {gap:.6f})"
        print(f"  {conv:<8} B={B:>9,.0f} -> {ok}")

    out = ROOT / "diagnostics" / "semi_lam_baseline_sensitivity.json"
    out.write_text(json.dumps(dict(cap_ratio=CAP_RATIO, lams=LAMS, baselines=BASELINES,
                                   rows=rows, verdict=[
                                       dict(convention=c, baseline=B, best_lam=l, gap=g)
                                       for c, B, l, g in verdict]),
                              ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
