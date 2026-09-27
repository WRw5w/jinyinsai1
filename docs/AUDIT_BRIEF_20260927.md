# 送审说明（2026-09-27 14:00）：本轮进展、证据链，以及请重点证伪的点

这份文档写给**外部审核 AI**。前提：你只能看到这个 GitHub 仓库，看不到本机工作区。
所以下面每条主张都标了「仓库内证据」与「复现命令」，并单列一节说明**哪些东西不在仓库里**
（审核时你会遇到的盲区，以及怎么按需补上）。

队名 `鱼不吃猫`，参赛编号 `AIC-2026-93096493`，阶段 `semi`（AIC 2026 复赛·棒材组合订单
高效锯切优化）。结果截止 2026-10-05 20:00，代码截止 2026-10-07 23:59。

**送审版本**：仓库分支 `semi-final`，以 `docs/AUDIT_BRIEF_20260927.md` 这个文件最近一次
提交为准（`git log -1 -- docs/AUDIT_BRIEF_20260927.md` 即本说明的版本号）。
此后我们还会继续提交（今晚还有 4 发），**本文档里凡带时间戳的数字都只在那之前有效**。

---

## 0. 一句话

本地最好计划 **92.4322**（`plan_deep1`，未提交），官方最高 **92.12**（`cand_rp4`，09-27 07:06
实测，榜面第 28/40，榜首 93.79）。**评分公式已用五个包逐位对账确认**，今晚还有 4 次提交额度，
排了 4 发（19:05 / 20:30 / 22:00 / 23:15）。下一波搜索（Wave H）正在跑。

## 1. 状态快照（2026-09-27 14:00 北京时间）

| 项 | 值 |
|---|---|
| 官方最高分 | **92.12**（`cand_rp4`：184,510 刀 / 93.65% / 99.85%） |
| 榜面位置 | 第 28 名 / 40 队，榜首 93.79（12:09 手工读数，**非**机器可验） |
| 本地最高 | **92.4322**（`plan_deep1`：184,531 刀 / 94.379% / 99.99%，账单 536,301,947 kg） |
| 今日额度 | 5 发上限，已用 1，余 4（北京时间午夜重置） |
| 剩余时间 | 结果截止 2026-10-05 20:00（约 198 h） |
| 正在跑 | Wave H（12:22 起，12 工位 × 每批 400 次重启，13:48 已有 22 处改进） |

**今晚的 4 发**（09-27 平台上午故障、打分 19:00 后恢复，原 11:05/15:05 两跳已确认未发出并取消）：

| 跳 | 时间 | 候选 | 队列 id | 本地预测 | 结构 |
|---|---|---|---:|---:|---|
| ② | 19:05 | `cand_deep1` | `b7736779-…` | **92.4322** | 重定量招式（集中火力） |
| ③ | 20:30 | `cand_cy1` | `e07bd9ea-…` | 92.1572 | **不带**重定量招式（家族对冲） |
| ④ | 22:00 | `cand_extra2` | `e4665b24-…` | 92.4290 | 重定量招式（额外轮） |
| ⑤ | 23:15 | `cand_anneal1` | `84d518db-…` | 92.4119 | 重定量招式（退火） |

五发取最高，所以同族里更低的包对最终分无贡献；③ 的存在是为了对冲"重定量招式本身把结构
改坏"这一族特有风险。执行链有三层（会话级 durable cron + 应用级计划任务 + 手册），
每层都要求先读台账确认"今日未超发、该候选未评过"才动手。

## 2. 主张 → 证据 → 复现

### C1 总分 = 0.4×刀子分 + 0.4×成材率 + 0.2×覆盖率，**无时间项**

- 证据：`src/platform_score.py:71`（`Rules.semi(baseline_knives=160000.0, weights=(0.4, 0.4, 0.2, 0.0))`）；
  权重是怎么从回执里解出来的，见 `docs/SCORE_MODEL.md` 的「权重是怎么钉死的」。
- 复现：`./.venv/Scripts/python.exe -X utf8 aic.py score <plan> --round semi --data data/semi`
- 说明：官方 PDF 里权重表写 30%、公式行写 40%，两者矛盾；四张回执钉死了是 40%。
  如果存在时间项，其权重 < 0.005（五次回分都在两位小数显示精度内被上式复现）。

### C2 官方分 = 本地分四舍五入到两位；三项**逐位相同**（5 包 × 3 项 = 15 项全中）

- 证据：`artifacts/accepted/official_scores.json`（五张平台原始回执，含平台报的
  `attachmentSha256`、`attachmentMatches`、台账 `hash_attestation`，以及我们今天重新做的字节核验）；
  对账表见 `docs/SCORE_MODEL.md`。
- 复现：对 `artifacts/candidates/<候选>/复赛结果_鱼不吃猫.json` 跑 §C1 的命令，
  与回执里的 `failureReason`（`可行; 锯切刀数=…, 成材率=…%, 覆盖率=…%`）逐位对比。

| 候选 | 本地刀数 | 官方刀数 | 本地材% | 官方材% | 本地覆% | 官方覆% | 本地分(未取整) | 官方分 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| cand_rounded | 185,229 | 185,229 | 91.39 | 91.39 | 97.43 | 97.43 | 90.5940 | 90.59 |
| cand_yieldprobe | 185,229 | 185,229 | 87.21 | 87.21 | 97.43 | 97.43 | 88.9220 | 88.92 |
| cand_covprobe | 185,252 | 185,252 | 91.41 | 91.41 | 93.81 | 93.81 | 89.8740 | 89.87 |
| cand_covprobe25 | 185,233 | 185,233 | 91.40 | 91.40 | 96.46 | 96.46 | 90.4040 | 90.40 |
| cand_rp4 | 184,510 | 184,510 | 93.65 | 93.65 | 99.85 | 99.85 | 92.1180 | 92.12 |

**这一条最值得你复核，因为它决定我们全部优化的方向。** 两点请特别看：

1. **不是循环论证**：公式是在 09-26 20:22–20:37 的四张回执上标定的，`cand_rp4` 是此后才
   生成、才提交的包——它的三项在提交前就由本地投影写下、提交后与平台读数逐位相同。
   四张用于识别模型，第五张是**事前预测**。（但要诚实：`cand_rp4` 的结构是用同一个本地
   打分器挑出来的，所以这检验的是「度量模型是否复现平台读数」，**不是**「搜索是否独立」。）
2. **旧口径 ×1.0104 已作废**：09-26 文档曾记「官方成材率 = 本地 × 1.0104」，四发比值一致
   （1.01038–1.01047）。真相是**旧本地代码**把成材率分子算成「直径截断到整毫米」的质量
   （偏小约 1%），平台读的是 `Σ min(实际交付, 需求)`。修正落在 `69ce957`（09-26 21:33）。
   同一份 `cand_rounded`，旧代码给 90.4428，当前代码直接给 91.39。

### C3 覆盖率只数「与别人同处一轮」的订单

- 证据：`src/platform_score.py:393`（`if len(set(scheme)) > 1: combined.update(scheme)`），
  分母 9,999；`rules.coverage_shared` 是复赛与初赛的开关差异。
- 由三发探针定价：`cand_covprobe` 93.81% 与 `cand_covprobe25` 96.46% 只差覆盖率，
  解出 w_覆 = 0.200（`docs/SCORE_MODEL.md`）。
- 推论：**独占一轮的订单白丢 0.002 分/单**，等价汇率 `COVER_KG ≈ 28,838 kg-eq`
  （`tools/analysis/requant_batches.py:71`）。

### C4 账单是**逐轮**取整的

- 证据：`tools/analysis/requant_batches.py:81`（`bill(m, w) = ceil((m − 1e-7)/w) · w`，
  half-open、防浮点边界）；
- 累加的位置是关键，而那个文件不在库里（`runs/` 被 .gitignore），所以把它**逐字抄在下面**
  （`runs/_requant_report.py` 第 41–56 行，一个批次内的循环）：

```python
        for s, c in zip(b['length_scheme'], b['counts']):
            R += 1
            net = sum(F(str(v)) for v in s.values())
            m = (net + 2) * int(c) * lin
            M += m
            trim_b += 2 * int(c) * lin
            billed += bill(m, w)          # ← 第 47 行：逐轮累加，这就是平台的账单口径
            for o, length in s.items():
                k_total += int(F(str(length)) / F(str(orders[o]['size'])))
                deliv[o] += int(F(str(length)) / F(str(orders[o]['size']))) * int(c)
            if len(s) > 1:
                shared.update(s)
        mass += M
        trim += trim_b
        floor_cur += bill(M, w)           # ← 第 55 行：把整批的 M 合起来只取整一次（下界，非平台口径）
        floor_min += bill(sum(F(str(orders[o]['weight'])) for o in names) + trim_b, w)
```

- 也就是说：`billed` 是 `Σ_轮 bill(该轮用料)`，而 `floor_cur` 是 `bill(该批总用料)`。
  差额（4.84M kg）来自"合起来只取整一次"这个更宽松的算法，**平台不会这么算**。
- 复现：`./.venv/Scripts/python.exe -X utf8 runs/_requant_report.py artifacts/runs/requant/plan_deep1.json`
- 旁证：本地**逐轮**口径的成材率与官方**逐位相同**（§C2 的 15 项）。若平台是按批取整，两者不可能五包全等。

### C5 「自己材料的地板」那 4.84M kg **不是可达靶**

- 这是本轮最重要的自我纠错，也是我们请审核者重点检查的一条**推理**（不是测量）。
- `runs/_requant_report.py:55` 的 `floor_cur += bill(M, w)` 把**一批的各轮用料合起来**只取整一次，
  而平台是逐轮算（§C4）。所以「离自己材料的地板还差 4.84M kg / +0.336 分」是一个**下界**，
  差额里真正能收的只有**取整边界的条数**，也就是**轮数**——少一轮就少一条边界、还少一把刀。
- 后果：我们把攻击面从「省材料」改成了「减轮数 / 减刀数」，并据此设计 Wave H。

### C6 「合并轮」招式与那次被否证的直觉

- 代码：`tools/analysis/requant_batches.py` 的 `merge_kick()` / `iterated_merge()`，
  CLI `--merges/--merge-depth/--merge-seed`。动机：原 `polish()` 的删轮招式只能按比例缩放
  件数并**保留幸存轮的切法**，所以两个订单集不同的轮永远合不到一起。
- 测试：`tests/test_requant.py::MergeKickTests`（合成 4 轮并集、2 轮不动、真实批次 bi=1800）。
- **同预算对照（各 150 次重启，47 个抽样批）**：纯 kick 命中 3 个改进 / 合并 depth-1 命中 1 个 /
  depth-3 命中 2 个，命中批次高度重叠。结论与直觉相反：**卡住中间批的不是招式种类，是重启预算**
  （同一样本 50 次重启只有 1 个命中，150 次有 3 个）。因此 Wave H 买的是预算不是新邻域。
- 对照日志在 `runs/logs/cmp_merge/*.log`、脚本 `runs/_cmp_merge.sh`——**两者都不入库**（见 §3）。

### C7 其余可复现的支撑

- 167 个测试全绿：`./.venv/Scripts/python.exe -X utf8 aic.py test` → `Ran 167 tests … OK (skipped=1)`。
- 轨迹顺序决定落点（92.1402–92.1572，盆地宽 0.017）：`tools/analysis/trajectory.py`，
  明细见 `docs/OVERPRODUCTION_TRIM.md`。
- 平台校验器的双实现与四锚点复现：`src/platform_check.py`、`src/platform_score.py`、
  `tests/test_clause6_anchors.py`。

## 3. 不在 GitHub 上的东西（审核盲区，请对照本节）

仓库 `.gitignore` 排除 `runs/`、`artifacts/candidates/`、`artifacts/runs/`、`tmp/`、`*.log`。
这是仓库既定策略（生成物不入库、未提交的候选包是本队参赛资产），代价是**下列证据你看不到**：

| 不在库里的 | 位置 | 为什么重要 | 怎么补 |
|---|---|---|---|
| 五个已提交包的 ZIP 与计划 JSON | `artifacts/candidates/<候选>/` | §C2 的对账就是对这些 JSON 重算的 | 在库里有 `artifacts/accepted/official_scores.json` 记录了它们的哈希与回执；原始包需本机 |
| `plan_deep1.json` 等计划（本地最高 92.4322） | `artifacts/runs/requant/` | §C1/C4/C5 的数字来源 | 需本机；`runs/_requant_report.py <plan>` 复算 |
| `runs/_requant_report.py`、`_traj.py`、波次脚本、日志 | `runs/` | §C4/C5 的逐轮 vs 按批口径差就在这个文件里 | 决定性那 16 行已逐字抄进 §C4，无需本机即可核；整文件仍在本机 |
| Wave C–H 的补丁与日志 | `artifacts/runs/requant/patches_*/`、`logs/` | §C6 的对照原始数据 | 需本机 |
| 平台台账原卷 | `D:\new_mcp\submission_ledger.jsonl`（**另一个仓库，只读，不推**） | 所有官方分的最终原始记录 | 本机；或在平台结果页按 `docs/WINDOW_RUNBOOK_20260927.md` 的命令重取 |

**已知的核验缺口（我们不想掩盖）**：五张回执里，只有 `cand_rp4` 带平台侧的
`attachmentSha256` 且 `attachmentMatches: true`；另外四张是 `unverified-download-failed`——
那是 Edge 153 自身的下载崩溃（`docs/PIPE_ATTACHMENT_EDGE153.md`，含转储指纹），
**发生在提交之后、只影响附件哈希**。我们今天的补救核验是：五个本地 ZIP 的当前哈希
仍等于提交时记进台账的哈希（`local_zip_eq_ledger_sha: true`），且 ZIP 内载荷与那份被重算的
JSON 逐字节相同（`zip_payload_eq_loose_json: true`）。也就是说，**「被重算的字节 == 提交的字节」
这一环我们核过了；「平台收到的字节 == 我们提交的字节」这一环只有 `cand_rp4` 有平台侧背书。**

## 4. 请重点证伪（红队清单）

按我们自己的估计，从最可能出错排起：

1. **§C2 的度量模型是否过拟合四张回执？** 模型在 09-26 夜按四张回执改过两处
   （权重 0.4/0.4/0.2/0 与半成品分子口径，提交 `69ce957`）。五包里有四包是**样本内**。
   唯一的事前预测是 `cand_rp4`。要真正证伪，需要第六个独立样本——今晚的四发正是为此排的，
   发完可回来查。
2. **残留的四舍五入口径不确定**：本地未取整分 vs 官方先各自取整到两位再加权，两种算法在
   五包上给出同一个两位小数，但理论上会在 `.005` 边界附近差 0.01。五包真值离最近边界
   都有 0.0010 以上，所以**没被区分**。若今晚某发的真值落在边界附近，就能分辨。
3. **本地校验器有已知判据缺口**——本地过 ≠ 平台收。这是血债：`submission_semi_merged_v2`
   （7,030 条违规）与 `v4`（7,342 条跨轮接续）都被平台判**不可行、0 分**，
   原始回执在 `artifacts/rejected/*/official_feedback.json`。那次教训是「边界的两种读法」，
   修复过程见 `docs/ARCHIVE_AUDIT.md`、`docs/EVIDENCE.md`。请检查 `src/platform_check.py`
   是否还有未被回执证伪过的判据。
4. **§C5 的推理链**（逐轮账单 ⇒ 4.84M kg 地板不可达）是全篇最长的推理。若平台其实按批
   取整，结论就反转。请检查 §C4 里逐字抄出的第 47 行（逐轮累加）与第 55 行（按批取整）
   的差别是否真能支撑「可达的只有轮数」这个结论。
5. **Wave H 的 12 工位是并行搜索**，其中 6 个用 `merge_kick`、6 个用纯 `kick`。若你发现
   `merge_kick` 产出的状态**违反某个我们在 `evaluate()` 里没建模的平台规则**，
   那 Wave H 的产物整体不可信。请重点看 `tools/analysis/requant_batches.py` 的
   `merge_kick` / `evaluate` / `polish(..., allow_illegal=True)` 这条路径。
6. **覆盖率的长尾**：`cand_deep1` 的覆盖率是 99.99%（9,998/9,999），剩一个是结构性独占订单
   `B20271919`（0.002 分）。若我们的 `COVER_KG` 定价（28,838 kg-eq）算错，
   那么 Wave C–H 里所有「主动放弃共享换材料」的补丁都可能是亏的。
7. **数据/规则版本**：我们按 `data/semi/constraints.txt` 与群答疑锚点工作
   （`docs/RULES.md`）。若官方口径在 09-27 之后又变了，全部结论需重算。

## 5. 硬约束与红线（我们自己在遵守的）

- **任何已评过 / 已发过的包绝不重发**。队列里两行陈旧记录
  （`ff0a9e61` 的旧 `cand_rounded` 条目、`1a62b92e` 已评 90.59）**永不提交**。
- 每次提交前用 `aic_submission_plan` 做只读预检；管道需显式 `confirm_real_submit=true` 才真点。
- **07:00 前不提交**；每日 5 发上限。
- **密钥与令牌不进仓库**：本机的迁移密钥文件、模型 API 令牌、原始会话凭据、浏览器登录状态
  一律不入库；本文档也不点名它们的具体路径。仓库里没有任何密钥材料（可自行 grep 复核）。
- 不改原始证据字节：原包哈希、平台原始回执、来源版本可追溯。本仓库里的
  `artifacts/accepted/official_scores.json` 是**副本**，唯一改动是删掉 `attachmentUrl`
  （那是我方已提交包的直链，公开它等于把包送出去）；`D:\new_mcp` 原卷未动。
- `D:\new_mcp` 是公开仓库：只读，不提交、不推送。

## 6. 本机怎么复现全部结论

```bash
cd /d/02_Projects/ML/jinyinsai1_nolimit
./.venv/Scripts/python.exe -X utf8 aic.py test                                  # 167 tests
./.venv/Scripts/python.exe -X utf8 aic.py check <plan> --round semi --data data/semi
./.venv/Scripts/python.exe -X utf8 aic.py score <plan> --round semi --data data/semi
./.venv/Scripts/python.exe -X utf8 runs/_requant_report.py artifacts/runs/requant/plan_deep1.json
```

时间口径：本机时间 == 北京时间（UTC+8）。工具链一律走 `aic.py`；源码在 `src/`、
测试在 `tests/`、产物在 `artifacts/runs/`。

## 7. 相关文档

- 当前状态：[`docs/CURRENT.md`](CURRENT.md)
- 评分公式与五包对账：[`docs/SCORE_MODEL.md`](SCORE_MODEL.md)
- 超产转换、三条新遍历、地板与轨迹：[`docs/OVERPRODUCTION_TRIM.md`](OVERPRODUCTION_TRIM.md)
- 提交实验读法：[`docs/SUBMISSION_PLAN_20260926.md`](SUBMISSION_PLAN_20260926.md)
- 今日/明日窗口手册：[`docs/WINDOW_RUNBOOK_20260927.md`](WINDOW_RUNBOOK_20260927.md)、
  [`docs/WINDOW_RUNBOOK_20260928.md`](WINDOW_RUNBOOK_20260928.md)
- 规则：[`docs/RULES.md`](RULES.md)　证据与存档审计：[`docs/EVIDENCE.md`](EVIDENCE.md)、
  [`docs/ARCHIVE_AUDIT.md`](ARCHIVE_AUDIT.md)
- Edge 153 下载崩溃的证据链：[`docs/PIPE_ATTACHMENT_EDGE153.md`](PIPE_ATTACHMENT_EDGE153.md)

## 8. 希望你给什么

最有用的审核输出（按价值排序）：

1. **逐条裁决** §2 的 C1–C7：哪几条你能从仓库内独立验证、哪几条你只能接受我们的说法
   （后者请点名，我们好补证据）。
2. **§4 红队清单里你实际能打穿哪一条**，以及我们没想到的错法。特别是 C2 的度量模型：
   若你能从 `src/platform_score.py` + `data/semi/` 独立复算五包的分项，请直接给出你的数字，
   与上面那张表对。
3. **指出任何"结论跑在证据前面"的地方**——我们更怕被自己的口径骗，不怕被指出错误。
4. 若发现问题，请给到 `文件:行号` 与一个可执行的判定命令，我们会当轮修复并回写文档。

## 9. 审计回应与已落地的修改（2026-09-27 15:30 追加）

审计报告已回。上节原文一律保留（不改历史），本节的裁决与修改与之并列。审计的主判断
——「与前十最大的差距在于**对'还有哪些解值得搜索'的判断**，而不是局部优化能力」——我们接受，
并按它重排了优先级。逐条：

| 审计点 | 裁决 | 落地 |
|---|---|---|
| §2 独立下界：账单不变则 `S ≤ 92.851 < 93`，**材料必须降** | **接受**（算术复核一致） | 记入 [CURRENT.md](CURRENT.md) |
| §5 `requant_batches.py:495` 账单差符号写反 | **属实** | 已修，见下 |
| §5 `allow_illegal` 不能走"违规下降"路径 | **属实且更重要**（修复时才发现它会让整条合并招式消失） | 已修 |
| §6 `solve_semi.py` 入口仍旧口径 | **属实**（原注释还引用了一个不存在的函数 `_demand_numerator_mass`，把**初赛**口径当成半决赛口径） | 注释已改；**数值刻意不改**，理由见 [CURRENT.md](CURRENT.md) |
| §4 「只有轮数能收」是推理跳跃 | **属实**，反例已抄进 [CURRENT.md](CURRENT.md) | 已改 |
| §7 文档漂移：`WINDOW_RUNBOOK_20260928.md:24` 的 "本地 +0.0045" | **属实**（该口径早已被四舍五入口径取代） | 已改 |
| §4 「count 整个约掉了」「坯料类型已全局最优」两处过度声明 | **属实**（前者只有连续项约掉，后者只是"给定轮型"下的局部结论） | 已改 |
| §3 跨批联合重建被过早放弃 | **接受，已按建议在最小窗口实测**：边界锁住 **0** 收益（联合同轮数、重划分逐位相同），锁住结构的是**接续规则** | 见下 |

**§5 的符号修复及其带出的两个项**（`tools/analysis/requant_batches.py`，判定谓词 `worth_exact()`）：
审计指出的符号确实写反，方向恰好**挡住"省整根坯料 + 顺手省刀"的招式**。全卷 2,303 批实测：
修正后 **9 批**在旧筛选器的不动点上严格改进、**无一变差**，合计 **18,255 kg-eq（≈+0.0013 分）**
—— 量级很小，与审计的判断一致：这**不是**差 1.67 分的原因。修的过程中又发现两个筛子**本来就没算**
的项，两个都由 `tests/test_requant.py::MergeKickTests.test_a_merge_beats_the_descent_on_the_batch_it_was_found_on`
反着守住（改回任一处该测试立红）：(a) 整轮被腾空时少算该轮自己的 `+1` 刀（漏掉它 → bi=1800 的合并
65→62 刀整条消失）；(b) 筛子的经济判据只在**在位状态合法**时等于 `better()` 的判据，从违规态修复时会
挡住恰好清掉违规的招式。细节与实测见
[OVERPRODUCTION_TRIM.md](OVERPRODUCTION_TRIM.md#那道浮点预筛的符号写反了09-27-下午外部审计指出)。

**§3 跨批联合重建：已做（最小窗口），结论是"不扩大"（09-27 15:20）。** 审计建议"先用小窗口得到高质量
结果或最优性界，验证当前批次边界到底锁住了多少收益，再决定是否扩大"。我们在**最小的 (钢种,直径) 组**
`XL2:38.0`（20 单，整组正好一个 chunk，没有组内分块的干扰）上做了三组对照，**全部在半决赛口径下量**：

| 做法 | 方案 | seam | 轮 | 刀 | 账单 kg |
|---|---:|---:|---:|---:|---:|
| 现状（`plan_deep1` 里这 20 单的 6 批） | 6 | **0** | 22 | **373** | **1,064,219** |
| 联合从头解（`solve_semi.py --groups XL2:38 --chunk 60 --offset-mode anchor --offset-anchor plan_deep1.json`） | 5 | **17** | 22 | 375 | 1,118,864 |
| 接续感知重划分（`repack_batches.py` 喂同一组的 6 批，完全自由重划） | 6 | **0** | 22 | **373** | **1,064,219** |

联合从头解把 20 单的自由度用满，**轮数一样是 22**，刀 +2、材料 +54,645 kg，还**违反接续规则 17 次**（不可行）；
接续感知的重划分给的是**逐位相同**的计划。**边界在这个窗口上锁住的收益是零。**
原因不是边界：接续规则禁止相邻两轮共享 ≥2 个订单，而当前全卷 7,989 条相邻轮边界里 **95.3% 共享订单、
每条恰好只有一个共享订单坐在接缝上**（全卷 seam = 0）——这套结构的每个接缝都是**单订单交班**，
联合重建要去的方向按构造就会产生 ≥2 共享。**锁住结构的是接续规则，不是批次边界。**

**顺带回答红队第 5 条**：`tools/analysis/requant_batches.py` 的 `evaluate` **建模了**接续规则
（相邻轮共享 >1 即记 200,000 违规，配 `seam_keys` 修缝），所以 Wave H 的产物在这一点上是可信的；
**不建模的是 from-scratch 求解器** `src/solve_semi.py` 的 `search_10s`（早于 09-26 那条规则），
上面那份 17 seam 的"更省刀"解正是它吐出来的。未来任何联合重建必须建在 `requant_batches` 的合法性模型上。

**质量分解的闭合**（附带修正）：`runs/_requant_report.py` 原先把订单表的 `weight`（分子口径）与
`pieces × size × linear`（几何口径）混在一张分解表里，缺 **346,664.57 kg** 的"表差"（9,999 行行行不同）。
现已单列该项，并加一条精确分数的 `assert` 守住恒等式：
`536,301,947 = 506,157,994 + 11,379,649 + 5,927,276 + 12,490,364 + 346,665`。

**测试**：`./.venv/Scripts/python.exe -X utf8 aic.py test` → `Ran 173 tests … OK (skipped=1)`。
