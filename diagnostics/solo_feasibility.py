"""Is `B20270366` packable at all within 6 rounds under the semi-final rules?

The order is 674 pieces at 5.0 m on NP01:43, so `674 = 2 x 337` with 337 prime.  A
sweep over the whole drop said EVERY order fits 6 rounds, yet `_seed_group` raised
`ModelError` on this one.  Either the sweep's bound is too loose or `_solo_scheme`
is searching too narrow a family.

This settles it by exhaustive DP over the family `_solo_scheme` cannot express:
per-round `(k_i, p_i)` with `k_i` a free variable of each round, rather than one
shared `parallel` plus a near-equal split of `total_k`.

Constraints from `Model.make_round`, applied exactly rather than approximated:
    length_i = k_i * size + round_trim       in [min_bed_length, bed_length]
    mass_i   = length_i * p_i * linear       <= bed_weight
    p_i      <= parallel_limit
    sum(k_i * p_i) == pieces                 exact; max_overproduction_ratio is 0

A state is `(rounds used, pieces delivered)`; the DP is forward over rounds, so the
first layer that reaches `pieces` is the minimum round count.  The feasibility
verdict is a genuine proof about this restricted family, unlike the solver's
`ModelError`, which only reports that its own search space came up empty.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from solver import Config, load_orders  # noqa: E402

cfg = Config(continuity=True, coverage_shared=True, enforce_order_mass_floor=True,
             objective='platform_score', baseline_knives=160000)
orders = load_orders(str(ROOT / 'data/semi/orders.normalized.csv'), cfg, skip_invalid=True)

TARGET = sys.argv[1] if len(sys.argv) > 1 else 'B20270366'
order = next(o for o in orders if o.oid == TARGET)

size, pieces, linear = order.size, order.pieces, order.linear_weight
limit = int(cfg.bed_width_mm // order.diameter) if cfg.bed_width_mm else 46
max_k = int((cfg.bed_length - cfg.round_trim) // size)

print(f'{TARGET}: pieces={pieces} size={size} linear={linear:.4f}')
print(f'  max_k/round = {max_k} (length {max_k * size + cfg.round_trim} m), '
      f'parallel_limit = {limit}')
print()

# Enumerate the legal per-round outputs once: each is (k, p, produced, length, mass).
outputs = []
for k in range(1, max_k + 1):
    length = k * size + cfg.round_trim
    if length < cfg.min_bed_length - 1e-8:
        continue
    for p in range(1, limit + 1):
        mass = length * p * linear
        if mass > cfg.bed_weight + 1e-8:
            break                      # monotone in p
        if mass < cfg.min_bed_weight - 1e-8:
            continue
        outputs.append((k, p, k * p, length, mass))

print(f'legal per-round shapes: {len(outputs)}')
best_single = max(o[2] for o in outputs)
print(f'most a single round can deliver: {best_single} pieces '
      f'(at k={max((o for o in outputs if o[2] == best_single), key=lambda o: o[1])[0]}, '
      f'p={max((o for o in outputs if o[2] == best_single), key=lambda o: o[1])[1]})')
print(f'pieces needed: {pieces} -> round-count lower bound '
      f'{-(-pieces // best_single)}')
print()

# DP.  `parent` records (previous total, k, p) so a hit can be reconstructed.
layers = [{0: None}]
hit = None
for rnd in range(1, cfg.max_rounds + 1):
    current = {}
    for total in layers[-1]:
        for k, p, produced, _length, _mass in outputs:
            nxt = total + produced
            if nxt > pieces or nxt in layers[-1] or nxt in current:
                continue
            current[nxt] = (total, k, p)
            if nxt == pieces:
                hit = rnd
                break
        if hit:
            break
    layers.append(current)
    if hit:
        break
    if not current:
        break

if not hit:
    print(f'NOT solvable in {cfg.max_rounds} rounds by exhaustive DP over '
          f'per-round (k, p) -- a proof for this family, unlike the solver\'s own error')
    sys.exit(1)

# Reconstruct: walk the layers backwards.  `layers[rnd]` holds totals reachable
# AFTER `rnd` rounds, so the round that produced `pieces` is `hit`.
plan = []
node = pieces
for rnd in range(hit, 0, -1):
    prev, k, p = layers[rnd][node]
    plan.append((k, p, k * p))
    node = prev
plan.reverse()
print(f'SOLVABLE in {hit} rounds')
print(f'  per-round (k, parallel, delivered): {plan}')
print(f'  lengths: {[k * size + cfg.round_trim for k, _, _ in plan]}')
print(f'  masses:  {[round((k * size + cfg.round_trim) * p * linear, 1) for k, p, _ in plan]}')
print(f'  delivered: {sum(d for _, _, d in plan)}  (need {pieces})')
