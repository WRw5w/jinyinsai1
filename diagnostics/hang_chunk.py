"""Reproduce the seeding hang by replaying NP01:43 chunks in order.

`solve_semi.py` stalled after writing seven chunk files (@0..@360) and never
produced @420.  Running @420 alone finishes in ~35 s, so the trigger is the
ACCUMULATED state of a multi-chunk run rather than that chunk in isolation.
This replays the chunks in the driver's order with faulthandler armed, so a
stall dumps the exact Python stack instead of hanging silently.
"""
import faulthandler
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from solver import Config, load_orders, load_blanks  # noqa: E402
from solve_semi import pick_group, solve_chunk  # noqa: E402

LIMIT = float(sys.argv[1]) if len(sys.argv) > 1 else 120.0
START = int(sys.argv[2]) if len(sys.argv) > 2 else 0
faulthandler.dump_traceback_later(LIMIT, exit=True)

settings = json.loads((ROOT / 'data/semi/competition.config.json').read_text(encoding='utf-8'))
settings.update(continuity=True, coverage_shared=True, enforce_order_mass_floor=True,
                objective='platform_score', baseline_knives=160000)
cfg = Config(**settings)
all_orders = load_orders(str(ROOT / 'data/semi/orders.normalized.csv'), cfg, skip_invalid=True)
blanks = load_blanks(str(ROOT / 'data/semi/blanks.normalized.csv'))
members = pick_group(all_orders, 'NP01:43')

out = ROOT / 'runs' / 'hang_repro'
for offset in range(START, len(members), 60):
    chunk = members[offset:offset + 60]
    t0 = time.perf_counter()
    plan, info = solve_chunk(chunk, cfg, blanks, 20, 1 + offset, out / f'{offset:06d}.json')
    print(f'@{offset}: {len(plan)} batches in {time.perf_counter()-t0:.1f}s ({info})', flush=True)
print('ALL DONE', flush=True)
faulthandler.cancel_dump_traceback_later()
