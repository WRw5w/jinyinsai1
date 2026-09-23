"""Run `solve_semi.py` with the PRE-FIX round-shape enumeration.

The `offer()` guard, the `parallel` sweep direction and the `moves` tie-break in
`solver.py` were all inverted in the same way -- they kept the NARROW bed for a
fixed delivery, which is the expensive one.  `diagnostics/shape_dedup_probe.py`
and `diagnostics/round_shape_audit.py` measure that on the produced plan, but the
only honest way to price the fix is an A/B on the solver itself.

Rather than stashing the working tree (which would put the fixed file at risk of a
half-finished run), the legacy enumeration is taken verbatim from git:

    git show HEAD:solver.py > diagnostics/solver_prefix_snapshot.py

and injected into the live module.  `_round_moves` reaches the enumeration through
a module-global lookup, so rebinding `solver._enumerate_round_shapes` is enough;
`_ROUND_SHAPE_CACHE` is cleared so no fixed-table entry survives into the legacy
arm.

Run the two arms into separate output directories:

    python diagnostics/ab_legacy_driver.py \\
        --output runs/ab_before --chunk 60 --seconds-per-chunk 0
    python solve_semi.py \\
        --output runs/ab_after  --chunk 60 --seconds-per-chunk 0

`--seconds-per-chunk 0` matters: seeding runs with `deadline=None` and is
therefore deterministic, while the annealing phase is wall-clock boxed and is not.
With the annealing budget at zero both arms are pure construction, so any
difference is the enumeration and nothing else.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'diagnostics'))

import solver  # noqa: E402
import solver_prefix_snapshot as legacy  # noqa: E402


def main():
    solver._enumerate_round_shapes = legacy._enumerate_round_shapes
    solver._ROUND_SHAPE_CACHE.clear()
    print('[ab] legacy round-shape enumeration installed '
          f'(sweep ascending, guard `parallel >= old[0]`, tie-break `m[1]`)',
          flush=True)
    import solve_semi  # noqa: E402  (imported late so the patch is already in place)
    sys.argv = ['solve_semi.py'] + sys.argv[1:]
    solve_semi.main()


if __name__ == '__main__':
    main()
