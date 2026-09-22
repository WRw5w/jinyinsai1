# 棒材优化复赛全量重跑健康监控 — 执行记忆

## 任务要点
- 运行目录：`runs/semi_nolimit_v1`；日志：`runs/semi_nolimit_v1.log`
- 规模：183 chunk / 36 组 / 9999 单，约 20s/chunk，全量 ~1h
- 判据：最新 chunk mtime 距今 <=5min 正常；5–15min 可疑；>15min 高度疑似卡死
- 只读，不得修改/删除运行目录文件

## 可复用命令与坑
- 组文件命名：`chunks/<组名>/<六位offset>.json`，步长 60（000000/000060/…）。
  组目录名用下划线（如 `GC-4_32.0`），组内 JSON 不记组名——报「卡在哪个 offset」时
  看该目录内文件名的最大数字即可。
- 进程检查必须用 PowerShell 工具：`Get-CimInstance Win32_Process -Filter "Name='python.exe'"`。
  **Bash 里调 powershell 会被安全策略拒绝**（Bypassing PowerShell security checks）。
- `summary.json` / `result.json` / `progress.json` / 日志在**同一秒**一起落盘，
  说明它们是**跑完全部后一次性写出**，不能作为「仍在推进」的判据 → 判进度只认 chunks 下 .json 的 mtime。
- 刀数口径两套，勿混：
  - `summary.json` 的 `knives` = **197569**（内部口径，刀数/根 17.8167）
  - `check_run.py` / `platform_score.evaluate` 的 `knives` = **185648**（评分口径，刀数/根 16.7416）
- 刀数比值自算：`evaluate()` 返回里**没有** `knives_per_bar` 字段，需 `knives / rounds`。
- 基准切换：`ps.Rules.semi(baseline_knives=160000.0)` 复用同一 plan 即可得复赛口径；
  90000 只作历史对照（同一 plan：160000→91.7976，90000→76.7153）。
- `check_run.py` 头部 `head -30` 才有 knives/yield/coverage/score，尾部是长字段清单。

## 执行历史

### 2026-09-22 12:12（首次执行）
- 结论：**全量跑完（183/183），无异常、无卡死**。
- chunk 183/183（36 组），最新 `chunks/XL2_38.0/000000.json`，mtime 11:30:34，
  距 12:12 约 42 分钟不推进，但属**正常结束**（config 时长 5569.1s，另见 summary/result
  同秒落盘）。组布局：NP01 59 + C60 37 + 30C 15 + GB40 9 + … + 尾部各 1–5。
- 异常扫描：无 `*_failed.json`；日志无 FAILED/Traceback/Error/Exception；
  progress.json 全部组 `failed_chunks=0 / unrun_chunks=0 / unplaced_orders=0`。
- python 进程：**已退出（无残留）**，确认是正常收尾而非卡死。
- 最终核对（评分口径）：knives 185648（刀数/根 16.7416）、成材率 91.0793%、
  coverage 1.0、violation 0、封顶总分 **91.7976**（≈初赛 45.90/50），
  physical_passed=True，orders 9999/9999。
- 比值 vs 160000 基准：185648/160000 = **1.1603×**（刀数子项 86.1846/100，
  已超 09-22 记的理论下界 177736/1.11× 可逼近区间）；vs 90000 基准仅作对照 2.0628×。
- 下一步建议（未执行）：`platform_check` 用全量原始直径复验；考虑在此 plan 上继续压刀数。
