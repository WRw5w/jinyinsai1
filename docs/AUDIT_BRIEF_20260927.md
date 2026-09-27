# 送审说明（2026-09-27 14:00）：本轮进展、证据链，以及请重点证伪的点

这份文档写给**外部审核 AI**。前提：你只能看到这个 GitHub 仓库，看不到本机工作区。
所以下面每条主张都标了「仓库内证据」与「复现命令」，并单列一节说明**哪些东西不在仓库里**
（审核时你会遇到的盲区，以及怎么按需补上）。

队名 `鱼不吃猫`，参赛编号 `AIC-2026-93096493`，阶段 `semi`（AIC 2026 复赛·棒材组合订单
高效锯切优化）。结果截止 2026-10-05 20:00，代码截止 2026-10-07 23:59。

---

## 0. 一句话

本地最好计划 **92.4322**（`plan_deep1`，未提交），官方最高 **92.12**（`cand_rp4`，09-27 07:06
实测，榜面第 28/40，榜首 93.79）。**评分公式已用五个包逐位对账确认**，今晚还有 4 次提交额度，
排了 4 发（19:05 / 20:30 / 22:00 / 23:15）。第六波搜索（Wave H）正在跑。

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
|---|---|---|---:|---|---:|---|
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
  half-open、防浮点边界）；`runs/_requant_report.py:47` 在**轮循环里**累加 `billed += bill(m, w)`。
- 复现：`./.venv/Scripts/python.exe -X utf8 runs/_requant_report.py artifacts/runs/requant/plan_deep1.json`
- 旁证：本地逐轮口径的成材率与官方**逐位相同**（§C2 的 15 项）。若平台是按批取整，两者不可能五包全等。

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
| `runs/_requant_report.py`、`_traj.py`、波次脚本、日志 | `runs/` | §C4/C5 的逐轮 vs 按批口径差就在这个文件里 | 需要的话可以把 `_requant_report.py` 移进 `tools/analysis/` 入库 |
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
   取整，结论就反转。请检查 `runs/_requant_report.py:47` 与 `:55` 这两行的差别是否真能
   支撑「可达的只有轮数」这个结论。
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
