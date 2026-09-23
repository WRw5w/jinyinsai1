"""Acceptance test for the three semi-final deliverables (value-agnostic).

Nothing here trusts the previous step's report blindly:

 1. the result ZIP really carries exactly the JSON that `validation_report.json`
    hashes, and the ZIP CRC is intact;
 2. `diagnostics/semi_continuity_audit.py` on the delivered JSON reports
    C_equal_adjacent == 0 and orders_with_skips == 0 (clause 6);
 3. the code archive is *self-sufficient*: unpacked into a clean directory with no
    access to this repository, its own copy of the code + data reproduces the
    metrics that the archive's own `validation_report.json` claims, through the two
    independent gates (`platform_check`, `validate_plan`) and `platform_score.evaluate`;
 4. rebuilding the code archive is byte-stable.

Run:
    python -X utf8 verify_deliverables.py
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DELIV = ROOT / 'deliverables_semi'
RESULT_ZIP = DELIV / '复赛结果_棒材优化.zip'
RESULT_JSON = DELIV / '复赛结果_棒材优化.json'
CODE_ZIP = DELIV / '复赛代码与模型.zip'
REPORT = DELIV / 'validation_report.json'
PY = sys.executable

INNER = r'''
import json
from pathlib import Path
from platform_check import check
from platform_score import evaluate
from solver import Config, load_blanks, load_orders, validate_plan

rep = json.loads(Path('result/validation_report.json').read_text(encoding='utf-8'))
# 超产配置从报告自身推导，**不要**靠 runs/ 目录名猜：目录名会随构建批次变化，
# 猜错会静默退回 r=0.02，于是 validate_plan 的 `produced <= caps` 会用错上限，
# 把一份 r=0.25 的合法方案判成"超产越界"。
r_lim = rep.get('production', {}).get('per_order_extra_limit', 0.02)
cap = {0.02: 'cap002', 0.05: 'cap005', 0.10: 'cap010',
       0.25: 'cap025'}.get(round(r_lim, 4), 'competition')
settings = json.loads(Path('data/semi/competition.config.json').read_text(encoding='utf-8'))
settings.update(json.loads(Path('data/semi/%s.config.json' % cap).read_text(encoding='utf-8'))
                if Path('data/semi/%s.config.json' % cap).is_file() else {})
settings.update(continuity=True, coverage_shared=True, enforce_order_mass_floor=True,
                objective='platform_score', baseline_knives=160000.0)
cfg = Config(**settings)
orders = load_orders('data/semi/orders.normalized.csv', cfg, skip_invalid=True)
blanks = load_blanks('data/semi/blanks.normalized.csv')
plan = json.loads(Path('result/复赛结果_棒材优化.json').read_text(encoding='utf-8'))

metrics = validate_plan(plan, orders, cfg, blanks, blank_rule='per_round')
phys = check(plan, data=Path('data/semi'), weight_mode='strict', round='semi')
# 基准刀数必须**从报告里读**：build_submission 默认取 160000（复赛值，见
# evidence/rules/BASELINE_160000_SOURCE.md），并把 90000 作为并列读数单独存在
# `calibrated_prediction_at_90000_anchor`。写死任一个都会让 `score_capped` 的比对
# 误报失败（B 直接进 `40*min(1, B/K)`，两个读法的分数相差十几分）。
base = float(rep.get('calibrated_prediction', {}).get('assumptions', {})
             .get('baseline_knives', 90000.0))
sc = evaluate(plan, Path('data/semi'), round_name='semi', baseline_knives=base)
print(json.dumps(dict(
    orders_in_file=len(orders), schemes=len(plan), baseline_knives=base,
    validator_rounds=metrics['rounds'], validator_knives=metrics['knives'],
    validator_yield=round(metrics['yield_rate'], 6),
    validator_coverage=round(metrics['coverage'], 6),
    check_passed=phys['passed'], check_errors=phys['error_counts'],
    knives=sc['knives'], yield_rate=round(sc['yield_rate'], 6),
    coverage=round(sc['coverage'], 6), violations=sc['violation_count'],
    score_capped=round(sc['score_capped'], 6),
), ensure_ascii=False))
'''

ok = True


def say(good, text):
    global ok
    ok = ok and good
    print(('  OK   ' if good else '  FAIL ') + text)


report = json.loads(REPORT.read_text(encoding='utf-8'))

print('== 1. result ZIP layout and hashes ==')
plain = RESULT_JSON.read_bytes()
with zipfile.ZipFile(RESULT_ZIP) as archive:
    names = archive.namelist()
    say(names == ['复赛结果_棒材优化.json'], f'zip holds exactly one entry: {names}')
    packed = archive.read(names[0]) if names else b''
    say(packed == plain, 'packed bytes == validated plain JSON')
    say(archive.testzip() is None, 'zip CRC intact')
say(hashlib.sha256(plain).hexdigest() == report['json_sha256'],
    f"json sha256 matches validation_report ({report['json_sha256'][:16]}...)")
say(hashlib.sha256(RESULT_ZIP.read_bytes()).hexdigest() == report['zip_sha256'],
    f"zip sha256 matches validation_report ({report['zip_sha256'][:16]}...)")

print('== 2. clause 6 continuity audit on the delivered JSON ==')
proc = subprocess.run([PY, '-X', 'utf8', str(ROOT / 'diagnostics' / 'semi_continuity_audit.py'),
                       str(RESULT_JSON)], cwd=ROOT, capture_output=True, text=True,
                      encoding='utf-8', errors='replace')
tail = [ln for ln in proc.stdout.splitlines() if '= ' in ln and 'C 相邻' in ln]
say(proc.returncode == 0 and any(' = 0 ' in ln for ln in tail),
    'audit ran; C(相邻两轮集合完全相同) count line: ' + (tail[0].strip() if tail else '<none>'))
say('PASS' in proc.stdout, 'audit verdict PASS')

print('== 3. code archive is self-sufficient ==')
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    with zipfile.ZipFile(CODE_ZIP) as archive:
        archive.extractall(tmp)
    say(not (tmp / 'runs').exists(), 'no runs/ in archive')
    say((tmp / 'code' / 'continuity_shaper.py').is_file(),
        'code/continuity_shaper.py present')
    say((tmp / 'code' / 'build_semi_plan2.py').is_file(),
        'code/build_semi_plan2.py present')
    say((tmp / 'data' / 'semi' / 'orders_semi.csv').is_file(), 'raw GBK orders present')
    (tmp / '_inner.py').write_text(INNER, encoding='utf-8')
    env = {**os.environ, 'PYTHONPATH': 'code'}
    proc = subprocess.run([PY, '-X', 'utf8', '_inner.py'], cwd=tmp, env=env,
                          capture_output=True, text=True, encoding='utf-8',
                          errors='replace')
    if proc.returncode != 0:
        say(False, 'inner re-validation crashed')
        print(proc.stdout[-2000:])
        print(proc.stderr[-3000:])
    else:
        got = json.loads(proc.stdout.strip().splitlines()[-1])
        print('       ' + json.dumps(got, ensure_ascii=False))
        cp = report.get('calibrated_prediction', {})
        after = report['after']
        say(got['check_passed'] is True and not got['check_errors'],
            f"platform_check passed with no errors ({got['check_errors']})")
        say(got['violations'] == 0, 'violation_count == 0')
        say(got['orders_in_file'] == report['included_order_count'],
            f"archive data has all {report['included_order_count']:,} valid orders")
        say(got['validator_rounds'] == after['rounds'],
            f"rounds reproduce validation_report: {got['validator_rounds']}")
        say(abs(got['validator_coverage'] - 1.0) < 1e-9 or
            abs(got['validator_coverage'] - after['coverage']) < 1e-6,
            f"validator coverage reproducible: {got['validator_coverage']}")
        say(got['knives'] == cp.get('knives'),
            f"knives reproduce calibrated_prediction: {got['knives']}")
        say(abs(got['score_capped'] - cp.get('score_capped', -1)) < 1e-6,
            f"score reproduces calibrated_prediction: {got['score_capped']}")

print('== 4. byte-stable repack ==')
first = hashlib.sha256(CODE_ZIP.read_bytes()).hexdigest()
subprocess.run([PY, '-X', 'utf8', str(ROOT / 'make_code_zip.py')],
               cwd=ROOT, capture_output=True)
say(hashlib.sha256(CODE_ZIP.read_bytes()).hexdigest() == first,
    'rebuilding the code archive reproduces the same SHA-256')

print()
print('ACCEPTANCE: ' + ('PASS' if ok else 'FAIL'))
sys.exit(0 if ok else 1)
