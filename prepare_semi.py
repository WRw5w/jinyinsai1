"""Normalize the semi-final (复赛) CSVs without modifying the source files.

Differences from the preliminary round (prepare_data.py):

* Encoding.  The semi-final drop is GBK for both CSVs; `constraints.txt` is
  UTF-8 **with BOM**.  `orders_semi.csv` and `blank_used_finals.csv` both carry
  the GBK bytes, so they must NOT be opened as utf-8-sig.
* Column names.  Orders use `订单号,订单重量,钢种,规格,定尺长度` (the size is the
  last column, not `订单定尺(mm)`), and blanks use `编号,钢坯长度,钢坯宽度,钢坯定尺`.
  The blank header's first dimension is mislabelled: the column called
  `钢坯长度` holds a width-scale millimetre value (260, 250, 210, 250, 320) while
  `钢坯宽度` holds the other planar dimension.  Only the product of the two
  planar dimensions enters the mass, so the swap is immaterial, but we record
  it so nobody later "fixes" one of them.
* Anomaly predicate.  The preliminary round had one order with a negative
  length (A20260949, -1100 mm).  The semi-final round instead has one order
  with a negative *weight* but a perfectly legal length: B20270281, -25.418 t.
  The predicate is therefore generalised from `size <= 0` to "any of the four
  numeric fields is non-positive", which covers both rounds with one rule.
* Round cap.  constraints.txt fixes it at 6, and the objective weights are kept
  configurable because the PDF (40/30/20/10 with a separate time subscore) and
  the group Q&A (time folded into yield) disagree -- see RULES.md.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

DENSITY = 9860  # Explicit in the source header: 9.86 g/cm^3.

# ---------------------------------------------------------------------------
# Column-name aliases.  The preliminary files and the semi-final files disagree
# on every header, and the semi-final pairs `规格` with both a diameter (orders)
# and a planar width (blanks), so each loader carries its own alias set.
# ---------------------------------------------------------------------------
ORDER_ALIASES = {
    'oid': ('订单号', 'order_id', '订单编号', 'id'),
    'weight': ('订单重量', 'weight', '重量', '需求重量'),
    'steel': ('钢种', '坯料钢种', '订单钢种', 'steel'),
    'diameter': ('规格', '订单直径(mm)', '直径', 'diameter'),
    'size': ('定尺长度', '订单定尺(mm)', '定尺', 'length', 'size'),
}
BLANK_ALIASES = {
    'bid': ('编号', '坯料', 'blank_type', 'blank_id', 'id'),
    'planar_a': ('钢坯长度', '宽度mm', 'width', '宽度'),
    'planar_b': ('钢坯宽度', '厚度mm', 'thickness', '厚度'),
    'length': ('钢坯定尺', '长度mm', 'length', '长度'),
}


def _pick(row, names, required=True):
    for name in names:
        value = row.get(name)
        if value not in (None, ''):
            return value.strip()
    if required:
        raise ValueError(f'none of these columns present: {names}')
    return None


def _read_csv(path, encoding):
    with Path(path).open(encoding=encoding, newline='') as stream:
        return list(csv.DictReader(stream))


def _detect_encoding(path, candidates=('utf-8-sig', 'gbk')):
    raw = Path(path).read_bytes()
    for encoding in candidates:
        try:
            raw.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    raise ValueError(f'cannot decode {path} with {candidates}')


def prepare(orders_path, blanks_path, root=Path('data/semi')):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    orders_encoding = _detect_encoding(orders_path)
    blanks_encoding = _detect_encoding(blanks_path)
    rejected, valid = [], 0

    with (root / 'orders.normalized.csv').open('w', encoding='utf-8', newline='') as dst:
        writer = csv.writer(dst)
        writer.writerow(['order_id', 'steel', 'diameter', 'length', 'weight', 'density'])
        for line, row in enumerate(_read_csv(orders_path, orders_encoding), 2):
            try:
                oid = _pick(row, ORDER_ALIASES['oid'])
                steel = _pick(row, ORDER_ALIASES['steel'])
                diameter = float(_pick(row, ORDER_ALIASES['diameter']))
                size = float(_pick(row, ORDER_ALIASES['size'])) / 1000.0
                weight = float(_pick(row, ORDER_ALIASES['weight'])) * 1000.0
            except (TypeError, ValueError) as exc:
                rejected.append({'line': line, 'order_id': row.get('订单号'), 'reason': 'unparsable',
                                 'detail': str(exc), 'original': row})
                continue
            # Generalised anomaly predicate: covers the semi-final negative
            # weight (B20270281) and the preliminary negative length.
            bad = None
            for name, value in (('nonpositive_diameter', diameter), ('nonpositive_length', size),
                                ('nonpositive_weight', weight)):
                if value <= 0:
                    bad = name
                    break
            if bad:
                rejected.append({'line': line, 'order_id': oid, 'reason': bad, 'original': row})
                continue
            writer.writerow([oid, steel, diameter, size, weight, DENSITY])
            valid += 1

    with (root / 'blanks.normalized.csv').open('w', encoding='utf-8', newline='') as dst:
        writer = csv.writer(dst)
        writer.writerow(['blank_type', 'width_mm', 'thickness_mm', 'length_m', 'density'])
        for line, row in enumerate(_read_csv(blanks_path, blanks_encoding), 2):
            planar_a = float(_pick(row, BLANK_ALIASES['planar_a']))
            planar_b = float(_pick(row, BLANK_ALIASES['planar_b']))
            length_mm = float(_pick(row, BLANK_ALIASES['length']))
            # Width/thickness assignment is arbitrary; only the product matters.
            writer.writerow([int(float(_pick(row, BLANK_ALIASES['bid']))),
                             planar_a, planar_b, length_mm / 1000.0, DENSITY])

    config = dict(
        bed_length=150, min_bed_length=50, bed_width=1000, bed_width_mm=2000,
        bed_weight=60000, trim=1,
        # constraints.txt clause 4: at most six cold-bed rounds per scheme.
        max_rounds=6,
        # Over-production is LEGAL in the semi-final, and has to be.  Witnesses:
        #   * constraints.txt clause 8 is a one-sided FLOOR -- "每订单冷床分配总重量须
        #     不低于该订单重量" -- and no clause of the 12 forbids delivering more;
        #   * RULES.md 12.5: the semi-final penalises SHORT delivery only, and the
        #     excess simply does not enter the yield numerator ("超产不扣分，但超产部分
        #     不计入成材率"), which `platform_score.Rules.numerator_capped_by_demand`
        #     implements;
        #   * `platform_check.check` already treats the piece demand as a floor and
        #     reports only `short_delivery`.
        # The cap cannot be 0, because 3,304 of the 9,999 valid orders then have NO
        # legal scheme at all: the 50 m bed floor fixes a minimum segment count, and
        # for those orders the demand is not divisible into it, so every round set
        # either strands pieces or over-delivers.  Measured with
        # `diagnostics/semi_overproduction_probe.py`:
        # 3,135 of them need <=0.5% and 169 need <=2%, none needs more, and 2% is
        # therefore the smallest ratio that admits every order.  The solver still
        # aims for exact delivery -- over-delivery is uncredited and costs billet
        # weight in the denominator -- so this is an envelope, not a target.
        max_overproduction_ratio=0.02,
        objective='lex', search_max_group=8, length_mode='net_shared_trim',
    )
    (root / 'competition.config.json').write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')

    report = {
        'round': 'semi',
        'source_orders': valid + len(rejected),
        'valid_orders': valid,
        'rejected': rejected,
        'density_kg_m3': DENSITY,
        'encodings': {'orders': orders_encoding, 'blanks': blanks_encoding},
        'submission_ready': not rejected,
        'confirmed_from_rules': [
            'round cap 6 (constraints.txt clause 4)',
            'homogeneity is steel + spec/diameter (constraints.txt clause 5)',
            'adjacent-round continuity required (constraints.txt clause 6)',
            'per-round blank mass >= per-round bar mass (clause 7)',
            'per-order allocated mass >= order weight (clause 8)',
            'knives per round = segments + 1 (PDF 六(一), 50m/3m -> 17)',
        ],
        'assumptions': [
            'one shared 1 m trim at each round end; never written into length_scheme',
            'coverage counts orders sharing a single cold-bed round',
            'over-production is allowed but uncredited, capped at 2% per order '
            '(constraints clause 8 is a floor; RULES.md 12.5 penalises short delivery only)',
            'weight 40/30/20/10 with an independent time subscore (PDF 九); '
            'RULES.md records a conflicting Q&A claim that time is folded into yield',
            'knife baseline for the semi-final round is unknown; left unset',
        ],
    }
    (root / 'data_audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                                          encoding='utf-8')
    return report


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--orders', default='data/semi/orders_semi.csv')
    ap.add_argument('--blanks', default='data/semi/blank_used_finals.csv')
    ap.add_argument('--root', default='data/semi')
    args = ap.parse_args()
    print(json.dumps(prepare(args.orders, args.blanks, args.root), ensure_ascii=False))
