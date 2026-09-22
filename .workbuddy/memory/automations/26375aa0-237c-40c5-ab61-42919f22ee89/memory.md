# 自动化任务记忆：棒材复赛中途体检（卡死检测）

任务 ID: 26375aa0-237c-40c5-ab61-42919f22ee89
目标：抓「进程存活但已卡死」（历史故障形态：进程存活、CPU 烧着、66 分钟零产出）。

运行目录：runs/semi_nolimit_v1
日志：runs/semi_nolimit_v1.log

## 关键事实（长期有效）
- `check_run.py <rundir>` 只能跑**初赛(prelim)**：它调 `ps.evaluate(plan, Path('data/semi'))` 却没传
  `round_name='semi'`，于是去读 `data/semi/orders_quarter.csv`（该文件名属初赛），必然 FileNotFoundError。
  正确调用方式（本次实测可用）：
  ```python
  import json, sys; sys.path.insert(0,'.')
  import platform_score as ps
  plan = json.loads(Path('runs/semi_nolimit_v1/result.partial.json').read_text(encoding='utf-8'))
  rules = ps.Rules.semi(baseline_knives=160000.0)
  rep = ps.evaluate(plan, Path('data/semi'), rules=rules, round_name='semi')
  ```
- `rep` 里**没有** `knives_per_bar` 字段（check_run 的打印列表里那个 key 永远打不出来）。
  «刀数/根» 需自算：把每个 batch 的 `len(b['length_scheme'])` 累加得到总根数(rounds)，
  knives / rounds 即为每根刀数。（semi 规则 `knife_add_per='round'`，每行 knives = segments+1）
- 分组布局（复赛全量 183 chunk）：
  - NP01_43.0 → 59 chunk
  - C60_26.5  → 37 chunk  （实测偏慢，约 46s/chunk，不是 20s）
  - 30C_45.0  → 87 chunk  （最大组，启动最晚，是"当前正在跑"的那组）
  - 合计 183。
- chunk 文件名步长 60（000000/000060/...），每组独立编号。
- 进度判据：最新 chunk mtime 距今分钟数；<=5 正常，5-15 可疑，>15 高度疑似卡死。
  进程检查：`Get-Process python`（注意：自动化进程本身可能不在，需看是否有 python 常驻）。

## 执行历史
### 2026-09-22 10:35（首次执行）
- 状态：**正常推进**，未卡死。
- chunk 完成 106/183（NP01 59 + C60 37 + 30C 10）。
- 最新 chunk：chunks/30C_45.0/000540.json，mtime 10:35:13，距今 <1 分钟。
- 30C 波动 20s/chunk（000180 那次 36s），当前卡点组 = 30C_45.0，推进到 offset 540。
- 日志只有 2 行（NP01 1193s / C60 2029s 完成），无 FAILED/Traceback/Error/Exception；
  30C 尚未输出完成行属正常（还在跑）。无 `_failed.json`。
- 部分结果（result.partial.json，仅含 NP01+C60 两组）：knives 111232、刀数/根 17.28、
  成材率 90.5713%、覆盖率 57.3757%、违规 0、封顶总分 88.65（≈初赛 44.33/50）。
  注意 `coverage 0.5737` 与 `coverage_over_source 0.5737` 相同，但 `included_order_count`
  5737 恰好等于 coverage×10000 的分子，属巧合，勿混淆。
- 未跑完（106 < 183），不触发第 5 步完整核对。日志尾部无 30C 行属预期。
