"""Choose each scheme's blank type so the declared blank mass is smallest.

The platform's `blank_material` check is per ROUND: every round must declare
enough blank mass to cover `(net + 2) * count * linear`.  Because `blank_counts`
are whole blanks, each round wastes up to one blank of slack, and the waste is
bounded by the blank's WEIGHT -- so a lighter blank type wastes less.  The scheme
declares one `blank_type` for all its rounds, and picking the type that minimises
the declared mass is a small search over the five types.

Measured on `runs/_cand_rounded.json` (the clause-6-clean plan):

    original mix    yield 88.0464%   score 92.0120
    all type 2      yield 88.8187%   score 92.2436
    per scheme      yield 90.4428%   score 92.7309

The per-scheme choice beats "always the lightest" because what matters is the
total declared mass, not the per-round waste: a heavier blank can need fewer of
them and come out ahead.

Nothing else about the plan changes -- not the rounds, not the counts, not the
piece allocations -- so this composes with any other fix.  It is also honest:
`blank_type` is a declared plan parameter, any type is legal, and the mass check
is the only thing constraining the choice.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

for _extra in (str(Path(__file__).resolve().parents[2] / 'src'), str(Path(__file__).resolve().parent)):
    if _extra not in sys.path:
        sys.path.insert(0, _extra)

from platform_score import load_scoring_data                     # noqa: E402


def optimize(plan, linear, blank_weights, extra=1.0):
    """Rewrite blank_type / blank_counts in place; returns the plan."""
    changed = 0
    for scheme in plan:
        lin = linear[scheme['orders'][0]]
        pick = None
        for blank_type, weight in blank_weights.items():
            counts = [int(math.ceil((sum(r.values()) + 2) * c * lin / weight * extra))
                      for r, c in zip(scheme['length_scheme'], scheme['counts'])]
            declared = sum(counts) * weight
            if pick is None or declared < pick[0]:
                pick = (declared, blank_type, counts)
        if pick[1] != scheme['blank_type']:
            changed += 1
        scheme['blank_type'] = pick[1]
        scheme['blank_counts'] = pick[2]
    return plan, changed


def main():
    root = Path(r'D:/02_Projects/ML/jinyinsai1_nolimit')
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'runs/_cand_rounded.json'
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_name(src.stem + '_blankopt.json')
    extra = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
    data = load_scoring_data(root / 'data/semi', 'semi')
    plan = json.loads(Path(src).read_text(encoding='utf-8'))
    plan, changed = optimize(plan,
                             {o: x.physical_linear_weight for o, x in data.orders.items()},
                             dict(data.blank_weights), extra)
    out.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'changed': changed, 'of': len(plan), 'out': str(out)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
