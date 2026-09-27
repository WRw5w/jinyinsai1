"""Per-batch (k, c) requantisation: spend knife strokes to land round masses just
above a blank multiple.

Why this phase exists.  `coarsen` and everything downstream fix each round's count
`c` at the bed-width cap and only then move pieces around, so every round's mass
sits wherever the piece lattice puts it -- on average ~1.6 t above the multiple it
is billed at (16.5M kg of the 540.3M bill).  Raising `c` is not what pays: knives
are `sum_k + rounds`, so `c` is free of knife cost, and a round whose mass lands
just above a multiple of the blank weight bills one blank less for the same
material.  What `c` is *not* free of is delivery -- `sum k_i * c_i` must still
reach the order's piece demand -- so cutting `c` buys its phase alignment with
extra pieces, and the trade is scored in kg-equivalents against the pinned
exchange rates:
    bill    1 kg        -> 0.4 * demand / bill^2   = 6.936e-8 points
    knife   1 stroke    -> 0.4 * 100 * 160000 / knives^2 = 1.88e-4 points
    one round            -> 1 knife + 2 * c * linear kg of declared trim

Search: simulated annealing per batch over (pieces per round, count per round,
number of rounds) with a penalty objective, so it can cross the feasibility
valleys that a move-only-if-feasible walk cannot.  State is a candidate only at
zero violation; see `_batch_v3.py` in runs/ for the single-batch prototype and
`runs/_probe_floor.py` for the per-batch prize accounting.  Random insertion of a
round rarely survives -- it opens the delivery ledger short by a whole round's
output -- so `--extra-rounds N` anneals from the N best `split_starts`, which
build the one-round-bought layout directly by moving half of one order's pieces
into a new round beside their own.

A scheme may hold at most six rounds (constraints.txt clause 4).  When the search
wants more, `split_scheme` cuts the round list at a boundary that shares no order
so rule 6/10 (an order lives in exactly one scheme, contiguous within it) holds.

Usage:
  # one worker slice, writes its patch file (safe to run several in parallel)
  python tools/analysis/requant_batches.py patch --plan runs/_cy1.json --slice 0/8
  # the whole one-round prize list, starting from a split (runs/_prize_list.py)
  python tools/analysis/requant_batches.py patch --plan runs/_cy1.json \
         --batches "$(cat runs/_prize_bis.txt)" --rmax 10 --extra-rounds 3 --out …
  # collect every patch in a directory into a new plan, splitting long schemes
  python tools/analysis/requant_batches.py merge --plan runs/_cy1.json \
         --patches artifacts/runs/requant --out artifacts/runs/requant/plan_rq.json
"""
import argparse
import copy
import json
import math
import random
import sys
from decimal import Decimal as D
from fractions import Fraction as F
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from platform_check import load_orders_and_blanks      # noqa: E402

# Pinned rates (see docs/SCORE_MODEL.md): both are marginal values at the current
# operating point, so they are only valid while a change stays a small fraction.
REF_BILL = F(540281579)
REF_KNIVES = F(184504)
REF_DEMAND = F(506157994)
KNIFE_KG = F(160000) * REF_BILL ** 2 / (REF_DEMAND * REF_KNIVES ** 2)
# The platform's third scoring term, per order: `platform_score.py` marks an order
# as combined only when some round carries it together with another order
# (`if len(set(scheme)) > 1: combined.update(scheme)`), so an order alone in every
# one of its rounds -- every piece delivered, still -- costs 0.2 * 100/9999 points.
# Priced in the same kg-eq as the rest of the objective, and added to the objective
# only: it is a scoring term, not a legality rule, so it must not touch `viol`.
# `shift_cuts.py` carries the same two prices as floats (COVER_BONUS_KG 30000,
# KNIFE_KG 2830); these are their exact fractions.
PTS_PER_KG = F(4, 10) * 100 * REF_DEMAND / REF_BILL ** 2
COVER_KG = F(2, 10) * 100 / (F(9999) * PTS_PER_KG)
MASS_CAP = F(60000)
NET_LO, NET_HI = F(48), F(148)
PEN = F(60)                 # kg-eq penalty per kg of violation


def ceil_frac(x):
    return -((-x.numerator) // x.denominator)


def bill(m, w):
    """Declared kg for material m at blank weight w (ROUND_CEILING, half-open)."""
    return ceil_frac((m - F(1, 10 ** 7)) / w) * w


def shared_orders(st):
    """Orders standing on some round beside at least one other order.

    That is the platform's coverage set (`platform_score.py` combines a round's
    orders only when the round carries more than one), so an order missing here
    costs COVER_KG however many pieces it delivers.
    """
    out = set()
    for kd, _ in st:
        if len(kd) > 1:
            out.update(kd)
    return out


def load_state(batch, sizes):
    """[(dict oid->pieces, count)] with the JSON key order preserved."""
    st = []
    for s, c in zip(batch['length_scheme'], batch['counts']):
        kd = {}
        for o, length in s.items():
            kd[o] = kd.get(o, 0) + int(F(str(length)) / sizes[o])
        st.append((kd, int(c)))
    return st


def batch_ctx(orders, b):
    h = b['orders'][0]
    lin = F(str(orders[h]['linear']))
    cap_c = int(2000 / float(orders[h]['dia']))
    sizes = {o: F(str(orders[o]['size'])) for o in b['orders']}
    dem = {o: int(orders[o]['pieces']) for o in b['orders']}
    return lin, cap_c, sizes, dem


def evaluate(st, w, sizes, dem, lin, cap_c):
    """(objective kg-eq, bill kg, knives, violation kg).

    Beyond the bed/weight/width/delivery limits this prices the two structural
    rules the checker enforces per scheme, because a state that breaks either is
    worth nothing however cheap its bill looks:
      clause 6  -- an order's rounds must be one contiguous block (a round list
                   with a hole in it cannot be cut into legal schemes either);
      seam      -- two adjacent rounds may share at most one order, since every
                   shared order must sit at both ends of the boundary at once.
    It also prices the platform's coverage term (COVER_KG per order that no round
    shares with another order), which is a scoring term rather than a legality
    rule: it lives in the objective alone, and the move that would strip it has to
    out-earn it.  Cheap layouts that strand an order alone in a round of its own
    are exactly what the joint count/piece sweep used to find.
    """
    billed = F(0)
    knives = 0
    viol = F(0)
    deliv = {o: 0 for o in dem}
    span = {}
    for i, (kd, c) in enumerate(st):
        if not kd:
            viol += F(100000)
            continue
        net = sum(F(k) * sizes[o] for o, k in kd.items())
        mass = (net + 2) * c * lin
        if mass > MASS_CAP:
            viol += (mass - MASS_CAP) * PEN
        if net < NET_LO:
            viol += (NET_LO - net) * c * lin * PEN
        elif net > NET_HI:
            viol += (net - NET_HI) * c * lin * PEN
        if c > cap_c:
            viol += F(5000)
        billed += bill(mass, w)
        knives += sum(kd.values()) + 1
        for o, k in kd.items():
            deliv[o] += k * c
            lo, hi, n = span.get(o, (i, i, 0))
            span[o] = (min(lo, i), max(hi, i), n + 1)
    for o, (lo, hi, n) in span.items():
        if hi - lo + 1 != n:
            viol += F(200000)                       # order broken into islands
    for left, right in zip(st, st[1:]):
        if len(set(left[0]) & set(right[0])) > 1:
            viol += F(200000)                       # boundary cannot close its seam
    # A round must END on the order it shares with its left neighbour and BEGIN on
    # the one it shares with its right neighbour; one order shared on both sides
    # needs the round to be that order alone.
    for i in range(1, len(st) - 1):
        a = set(st[i - 1][0]) & set(st[i][0])
        b = set(st[i][0]) & set(st[i + 1][0])
        if len(a) == 1 and len(b) == 1 and next(iter(a)) == next(iter(b)) \
                and len(st[i][0]) > 1:
            viol += F(200000)
    for o in dem:
        if deliv[o] < dem[o]:
            viol += F(dem[o] - deliv[o]) * sizes[o] * lin * PEN
    obj = billed + KNIFE_KG * knives + viol
    shared = shared_orders(st)
    for o in dem:
        if o not in shared:
            obj += COVER_KG
    return obj, billed, knives, viol


def snap_c(net, w, lin, cap_c):
    """Best count for a round whose net length is fixed: the c <= cap_c that both
    fits the 60 t bed and lands the mass closest above a blank multiple."""
    c_top = min(cap_c, int(MASS_CAP / ((net + 2) * lin)))
    if c_top < 1:
        return None
    best, bc = None, c_top
    for c in range(max(1, c_top - 12), c_top + 1):
        val = bill((net + 2) * c * lin, w) - (net + 2) * c * lin
        if best is None or val < best:
            best, bc = val, c
    return bc


def structural_ok(st):
    """The two rules `evaluate` prices as violations, checked on a raw state:
    clause 6 (an order's rounds are one contiguous block) and the seam rule (at
    most one shared order per boundary, and a round entered and left on the same
    order must be that order alone)."""
    span = {}
    for i, (kd, _) in enumerate(st):
        if not kd:
            return False
        for o in kd:
            lo, hi, n = span.get(o, (i, i, 0))
            span[o] = (min(lo, i), max(hi, i), n + 1)
    for lo, hi, n in span.values():
        if hi - lo + 1 != n:
            return False
    for left, right in zip(st, st[1:]):
        if len(set(left[0]) & set(right[0])) > 1:
            return False
    for i in range(1, len(st) - 1):
        a = set(st[i - 1][0]) & set(st[i][0])
        b = set(st[i][0]) & set(st[i + 1][0])
        if len(a) == 1 and len(b) == 1 and next(iter(a)) == next(iter(b)) \
                and len(st[i][0]) > 1:
            return False
    return True


def split_starts(st, sizes, w, lin, cap_c, pad=3):
    """States that buy a round by splitting one order's pieces off a round.

    The extra-round prize (see runs/_probe_floor.py: 1,064 batches, +0.243 pts)
    needs a round the annealer has to *construct*: inserting a random round leaves
    the delivery ledger short by that round's whole output, a valley the annealer
    crosses only by luck.  This builds the structure directly -- move half of one
    order's pieces out of round i into a new round right after it, and, when half
    an order does not reach the 48 m floor on its own, carry whole orders across
    with it.  The new round's count is snapped; its delivery roughly replaces what
    the parent loses, so the state starts near-feasible.  Candidates that would
    break a structural rule (a carried order whose other rounds leave it an
    island, a boundary sharing two orders, a round entered and left on one order
    that is not alone) are dropped here rather than handed to the annealer.
    """
    out = []
    for i, (kd, c) in enumerate(st):
        net = sum(F(k) * sizes[o] for o, k in kd.items())
        for o, k in sorted(kd.items()):
            if k < 2:
                continue
            k_move = k // 2
            net_new = k_move * sizes[o]
            net_par = net - k_move * sizes[o]
            moved = []
            for q in sorted((x for x in kd if x != o), key=lambda x: sizes[x]):
                if net_new >= NET_LO or len(moved) >= pad:
                    break
                if net_par - kd[q] * sizes[q] < NET_LO:
                    continue
                moved.append(q)
                net_new += kd[q] * sizes[q]
                net_par -= kd[q] * sizes[q]
            if net_new < NET_LO or net_par < NET_LO:
                continue
            cn = snap_c(net_new, w, lin, cap_c)
            if cn is None:
                continue
            cand = [(dict(x), y) for x, y in st]
            par = cand[i][0]
            par[o] = k - k_move
            for q in moved:
                del par[q]
            new = {o: k_move}
            new.update({q: kd[q] for q in moved})
            cand.insert(i + 1, (new, cn))
            if structural_ok(cand):
                out.append(cand)
    return out


def anneal(st0, w, sizes, dem, lin, cap_c, iters, seed, rmax=6, T0=40000.0, T1=60.0):
    rng = random.Random(seed)
    st = [(dict(kd), c) for kd, c in st0]
    cur = evaluate(st, w, sizes, dem, lin, cap_c)
    best_feas = None
    if cur[3] == 0:
        best_feas = (cur, [(dict(kd), c) for kd, c in st])
    T = T0
    decay = (T1 / T0) ** (1.0 / max(1, iters))
    oids = list(sizes)
    kw = dict(w=w, lin=lin, cap_c=cap_c)
    for _ in range(iters):
        T *= decay
        cand = [(dict(kd), c) for kd, c in st]
        op = rng.random()
        if op < 0.30:                                   # k +/- 1
            i = rng.randrange(len(cand))
            kd, c = cand[i]
            o = rng.choice(list(kd))
            d = 1 if rng.random() < 0.5 else -1
            if d < 0 and kd[o] == 1:
                continue
            kd[o] += d
        elif op < 0.50:                                 # count +/- 1
            i = rng.randrange(len(cand))
            kd, c = cand[i]
            c += 1 if rng.random() < 0.5 else -1
            if c < 1:
                continue
            cand[i] = (kd, c)
        elif op < 0.70:                                 # snap this round to its best phase
            i = rng.randrange(len(cand))
            kd, c = cand[i]
            net = sum(F(k) * sizes[o] for o, k in kd.items())
            nc = snap_c(net, **kw)
            if nc is None or nc == c:
                continue
            cand[i] = (kd, nc)
        elif op < 0.85:                                 # transfer n pieces j -> i
            if len(cand) < 2:
                continue
            j, i = rng.sample(range(len(cand)), 2)
            kdj, kdi = cand[j][0], cand[i][0]
            shared = [o for o in kdj if o in kdi]
            if not shared:
                continue
            o = rng.choice(shared)
            if kdj[o] <= 1:
                continue
            n = rng.randint(1, min(3, kdj[o] - 1))
            kdj[o] -= n
            kdi[o] = kdi.get(o, 0) + n
        elif op < 0.93:                                 # extend / trim a round's orders
            i = rng.randrange(len(cand))
            kd, c = cand[i]
            if rng.random() < 0.5:
                free = [o for o in sizes if o not in kd]
                if not free:
                    continue
                kd[rng.choice(free)] = rng.randint(1, 6)
            elif len(kd) >= 2:
                del kd[rng.choice(list(kd))]
        else:                                           # add / drop a round
            if rng.random() < 0.5 and len(cand) < rmax:
                cand.insert(rng.randrange(len(cand) + 1),
                            ({rng.choice(oids): rng.randint(1, 8)}, rng.randint(20, cap_c)))
            elif len(cand) > 1:
                cand.pop(rng.randrange(len(cand)))
        if any(not kd for kd, _ in cand):
            continue
        val = evaluate(cand, w, sizes, dem, lin, cap_c)
        d = float(val[0] - cur[0])
        if d <= 0 or rng.random() < math.exp(-d / T):
            st, cur = cand, val
            if cur[3] == 0 and (best_feas is None or cur[0] < best_feas[0][0]):
                best_feas = (cur, [(dict(kd), c) for kd, c in st])
    return best_feas


def best_w(st, sizes, dem, lin, cap_c, blanks):
    """(evaluate tuple, blank type, state) for the cheapest blank type here."""
    best = None
    for wi in blanks:
        w = F(str(blanks[wi]))
        val = evaluate(st, w, sizes, dem, lin, cap_c)
        if val[3] == 0 and (best is None or val[0] < best[0][0]):
            best = (val, wi, [(dict(kd), c) for kd, c in st])
    return best


def polish(st, w, sizes, dem, lin, cap_c, passes=12, delete=True, allow_illegal=False):
    """Deterministic best-improvement descent on the (k, count) state.

    The annealer's moves are random and most of them break delivery, so it only
    collects the local wins it happens to fall into; the bulk of a batch's
    sawtooth sits in combinations no random walk steps through.  This walks the
    same neighbourhood exhaustively instead -- every count up to the bed cap,
    every k +/- 1, every delivery-safe transfer between two rounds, and the
    deletion of a whole round -- and stops when a whole pass changes nothing.

    Deletion is the one move that changes the round count, so it is the only way
    to collect the knife a round costs (KNIFE_KG) on top of the ceil boundary its
    bill carries (~847 kg per round over the current plan).  Its pieces are
    re-homed by scaling each k up by c_j/c_i, which keeps delivery whole.

    Returns (value tuple, state) for the local optimum, or None when the input
    is not a legal state.  Never returns a state worse than the input.

    With `allow_illegal` the descent may start from a violating state (a kick that
    broke delivery or a seam) and repair it: it accepts the first legal state it
    finds, then continues as usual.  Callers must still check the returned
    violation count, since a start that cannot be repaired comes back as-is.
    """
    cur = evaluate(st, w, sizes, dem, lin, cap_c)
    if cur[3] > 0 and not allow_illegal:
        return None
    st = [(dict(kd), c) for kd, c in st]

    def better(cand):
        nonlocal cur, st
        val = evaluate(cand, w, sizes, dem, lin, cap_c)
        if val[3] == 0 and val[0] < cur[0]:
            cur, st = val, cand
            return True
        return False

    for _ in range(passes):
        moved = False
        for i in range(len(st)):                        # this round's count
            kd, c = st[i]
            for cc in range(1, cap_c + 1):
                if cc == c:
                    continue
                cand = [(dict(x), y) for x, y in st]
                cand[i] = (dict(kd), cc)
                if better(cand):
                    moved = True
        for i in range(len(st)):                        # this round's k, one order
            kd, c = st[i]
            for o in list(kd):
                for d in (1, -1):
                    if d < 0 and kd[o] == 1:
                        continue
                    cand = [(dict(x), y) for x, y in st]
                    cand[i][0][o] += d
                    if better(cand):
                        moved = True
        for i in range(len(st)):                        # pieces j -> i
            for j in range(len(st)):
                if i == j:
                    continue
                for o in list(st[j][0]):
                    if o not in st[j][0]:
                        continue
                    for n in range(1, st[j][0][o] + 1):
                        cand = [(dict(x), y) for x, y in st]
                        cand[j][0][o] -= n
                        if not cand[j][0][o]:
                            del cand[j][0][o]
                        cand[i][0][o] = cand[i][0].get(o, 0) + n
                        if better(cand):
                            moved = True
                            break
        # Joint count/piece move.  Dropping a piece alone breaks the delivery
        # floor and raising a count alone crosses a bill boundary, but together
        # they can hold the bill and hand back a knife: measured on bi=666, c
        # 72->75 with B20275702 8->7 keeps bill 55,462 while knives go 119->118.
        # For each round, order and count step the best drop is forced -- every
        # dropped piece is one knife (2,710) and shrinks the mass -- so one
        # candidate per (j, o, dc) is enough; the float screen below only decides
        # whether it is worth the exact evaluation `better` does.
        fl_sizes = {o: float(sizes[o]) for o in sizes}
        fl_lin, fl_w = float(lin), float(w)
        fl_knife = float(KNIFE_KG)
        net_r, mass_r, bill_r = [], [], []
        deliv_o = {o: 0 for o in dem}
        for kd, c in st:
            net = sum(fl_sizes[o] * k for o, k in kd.items())
            net_r.append(net)
            mass_r.append((net + 2) * c * fl_lin)
            bill_r.append(math.ceil(((net + 2) * c * fl_lin - 1e-7) / fl_w) * fl_w)
            for o, k in kd.items():
                deliv_o[o] += k * c
        for j in range(len(st)):
            if j >= len(st) or not st[j][0]:
                continue
            cj = st[j][1]
            for o in list(st[j][0]):
                if j >= len(st) or o not in st[j][0]:
                    continue
                kj = st[j][0][o]
                need = dem[o] - (deliv_o[o] - kj * cj)
                hit = False
                for dc in list(range(1, cap_c - cj + 1)) + list(range(-1, -cj, -1)):
                    cc = cj + dc
                    if dc > 0:
                        dn = min(kj, kj - -(-need // cc))
                        if dn < 1:
                            continue
                    else:
                        dn = kj - -(-need // cc)
                        if dn > -1:
                            continue
                    kk = kj - dn
                    if kk < 0:
                        continue
                    net = net_r[j] - dn * fl_sizes[o]
                    alive = kk > 0 or len(st[j][0]) > 1
                    if alive and (net < 48 or net > 148):
                        continue
                    if not alive and len(st) < 2:
                        continue
                    mass = (net + 2) * cc * fl_lin
                    if alive and mass > 60000:
                        continue
                    bill_new = math.ceil((mass - 1e-7) / fl_w) * fl_w
                    if -dn * fl_knife + bill_r[j] - bill_new > -0.5:
                        continue                       # cannot beat the knife cost
                    cand = [(dict(x), y) for x, y in st]
                    if kk:
                        cand[j][0][o] = kk
                    else:
                        del cand[j][0][o]
                    cand[j] = (cand[j][0], cc)
                    if not cand[j][0] and len(cand) >= 2:
                        del cand[j]                     # the order was the round
                    if better(cand):
                        moved = hit = True
                        break
                if hit:
                    break
        for j in range(len(st)):                        # delete a whole round
            if not delete or len(st) < 2 or j >= len(st):
                continue
            for i in range(len(st)):                    # every piece into one round
                if i == j or i >= len(st) or len(st) < 2 or j >= len(st):
                    continue
                kd, cj = st[j]
                c_i = st[i][1]
                cand = [(dict(x), y) for x, y in st]
                for o, kj in kd.items():
                    cand[i][0][o] = cand[i][0].get(o, 0) + -(-kj * cj // c_i)
                del cand[j]
                if better(cand):
                    moved = True
            if len(st) < 2 or j >= len(st):             # or each order to its own
                continue
            kd, cj = st[j]
            cand = [(dict(x), y) for x, y in st]
            masses = []
            for x, c in cand:
                masses.append((sum(F(k2) * sizes[o2] for o2, k2 in x.items()) + 2) * c * lin)
            del cand[j]
            del masses[j]
            for o, kj in kd.items():
                pick, pick_add = None, None
                for ii, (x, c) in enumerate(cand):
                    need = -(-kj * cj // c)
                    add = bill(masses[ii] + need * sizes[o] * c * lin, w) - bill(masses[ii], w)
                    if pick_add is None or add < pick_add:
                        pick, pick_add = ii, add
                need = -(-kj * cj // cand[pick][1])
                cand[pick][0][o] = cand[pick][0].get(o, 0) + need
            if better(cand):
                moved = True
        if not moved:
            break
    return cur, st


def kick(st, sizes, lin, cap_c, rng):
    """A random, structure-breaking perturbation of a state (or None).

    The descent is exhaustive inside its own neighbourhood, so the only way off a
    local optimum is to leave it: this changes the round count or a count without
    asking whether the change helps, and `polish` then re-optimizes (the caller
    keeps the result only when it beats the incumbent).  Delivery is deliberately
    allowed to break here -- the descent repairs it -- which is what makes a
    round-dropping kick reachable at all, since every legal state must deliver.
    """
    st = [(dict(kd), c) for kd, c in st]
    moves = [0, 1, 2, 3, 4]
    rng.shuffle(moves)
    for mv in moves:
        if mv == 0 and len(st) >= 2:                    # drop a round
            del st[rng.randrange(len(st))]
            return st
        if mv == 1 and len(st) < 6:                     # copy a round
            j = rng.randrange(len(st))
            st.insert(j, (dict(st[j][0]), st[j][1]))
            return st
        if mv == 2:                                     # re-count a round
            j = rng.randrange(len(st))
            st[j] = (st[j][0], rng.randint(1, cap_c))
            return st
        if mv == 3:                                     # pieces j -> i
            if len(st) < 2:
                continue
            j, i = rng.sample(range(len(st)), 2)
            kd = st[j][0]
            if not kd:
                continue
            o = rng.choice(list(kd))
            n = rng.randint(1, kd[o])
            kd[o] -= n
            if not kd[o]:
                del kd[o]
            st[i][0][o] = st[i][0].get(o, 0) + n
            if not kd and len(st) >= 2:
                del st[j]
            return st
        if mv == 4:                                     # nudge one k
            j = rng.randrange(len(st))
            kd = st[j][0]
            if not kd:
                continue
            o = rng.choice(list(kd))
            kd[o] += rng.choice((-2, -1, 1, 1, 2))
            if kd[o] <= 0:
                del kd[o]
                if not kd and len(st) >= 2:
                    del st[j]
            return st
    return None


def iterated_polish(st, w, sizes, dem, lin, cap_c, kicks, rng):
    """`polish` plus `kicks` rounds of kick-then-re-descend, kept only when better.

    Returns (value, state) for the best state seen, the polished input included,
    so the result is never worse than the plain descent.
    """
    best = polish(st, w, sizes, dem, lin, cap_c)
    if best is None:
        return None
    val = best[0][0]
    for _ in range(kicks):
        cand = kick(best[1], sizes, lin, cap_c, rng)
        if cand is None:
            continue
        got = polish(cand, w, sizes, dem, lin, cap_c, allow_illegal=True)
        if got is not None and got[0][3] == 0 and got[0][0] < val:
            best, val = got, got[0][0]
    return best


def improve_batch(orders, blanks, b, iters, seeds, rmax, w_try=2, seed_base=0,
                  extra=0, kicks=0, kick_seed=0):
    """Best zero-violation state found for this batch, or None."""
    lin, cap_c, sizes, dem = batch_ctx(orders, b)
    st0 = load_state(b, sizes)
    base = best_w(st0, sizes, dem, lin, cap_c, blanks)
    if base is None:
        base = (evaluate(st0, F(str(blanks[b['blank_type']])), sizes, dem, lin, cap_c),
                b['blank_type'], st0)
    best = base

    def offer(state):
        """Score a finished state at its own best blank type, keep it if better."""
        nonlocal best
        alt = best_w(state, sizes, dem, lin, cap_c, blanks)
        if alt is not None and alt[0][0] < best[0][0]:
            best = alt

    def offer_polished(state, w):
        """Descent from a state (already at blank type w), then score what it found."""
        pol = polish(state, w, sizes, dem, lin, cap_c)
        if pol is not None:
            offer(pol[1])

    offer_polished(st0, F(str(blanks[base[1]])))

    # try the w_try cheapest blank types: the phase maths changes with w
    w_order = sorted(blanks, key=lambda wi: evaluate(st0, F(str(blanks[wi])), sizes,
                                                     dem, lin, cap_c)[0])[:w_try]
    for wi in w_order:
        w = F(str(blanks[wi]))
        for seed in range(seeds):
            got = anneal(st0, w, sizes, dem, lin, cap_c,
                         iters, seed_base + seed + 1000 * wi, rmax=rmax)
            if got is not None:
                offer(got[1])
                offer_polished(got[1], w)
        if extra:
            # The extra-round prize needs a structure the random walk rarely
            # builds: start from the top `extra` splits, so the budget goes into
            # tuning a round-bought layout instead of crossing into one.
            starts = split_starts(st0, sizes, w, lin, cap_c)
            starts.sort(key=lambda s: evaluate(s, w, sizes, dem, lin, cap_c)[0])
            for si, s0 in enumerate(starts[:extra]):
                offer_polished(s0, w)
                got = anneal(s0, w, sizes, dem, lin, cap_c, max(1, iters // 2),
                             seed_base + 7000 + si + 100 * wi, rmax=rmax)
                if got is not None:
                    offer(got[1])
                    offer_polished(got[1], w)
    if kicks:
        # The descent is a fixpoint by now, so only a kick can move it: kick,
        # re-descend, keep what beats the incumbent, repeat.
        ils = iterated_polish(best[2], F(str(blanks[best[1]])), sizes, dem, lin,
                              cap_c, kicks, random.Random(kick_seed))
        if ils is not None and ils[0][3] == 0:
            offer(ils[1])
    if best[0][0] >= base[0][0]:
        return None
    return dict(val=best[0], w=best[1], state=best[2], base=base, lin=lin, sizes=sizes)


def seam_keys(rounds):
    """Per-round key order that closes every seam, for rounds sharing <=1 order.

    Right to left: at each boundary the shared order goes last in the left round
    and first in the right one.  Moving it to the front of the right round cannot
    disturb the boundary on the far side of it (that one constrains that round's
    LAST key), and `evaluate` has already rejected rounds shared on both sides.
    """
    keys = [list(r) for r in rounds]
    for j in range(len(keys) - 2, -1, -1):
        shared = [o for o in keys[j] if o in keys[j + 1]]
        if len(shared) != 1:
            continue
        o = shared[0]
        keys[j] = [x for x in keys[j] if x != o] + [o]
        keys[j + 1] = [o] + [x for x in keys[j + 1] if x != o]
    return keys


def state_to_fields(state, w, sizes, orders, lin):
    """plan fields for one batch's rounds: length_scheme / counts / blank_counts."""
    fields = {'length_scheme': [], 'counts': [], 'blank_counts': []}
    wF, linF = F(str(w)), F(str(lin))
    keys = seam_keys([list(kd) for kd, _ in state])
    for (kd, c), order in zip(state, keys):
        fields['length_scheme'].append(
            {o: float(F(str(orders[o]['size'])) * kd[o]) for o in order})
        fields['counts'].append(int(c))
        net = sum(F(k) * sizes[o] for o, k in kd.items())
        m = (net + 2) * c * linF
        fields['blank_counts'].append(ceil_frac((m - F(1, 10 ** 7)) / wF))
    return fields


def split_scheme(batch):
    """Cut a scheme's rounds into <=6-round chunks at order-free boundaries.

    Returns a list of scheme dicts, or None when no legal cut exists (an order
    spanning every candidate boundary).  Rule 6/10: an order lives in one scheme
    and must stay contiguous inside it, so a chunk boundary may not cross an
    order; with `length_scheme` key order fixed, a boundary is free exactly when
    the two rounds share no order.
    """
    rounds = batch['length_scheme']
    if len(rounds) <= 6:
        return [batch]
    cuts, start = [], 0
    while len(rounds) - start > 6:
        for j in range(start + 6, start, -1):        # latest legal cut first
            if not (set(rounds[j - 1]) & set(rounds[j])):
                cuts.append(j)
                start = j
                break
        else:
            return None
    cuts.append(len(rounds))
    out, start = [], 0
    for end in cuts:
        chunk = {}
        for k in ('orders', 'length_scheme', 'counts', 'blank_counts'):
            chunk[k] = copy.deepcopy(batch[k][start:end] if k != 'orders' else [])
        for s in batch['length_scheme'][start:end]:
            for o in s:
                if o not in chunk['orders']:
                    chunk['orders'].append(o)
        chunk['blank_type'] = batch['blank_type']
        out.append(chunk)
        start = end
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('patch')
    p.add_argument('--plan', default='runs/_cy1.json')
    p.add_argument('--data', default='data/semi')
    p.add_argument('--round', default='semi')
    p.add_argument('--out', required=True)
    p.add_argument('--slice', default='0/1', help='i/N: this worker takes bi %% N == i')
    p.add_argument('--batches', help='explicit comma list, overrides --slice')
    p.add_argument('--iters', type=int, default=20000)
    p.add_argument('--seeds', type=int, default=2)
    p.add_argument('--rmax', type=int, default=8)
    p.add_argument('--w-try', type=int, default=2)
    p.add_argument('--seed-base', type=int, default=0,
                   help='offset every seed, so a re-run walks a new trajectory')
    p.add_argument('--extra-rounds', type=int, default=0,
                   help='also anneal from the N best one-round splits of this batch')
    p.add_argument('--kicks', type=int, default=0,
                   help='kicked restarts of the descent, kept when they beat it')
    p.add_argument('--kick-seed', type=int, default=0)

    m = sub.add_parser('merge')
    m.add_argument('--plan', default='runs/_cy1.json')
    m.add_argument('--data', default='data/semi')
    m.add_argument('--round', default='semi')
    m.add_argument('--patches', required=True, help='directory or patch json files')
    m.add_argument('--out', required=True)
    args = ap.parse_args()

    plan = json.load(open(args.plan, encoding='utf-8'))
    orders, blanks, excluded, spec = load_orders_and_blanks(Path(args.data), args.round)

    if args.cmd == 'patch':
        if args.batches:
            sel = [int(x) for x in args.batches.split(',')]
        else:
            i, n = (int(x) for x in args.slice.split('/'))
            sel = [bi for bi in range(len(plan)) if bi % n == i]
        patch = {}
        n_imp = 0
        for bi in sel:
            b = plan[bi]
            got = improve_batch(orders, blanks, b, args.iters, args.seeds, args.rmax,
                                args.w_try, args.seed_base, args.extra_rounds,
                                args.kicks, args.kick_seed)
            if got is None:
                continue
            n_imp += 1
            patch[str(bi)] = dict(
                blank_type=got['w'],
                val=str(got['val'][0]),
                base=str(got['base'][0][0]),
                rounds=[[{o: k for o, k in kd.items()}, c] for kd, c in got['state']])
            print(f'bi={bi:5} w {got["base"][1]}->{got["w"]} '
                  f'{int(got["base"][0][1]):,} -> {int(got["val"][1]):,} kg bill, '
                  f'knives {got["base"][0][2]} -> {got["val"][2]}, '
                  f'obj {int(got["base"][0][0]) - int(got["val"][0]):+,} kg-eq', flush=True)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        json.dump(patch, open(args.out, 'w', encoding='utf-8'), ensure_ascii=False)
        print(f'slice {args.slice}: {n_imp}/{len(sel)} batches improved -> {args.out}',
              flush=True)
        return

    # merge
    paths = sorted(Path(args.patches).glob('*.json')) if Path(args.patches).is_dir() \
        else [Path(x) for x in args.patches.split(',')]

    def ctx_of(bi):
        """(sizes, dem, lin, cap_c) for one batch, in `evaluate`'s argument order."""
        lin, cap_c, sizes, dem = batch_ctx(orders, plan[bi])
        return sizes, dem, lin, cap_c

    patch = {}
    for path in paths:
        part = json.load(open(path, encoding='utf-8'))
        for k, v in part.items():
            # Re-score each candidate with this build's objective before comparing:
            # a patch written by an older revision (one that predates the coverage
            # price, say) carries a `val` this build would not agree with, and the
            # cheapest-looking stale entry would otherwise win the batch.
            v = dict(v)
            v['val'] = str(evaluate([(dict(kd), c) for kd, c in v['rounds']],
                                    F(str(blanks[int(v['blank_type'])])),
                                    *ctx_of(int(k)))[0])
            if k in patch and F(patch[k]['val']) <= F(v['val']):
                continue
            patch[k] = v
    out = []
    n_split = n_skip = n_flat = 0
    for bi, b in enumerate(plan):
        e = patch.get(str(bi))
        if e is None:
            out.append(b)
            continue
        lin, cap_c, sizes, dem = batch_ctx(orders, b)
        # A patch is only ever emitted for a batch it improves on, but that verdict
        # was reached by the revision that wrote it: re-check it against the base
        # here too, so a patch from an older objective cannot ride in on a price
        # this build no longer charges.
        if not F(e['val']) < evaluate(load_state(b, sizes),
                                      F(str(blanks[int(b['blank_type'])])), sizes,
                                      dem, lin, cap_c)[0]:
            n_flat += 1
            out.append(b)
            continue
        fields = state_to_fields([(dict(kd), c) for kd, c in e['rounds']],
                                 blanks[int(e['blank_type'])], sizes, orders, lin)
        nb = dict(b)
        nb.update(fields)
        nb['blank_type'] = int(e['blank_type'])
        parts = split_scheme(nb)
        if parts is None:
            n_skip += 1
            print(f'bi={bi:5} skipped: no order-free cut for {len(nb["counts"])} rounds')
            out.append(b)
            continue
        if len(parts) > 1:
            n_split += 1
        out.extend(parts)
    json.dump(out, open(args.out, 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'merge: {len(paths)} patch files, {len(patch)} improved batches, '
          f'{n_split} split, {n_skip} skipped for legality, {n_flat} not better than '
          f'the base -> {args.out} ({len(out)} schemes)')


if __name__ == '__main__':
    main()
