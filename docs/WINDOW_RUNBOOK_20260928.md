# 五发运行手册：2026-09-28 07:05–23:05（北京时间）

这份手册是**无人在场时**的提交脚本。它沿用 09-27 手册的骨架（那份手册与当天五发实测
见 `docs/WINDOW_RUNBOOK_20260927.md`），但**候选策略换了**：09-27 是按"谱系探针"固定排表，
09-28 是**按分数爬梯**——每跳发"当时可用的最好、且从未发过的包"。

## 硬规则（先读完这段再动手）

1. **最早一发不早于 07:00**；五发必须在 **23:59** 前发完。首发 07:05，之后每 4 小时一发。
2. 授权：用户 09-27 的 /goal 与 09-27 00:10 当面确认的时间表就是这类窗口的授权。
   MCP 工具仍需显式 `confirm_real_submit=true` 才会真点。
3. 队名 `鱼不吃猫`，参赛编号 `AIC-2026-93096493`，阶段 `semi`。
4. `D:\new_mcp` 是**公开仓库**：只读，不提交、不推送。密钥、原始会话凭据、浏览器登录状态不进仓库。
5. 每次提交前先用 `aic_submission_plan` 做只读预检；若它报 `already_submitted` 非空或配额异常，
   停下来查清原因，不要盲发。
6. **任何已评过 / 已发过的包绝不重发**。队列里那两行陈旧记录
   （`ff0a9e61` 旧 `cand_rounded` 条目、`1a62b92e` 已评 90.59 的 `cand_rounded`）**绝不提交**。
   09-26 的四发（`cand_rounded` 90.59 / `cand_yieldprobe` 88.92 / `cand_covprobe` 89.87 /
   `cand_covprobe25` 90.40）、`submission_semi_merged_v4`（0 分不可行）、以及 **09-27 的五发**
   都已评过，全部排除在外。

## 预测口径（已实测钉死，不要再改）

官方分 = **本地分四舍五入到两位**；官方**刀数 / 成材率 / 覆盖率**与本地投影**逐位相同**（五包 15 项）。
拿 `docs/SCORE_MODEL.md` 的公式复核本地分即可，`aic.py score <plan> --round semi --data data/semi`
或 `runs/_requant_report.py <plan>`。**不要**再用"官方 ≈ 本地 + 0.0045"那套旧口径——它只差在两位小数
的进位，已被四舍五入口径取代；"官方 = 本地 × 1.0104"同样作废（已证伪）。

## 五发时间表（阶梯待填：见下节"候选池"，09-27 夜打包后填入）

| 跳 | 时间 | 候选 | 队列 id | 本地预测 | 刀数 / 材 / 覆 | 结构 |
|---|---|---|---:|---|---:|---|---|
| ① | 07:05 | `cand_merge1`（**占位**，见下） | `fd16ee3c-4768-40ae-bc3a-2a26d8513518` | 92.4682 | —（以打包时报告为准） | Wave H（给现有精修器加重启预算）——**若 07:00 前有新谱系（计划第 4 项）包且 >92.48，改发它** |
| ② | 11:05 | `cand_anneal1`（占位） | `84d518db-11af-418d-a569-5aab588983f2` | 92.4119 | — | 全批退火（Wave D） |
| ③ | 15:05 | `cand_ils1`（占位） | `8b634902-cb63-42ec-a9a2-41324fe66a7a` | 92.3810 | — | 打乱重启 ILS（Wave C） |
| ④ | 19:05 | `cand_rq_polish`（占位） | `aceb668e-bd28-492e-85d2-cb2824cc1528` | 92.2969 | — | 修后确定性下降（Wave A/B） |
| ⑤ | 23:05 | **当晚新产出的最高分包** | | | | 必须能**超过 92.48**才有意义（见下"为什么这四格只是占位"） |

## 实发记录（2026-09-28）

表里 ①–⑤ 都是**占位**；当天实际发的是链路线扩量产出的包，**替换理由**：它比 ①–④ 高 0.02–0.22 分，
是当天唯一能超过 92.48 的候选（计划第 4 项"新谱系"的成品）。

| 跳 | 时间（北京） | 候选 | 队列 id | 本地预测 | 官方分 | 刀 / 材 / 覆 |
|---|---|---|---|---:|---:|---|
| ① | 14:46:06 点击 / 14:46:11 受理 / 14:46:08 出分 | `cand_all_chain106` | `48e2e16a-2b8b-43d7-b4f3-7ef30c312287` | 92.5176 | **92.52** | 86.7 / 94.59 / 100.0 |

- 包 `artifacts/candidates/cand_all_chain106/复赛结果_鱼不吃猫.zip`，
  sha256 `1aaf44a98e99036e23389faeab30667a97c64f5209cd029d1e3da7972becd71d`；
  `accepted_and_saved_file_verified`，`attachmentMatches=true` 且附件 sha 与本包**逐位相同**。
- **官方最高 92.48 → 92.52**；「官方 = 本地四舍五入」第 11 个样本。今日台账 **1/5**。
- 这是**路线 B（改链）的第一个平台实证**：链扰动不是本地伪影。
- 取分走的是**手工两步**（MCP 自动取分因队名/编号过滤匹配不上，必定失败）：
  `leaderboard_pipe.mjs result-records` → 改 `record.team` 为 `鱼不吃猫` →
  `tools/aic_attribute_score.py --apply`（输出 `applied: true`、`ledgerBest: 92.52`、
  `closedQueueIds` 含本跳 id）。
- **陈旧行 `ff0a9e61` / `1a62b92e` 与全部已评包都没再碰。**

**为什么这四格只是占位**：五发取最高分，而 09-27 夜 ⑤ `cand_merge2` 已经拿回 **92.48**，
表里 ①–④ 的四个包（92.2969–92.4682）**全部被 92.48 严格支配**——发它们不会改变榜面分。
它们的作用只有两条：(a) 万一 09-28 白天新谱系包**发失败**，用它们兜底；(b) 当作**独立复验样本**
（同一个包在不同日期的读数是否仍逐位相同）。**因此 09-28 的真正任务是产出能过 92.48 的新谱系包**
（计划第 4 项），① 那一格在 07:00 前若有更好的就**当场换掉**。

**09-27 夜实测（本手册决策规则的输入）**：① 92.12（`cand_rp4`）/ ② **92.43**（`cand_deep1`）/
③ 92.16（`cand_cy1`）/ ④ 92.43（`cand_extra2`）/ ⑤ **92.48**（`cand_merge2`），五发全部
`失败原因=-`（"可行"）、刀/材/覆 15 项与本地投影逐位相同、官方分 = `round(本地, 2)`。
⇒ **重定量家族在平台成立**（② ≥92.30）、**带共享的 Q→S→R 谱系没问题**（③ 92.16 正常回分）、
**没有任何一报告违规/不可行**。完整记录见 [WINDOW_RUNBOOK_20260927](WINDOW_RUNBOOK_20260927.md)。

分包名固定为 `复赛结果_鱼不吃猫.zip`，ZIP 根目录只有一个同名 JSON。
包路径：`D:\02_Projects\ML\jinyinsai1_nolimit\artifacts\candidates\<候选名>\复赛结果_鱼不吃猫.zip`。
**每一跳动手前先核哈希**：`sha256sum <zip>` 必须与本手册该行的队列 sha 逐位一致，不一致就停下查。

## 每一发的调用序列

```
1) aic_submission_plan(path=<zip>, stage="semi")        # 只读预检：配额、重复、best
2) aic_auto_submit(path=<zip>, stage="semi", team="鱼不吃猫",
                   confirm_real_submit=true)            # 守卫→浏览器→校验→入队→点提交→轮询→记分
3) aic_submission_ledger(stage="semi")                  # 1–10 分钟后取分
   aic_queue_status()                                   # 该候选 status=scored 且带 score
```

**取分修正（09-27 07:15 实测，仍然有效）**：MCP 的自动取分步骤**必定失败**——管道按
`fields["参赛编号"] === AIC_LEADERBOARD_TEAM_ID` 过滤行，而 `aic_auto_submit(team="鱼不吃猫")`
把队名当编号传下去，匹配不到任何行（症状 `no_result_payload` / `no_matching_result`）。
每次提交后用下面两步手工取分：

```bash
cd /d/new_mcp && AIC_LEADERBOARD_CHROME_PATH="C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" \
  AIC_LEADERBOARD_RECORDS_URL="https://reg.aicomp.cn/app/JSGLPT/65b75207a58fdc32c79e9842" \
  AIC_LEADERBOARD_ROOT="D:/new_mcp" AIC_LEADERBOARD_TEAM_ID=AIC-2026-93096493 \
  AIC_LEADERBOARD_STAGE=semi AIC_LEADERBOARD_EXPECTED_SHA256=<包的sha256> \
  node tools/leaderboard_pipe.mjs result-records > /tmp/raw_<跳>.json
```

成功的标志：`record.status="DONE"`、`record.attachmentMatches=true` 且 `attachmentSha256`
等于本跳包的哈希。然后把 `record.team` 这**一个**字段改成 `鱼不吃猫`（台账行的字段值），
另存 `/tmp/norm_<跳>.json`，回填（工具先备份台账与队列；哈希已核验，无需
`--accept-unverified-hash`）：

```bash
cd /d/new_mcp && ./.venv/Scripts/python.exe -X utf8 tools/aic_attribute_score.py \
  --root D:/new_mcp --stage semi --team 鱼不吃猫 \
  --row-json "$(cygpath -w /tmp/norm_<跳>.json)" --apply
```

成功标志：输出 `"applied": true`、`"ledgerBest": 92.xx`、`closedQueueIds` 含本跳 id。
注意：只能用它自己的 venv python（`D:\new_mcp\.venv\Scripts\python.exe`）；PATH 上的
`python` 是 WindowsApps 假壳，会静默 exit 49。python 参数里的路径要用 `cygpath -w` 转成
Windows 形式，bash 重定向路径用 `/tmp/...` 即可。

- 浏览器会由管道自己拉起（对战 Chrome）。若它要求人工登录而无人可登：**停下**，把现象写清楚，
  不要盲发后面的包。
- 若报 `download.saveAs: Target page, context or browser has been closed`（Edge 153 已知的下载崩溃）：
  **这发生在提交之后、只影响附件哈希**，提交本身有效。用 `tools/aic_attribute_score.py` 把该行分数
  补记进台账，然后照常继续。
- 出分时间：09-26/09-27 实测 8.5–34 秒。若 10 分钟还没出分，查 `aic_queue_status` 的 active 项，
  **别重复点同一个包**。

## 决策规则

- **先看 09-27 的收尾**（**已实测完毕，2026-09-27 23:30**：② `cand_deep1` 回 **92.43**、
  ③ `cand_cy1` 回 92.16，都没违规——下面两条判据**没有触发**，保留作历史）：① 发之前，先用
  `aic_submission_ledger(stage="semi")` 读一眼 09-27 五发的实测分
  （`docs/WINDOW_RUNBOOK_20260927.md` 的实发记录表 + `docs/CURRENT.md`）。两条要点：
  - 若 09-27 ② `cand_deep1`（**重定量招式**的第一个探针）报**违规/不可行**，说明重定量招式把结构
    改坏了；它后面的 ④ `cand_extra2`、⑤ `cand_anneal1` 都是同族，① 起改用**不带重定量招式**的包
    （`cand_cy1` 系已发过，就用回退表：`cand_rf3` / `cand_rq3` / `cand_p_shift2` / `cand_mx_*`）。
  - 若 09-27 ③ `cand_cy1`（**带共享**的 Q→S→R 二级不动点）报**违规/不可行**，说明"共享遍历"没过
    平台，整个 Q→S→R 谱系（含 `cand_sp1` / `cand_qs1` / `cand_ils1`）全部降级为"待诊断"。
  - 若 09-27 各发都正常回分，则本手册照走。
- **①（07:05，本梯队的探针）是全天的分水岭**：它回 **≥ 前一天的官方最高** → 新招式（Wave H 的
  预算 + 合并轮）在平台上成立，②–⑤ 按阶梯表继续发同族的新包（各自不同）；回**明显低于本地预测**
  （>0.3 的差）或报违规 → 新招式作废，②–⑤ 全部改发**回退表**（按分数降序、跳着发，
  不要五发都发同谱系），并把这一结论写进 `docs/CURRENT.md`。
- **⑤（23:05）可以临时替换**：若当天 15:00 之后又产出更好的、且已过 MCP 校验的包，就发它，
  不要为了"照表"发一个更差的。替换后在实发记录里写明换的是谁、为什么。
- 每一发后对照本地预测归因：差在**刀数 / 成材率 / 覆盖率**哪一项，记一行。
- 若某发因故失败（浏览器/登录/网络/超时）：修好后**顺延发同一格**，不要跳到下一格再回头。
- 若发现**另一个会话**也在跑这个日程（`mcp__scheduled-tasks__list_task_runs`，或
  `aic_queue_status` 里有并发 active 提交），**让给它**：把这一跳降级为观察，不要两边同时点。

## 候选池（09-27 夜打包，全部过本地严格校验 + MCP `aic_validate_candidate`）

**阶梯（待填）**：09-27 夜的重定量四波（C 打乱重启 / D 全批退火 / E 额外轮 / G 集中火力）
产出若干计划，按本地分降序打包成 `cand_*`。**已入队的**（每波出计划就打包+校验+入队，不等全链跑完）：

| 候选 | 队列 id | 本地预测 | sha256（前 16 位） |
|---|---|---:|---|
| `cand_deep1` | `b7736779-a01b-48fc-ac5a-87e73511f512` | **92.4322** | `1437d3d69e0167a9` |
| `cand_extra2` | `e4665b24-5237-4976-abc9-77bccee07ba3` | 92.4290 | `c6eaa58291f97e26` |
| `cand_anneal1` | `84d518db-11af-418d-a569-5aab588983f2` | 92.4119 | `e1e389e37679655e` |
| `cand_ils1` | `8b634902-cb63-42ec-a9a2-41324fe66a7a` | 92.3810 | `694ea7a3d61796c5` |
| `cand_rq_polish` | `aceb668e-bd28-492e-85d2-cb2824cc1528` | 92.2969 | `607332599460154f` |

**重定量四波到此收敛**（09-27 上午）：C +0.224 / D +0.255 / E +0.272 / G **+0.275**
（`plan_deep1` 92.4322，只改进 17 批、比 E 高 +0.0032）——**边际收益已塌到 0.003/波，这个招式集
基本耗尽**，09-28 的阶梯要换招式，不要继续加波次。四波里最高的三个 + `cand_cy1` 就是 09-27 晚间的
四发（见 `docs/WINDOW_RUNBOOK_20260927.md` 改期通告）；**它们若在 09-27 晚全部正常回分，
下面这五格就都已被消耗，09-28 的阶梯必须由 09-27 夜新产出的包填**（届时按本地分降序填表）。

**回退池（未发过、已过本地严格校验；09-27 的五发已消耗 cy1/qs1/p_full4/p_full8，不在池内）**：

| 候选 | 队列 id | 本地预测 |
|---|---|---:|
| `cand_trsplit6` | 未入队（按路径直发） | 92.1516（重打包谱系；`artifacts/candidates/cand_trsplit6/`，sha256 `11790196…`） |
| `cand_sp1` | `d993957f-3f64-4550-933c-8d857e033f89` | 92.1508（Q→S→R 谱系） |
| `cand_rp5` | `a1753376-2617-4302-ba10-343efee78cfe` | 92.1406（重打包+共享谱系） |
| `cand_rf3` | `8a5766ee-1f76-44f0-8cff-a1500c005871` | 92.1087（重打包谱系） |
| `cand_p_full2` | `3323c148-f758-4ca3-8630-ac2fffeeece0` | 92.0736 |
| `cand_p_shift2` | `1749cd17-da82-47c5-a6b5-0ee4fd6f8d29` | 92.0119（覆盖率 99.87，独立谱系） |
| `cand_rq3` | `9d8a73e0-0fe9-445e-bc2b-c1310c7f2a9b` | 91.99 |
| `cand_iter11` | `622f1ae1-49bf-4bac-a269-6b98e432ab9a` | 91.80 |
| `cand_mx_full8` | `baac014d-64d4-4746-985f-cb053cf4c9d5` | 91.80–91.99 |
| `cand_mx_full4` | `f6fc7058-abce-46f4-a77e-878542b7b4df` | 91.80–91.99 |
| `cand_mx_shift2` | `410578ba-af8f-410e-a2ad-3293a0958721` | 91.80–91.99 |

（表按预测分降序。**同一格绝不发两次**。）

## 实发记录（每发后更新）

| 跳 | 点击(北京) | 出分(北京) | 官方分 | 刀/材/覆 | 与本地差 | 备注 |
|---|---|---|---|---|---|---|

## 收尾

1. 把五发的实测分数、子分、与预测的偏差、以及"五发全部在 23:59 前完成"是否达成写进 `docs/CURRENT.md`；
   同步本手册与 `docs/SUBMISSION_PLAN_20260926.md` 的相关段落。
2. `git commit`（message 末尾加 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`）；
   **不要 push `D:\new_mcp`**。
3. 用中文向用户报告：五发各多少分、最好的一次是哪个包、官方榜面分变化、下一步建议。
   若有任何一发没发出去，如实说明并给出补救（比赛截止 2026-10-05 20:00）。
