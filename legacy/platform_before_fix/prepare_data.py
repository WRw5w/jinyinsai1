"""Normalize the provided competition CSVs without modifying source data."""
import csv
import json
from pathlib import Path


def prepare(root=Path('data')):
    density = 9860  # Explicit original CSV header: 9.86 g/cm^3.
    rejected = []
    with (root / 'orders_quarter.csv').open(encoding='utf-8-sig', newline='') as src, (root / 'orders.normalized.csv').open('w', encoding='utf-8', newline='') as dst:
        reader = csv.DictReader(src)
        writer = csv.writer(dst)
        writer.writerow(['order_id', 'steel', 'diameter', 'length', 'weight', 'density'])
        valid = 0
        for line, row in enumerate(reader, 2):
            size = float(row['订单定尺(mm)']) / 1000
            if size <= 0:
                rejected.append({'line': line, 'order_id': row['订单号'], 'reason': 'nonpositive_length', 'original': row})
                continue
            writer.writerow([row['订单号'], row['坯料钢种'], float(row['订单直径(mm)']), size, float(row['订单重量(t)']) * 1000, density])
            valid += 1
    with (root / 'blank_used.csv').open(encoding='utf-8-sig', newline='') as src, (root / 'blanks.normalized.csv').open('w', encoding='utf-8', newline='') as dst:
        writer = csv.writer(dst)
        writer.writerow(['blank_type', 'width_mm', 'thickness_mm', 'length_m', 'density'])
        for row in csv.DictReader(src):
            writer.writerow([row['坯料'], row['宽度mm'], row['厚度mm'], float(row['长度mm']) / 1000, density])
    config = dict(bed_length=150, min_bed_length=50, bed_width=1000, bed_width_mm=2000,
                  bed_weight=60000, trim=1, max_rounds=100, max_overproduction_ratio=0,
                  objective='lex', search_max_group=8)
    (root / 'competition.config.json').write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
    report = {'source_orders': valid + len(rejected), 'valid_orders': valid, 'rejected': rejected,
              'density_kg_m3': density, 'submission_ready': not rejected,
              'assumptions': ['1 metre trim per segment end, as PDF example',
                              '100 rounds algorithmic cap, not specified by supplied constraints',
                              'exact rounded-up piece demand, no additional overproduction',
                              'aggregate blank material accounting; no per-blank packing rules supplied',
                              'lexicographic objective; official baseline knife count unavailable']}
    (root / 'data_audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    prepare()
