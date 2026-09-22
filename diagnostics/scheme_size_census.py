"""Scheme-size census for one plan or chunk: how many orders actually got merged.

Read-only helper.  Coverage under the semi-final rule counts an order only when it
shares a *round* with another order, so the useful summary of a plan is not the
scheme count but the share of orders sitting in schemes of size > 1.  Accepts both
a plan array and a `solve_semi.py` chunk checkpoint (`{"complete": ..., "plan": [...]}`).

Usage:
    python -X utf8 diagnostics/scheme_size_census.py runs/semi_v2/result.json
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    path = Path(sys.argv[1])
    data = json.loads(path.read_text(encoding='utf-8'))
    plan = data['plan'] if isinstance(data, dict) else data

    sizes = Counter(len(b['orders']) for b in plan)
    rounds = Counter(len(b['length_scheme']) for b in plan)
    orders = sum(len(b['orders']) for b in plan)
    merged = sum(len(b['orders']) for b in plan if len(b['orders']) > 1)
    print(f'file: {path}')
    print(f'schemes={len(plan)}  orders={orders}  '
          f'orders_in_multi_order_schemes={merged} ({merged / max(1, orders):.1%})')
    print(f'scheme sizes      : {dict(sorted(sizes.items()))}')
    print(f'rounds per scheme : {dict(sorted(rounds.items()))}')


if __name__ == '__main__':
    main()
