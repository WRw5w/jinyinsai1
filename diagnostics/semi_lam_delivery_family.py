"""Sensitivity of the lambda choice to the knife baseline, on the DELIVERED family.

Why this exists
---------------
`semi_lam_baseline_sensitivity.py` answered the same question on the **old**
shaper family (`build_semi_plan.pair_group` + `round_shaper`), whose delivered
point was `lambda = 2400` and `r = 0.10`.  That family no longer ships: the
delivery path is `build_semi_plan2.py` + `continuity_shaper.py` at `r = 0.25`,
and its chosen point is `lambda = 4800` (interior optimum of the 12-point grid
at `B = 160,000`).  The old table therefore no longer describes the delivered
plan, and this script re-derives the table on the delivered family.

Mechanism (unchanged, and the reason the question keeps coming back)
-------------------------------------------------------------------
The knife subscore is `40 * min(1, B / K)` while the yield term does not depend
on `B`, so a larger `B` linearly raises the value of a saved knife: the optimum
of the knife/yield trade-off moves **right** (save more knives, spend more
material) until `B >= K`, after which the knife term caps and the objective
falls back to pure yield, pushing the optimum **left** again.

Method
------
Re-uses `runs/cand25.json`, the `--dump-candidates` output of the delivered
build: every group's Pareto set of `(knives, declared, batch)` candidates,
produced by the identical deterministic bucketing + `continuity_shaper`
shaping.  Each `lambda` only re-selects one candidate per group, so the whole
curve is repriced without rebuilding.  No random numbers anywhere.

Usage:
    python -X utf8 build_semi_plan2.py --out runs/cont_dump25 --sweep \\
        --cap-ratio 0.25 --jobs 0 --dump-candidates runs/cand25.json
    python -X utf8 diagnostics/semi_lam_delivery_family.py
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

DATA = ROOT / "data" / "semi"
CANDIDATES = ROOT / "runs" / "cand25.json"
OUT = ROOT / "diagnostics" / "semi_lam_delivery_family.json"

LAMS = [0.0, 100.0, 200.0, 400.0, 800.0, 1200.0, 1600.0, 2400.0,
        3200.0, 4800.0, 6400.0, 9600.0]
BASELINES = [90000.0, 160000.0, 180000.0]
CAP_RATIO = 0.25
ROUND_NAME = "semi"
# What the delivered package actually uses (both the grid and the sweep agree).
DELIVERED_LAM = 4800.0
PRICE_BASELINE = 90000.0   # scoring anchor only; everything but the knife term is B-free


def build_model():
    settings = json.loads((DATA / "competition.config.json").read_text(encoding="utf-8"))
    settings["max_overproduction_ratio"] = CAP_RATIO
    settings.update(continuity=True, coverage_shared=True, enforce_order_mass_floor=True,
                    objective="platform_score", baseline_knives=PRICE_BASELINE)
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
    if not CANDIDATES.exists():
        raise SystemExit(
            f"missing {CANDIDATES}\n"
            "build it first:\n"
            "  python -X utf8 build_semi_plan2.py --out runs/cont_dump25 --sweep "
            "--cap-ratio 0.25 --jobs 0 --dump-candidates runs/cand25.json")

    cfg, orders, model, blanks = build_model()
    sizes = {o.oid: o.size for o in orders}
    payload = json.loads(CANDIDATES.read_text(encoding="utf-8"))
    cand_groups = payload["groups"]
    print(f"loaded candidates for {len([g for g in cand_groups if g])} groups "
          f"from {CANDIDATES.name}", flush=True)

    started = time.perf_counter()
    rows = []
    for lam in LAMS:
        plan = []
        for item in cand_groups:
            if not item:
                continue
            plan.append(min(item, key=lambda c: c["declared"] + lam * c["knives"])["batch"])
        # Price once: everything except the knife term is baseline-independent.
        sc = evaluate(plan, DATA, round_name=ROUND_NAME, baseline_knives=PRICE_BASELINE)
        rows.append(dict(lam=lam, k_floor=sc["knives"], k_nearest=knives_nearest(plan, sizes),
                         finished=sc["finished_weight"], raw=sc["blank_weight"],
                         coverage=sc["coverage"], rounds=sc["rounds"],
                         yield_rate=sc["yield_rate"], viol=sc["violation_count"]))
        print(f"  lam={lam:<7g} K_floor={rows[-1]['k_floor']:>7,} "
              f"K_nearest={rows[-1]['k_nearest']:>7,} rounds={sc['rounds']:>6,} "
              f"yield={sc['yield_rate']:.10f}", flush=True)
    print(f"repriced {len(LAMS)} lambda points in {time.perf_counter() - started:.0f}s",
          flush=True)

    print("\n" + "=" * 104)
    print(f"lambda = {DELIVERED_LAM:g} vs the family optimum, per (convention, baseline)"
          "   [delivered family: continuity_shaper, r=0.25]")
    print("=" * 104)
    print(f"  {'convention':<10}{'B':>9}  {'S(lam=%g)' % DELIVERED_LAM:>20}{'best lam':>10}"
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
        ok = (f"delivered lam={DELIVERED_LAM:g} IS optimal" if best_lam == DELIVERED_LAM
              else f"delivered lam={DELIVERED_LAM:g} is NOT optimal "
                   f"(best {best_lam:g}, gap {gap:.6f})")
        print(f"  {conv:<8} B={B:>9,.0f} -> {ok}")

    OUT.write_text(json.dumps(dict(cap_ratio=CAP_RATIO, lams=LAMS, baselines=BASELINES,
                                   delivered_lam=DELIVERED_LAM,
                                   candidates=CANDIDATES.name,
                                   rows=rows, verdict=[
                                       dict(convention=c, baseline=B, best_lam=l, gap=g)
                                       for c, B, l, g in verdict]),
                              ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
