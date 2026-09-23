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

> **⚠️ 口径纠错（19:01 发现）**：13:14 记的「`score_capped` 的刀数子项不封顶（116.03>100）」
> **是错的**。`platform_score.py:268` 明确 `knife_subscore = 100 * baseline_knives / knives`，
> 复赛 baseline=160000、knives=185648 → **86.18463**（`subs_capped` 里再 `min(100, ·)` 兜底，
> 本例未触发）。我此前把公式写反成了 `knives/baseline`（才得到 116.03）。正确的总分分解：
> `91.797632 = 0.4×86.18463 + 0.3×91.079296 + 0.2×100 + 0.1×100`。
> 教训：报分前先 `grep -n "= 100 \* rules.baseline" platform_score.py` 核对公式方向，别凭比值直觉反推。

### 2026-09-22 13:14（第二次执行）
- 结论：**全量已完成且已收尾（183/183）**，无异常、无卡死。
- chunk 183/183，最新仍为 `chunks/XL2_38.0/000000.json`（mtime 11:30:34），距 13:14 约 104 分钟
  —— **非卡死**：与首跑同秒落盘的 summary/result 一致，属跑完后的静止。python 进程查询返回空（已退出）。
- 日志无 FAILED/Traceback/Error/Exception；无 `*_failed.json`；progress.json 36 组
  `failed_chunks=0 / unrun_chunks=0 / unplaced_orders=0`（elapsed 5569.1s）。
- 指标与首跑完全一致：knives 185648（16.7416/根）、0.910793、1.0、0、封顶 91.797632。
- **新增口径纠正**：`score_capped` 的刀数子项**不封顶**（116.03>100），封顶只作用于总分 →
  91.7976 = 0.4×116.03(未封顶) + 0.3×91.0793 + 0.2×100 + 0.1×100。勿用 min(ratio,1) 反推。
  另 check_run.py 输出的 `score_from_rounded_subscores_capped = 91.796`（子分先四舍五入口径），
  与官方 91.7976 相差 0.0016，报告时以 `score_capped` 为准。
- 任务性质已变：该跑已结束，后续执行应直接判定「183/183 已完成」并复述核对表，不再做停滞预警。

### 2026-09-22 19:01（第三次执行）
- 结论：**仍为 183/183 已完成、已收尾**，无卡死、无失败。本次无新跑（目录最后写入仍为 11:30）。
- 证据链：chunks 下 36 目录 / 183 个 .json；最新 `chunks/XL2_38.0/000000.json` mtime 11:30:34
  （距今 451 分钟，属**跑完后静止**，非卡死）；`tasklist //FI "IMAGENAME eq python.exe"`
  → 「没有运行的任务匹配指定标准」= 进程已退出（PowerShell 工具本次返回空，改用 Bash 的 tasklist 才拿到结果）。
- 异常扫描：无 `*_failed.json`；日志 36 行组摘要**全部** `0 failed / 0 unrun chunks`；
  关键字 FAILED/Traceback/Error/Exception **零命中**；progress.json 36 组 failed/unrun/unplaced 全 0（elapsed 5569.1s）。
- 核对（评分口径 `round=semi`, baseline 160000）：knives **185648**（rounds 11089 → 16.7416 刀/根）、
  yield **0.910793**、coverage **1.000000**、violation **0**、score_capped **91.797632**；
  physical_passed=True、orders 9999/9999（source 10000，valid 9999）。比值 vs 160000 = **1.1603×**。
- 新增可用命令：进程核查优先 `tasklist //FI "IMAGENAME eq python.exe"`（Bash 内可用）；
  PowerShell 工具本环境可能整段返回空，别仅凭其空输出就断言「无进程」。

### 2026-09-22 20:03（第四次执行）
- 结论：**仍为 183/183 已完成、已收尾**，无卡死、无失败。无新跑（目录最后写入仍是 11:30）。
- 证据：chunks 36 目录 / 183 .json；最新 `chunks/XL2_38.0/000000.json` mtime 11:30:34
  （距今约 513 分钟，属跑完后静止）；无 `*_failed.json`；日志 340 行、关键字零命中；
  progress.json 36 组 failed/unrun/unplaced 全 0（elapsed 5569.1s）。
- 核对（round=semi, baseline 160000）：knives **185648**（rounds 11089 → 16.7416 刀/根）、
  yield **0.910793**、coverage **1.000000**、violation **0**、score_capped **91.797632**。
  比值 vs 160000 = **1.1603×**；vs 90000 = 2.0628×（仅历史对照）。
- **本次新增坑与命令**：
  - `progress.json['groups']` 是 **list 不是 dict**，元素为
    `{"group":"NP01:43.0","orders":…,"plans":…,"failed_chunks":0,"unrun_chunks":0,"unplaced_orders":0}`
    —— 用 `.items()` 会 AttributeError，要按 list 遍历。
  - `tasklist //FI` 本次在 Bash 里报「无效参数 - '//FI'」**失败**；改用无参 `tasklist | grep -i python` 成功。
  - **PowerShell 工具本环境返回空但实际可执行**：把结果 `Set-Content "$env:TEMP\py_procs.txt"` 落盘后再用 Read/cat 读，成功拿到全部命令行。这是本环境查进程命令行唯一可靠姿势。
  - `wmic` **不存在**（WinError 2），别用。
- **本次发现的旁路事实**（非本任务监控对象，仅供判断「进程还活着吗」时排除干扰）：
  当前活跃 python 进程属于**其他跑/工具**，与 `runs/semi_nolimit_v1` 无关——
  `solve_semi.py --output runs/semi_fixed_v1 …`(PID 37592)、`diagnostics/seeding_ab.py --arm fixed`(29760)、
  一段对 `runs/semi_nolimit_v1/result.json` 与 `runs/theirs_main/复赛结果_棒材优化.json` 做对比的 ad-hoc `-c` 脚本(38680)，
  其余为 WorkBuddy/mcp/xhs/douyin 常驻服务。→ 判断本任务那一路时**必须按命令行过滤**，不能只看「有没有 python.exe」。
