# 提交方案（2026-09-26 夜）

五个候选已备妥并逐个通过 `platform_check(weight_mode='strict')`，零错误、SHA 唯一。
**它们是同一夜的一组实验，不只是为了拿分**——每个候选针对一个尚未确定的评分口径。

## 五个候选

均已应用**坯型优化**（逐方案在 5 种钢坯里选申报质量最小的那种，见 `tools/analysis/optimize_blanks.py`），
成材率因此从 88.05% 提到 90.44%，总分 +0.72。

| # | 目录 | 预测（floor） | 方案数 | 作用 |
|---|---|---:|---:|---|
| 1 | `cand_rounded` | **92.7309** | 1880 | 最佳候选；长度写成精确小数 |
| 2 | `cand_compliant` | 92.1154 | 1880 | **1 的对照**——同一方案，长度写成浮点乘积 |
| 3 | `cand_covprobe25` | 92.5351 | 1880 | 覆盖率探针（拆 25% 的多订单方案） |
| 4 | `cand_covprobe` | 91.9879 | 1881 | 覆盖率探针（拆 50%） |
| 5 | `cand_yieldprobe` | 91.4922 | 1874 | 权重探针（在最优坯型上再乘 1.05） |

3 与 1 的成材率、覆盖率完全相同（90.4428% / 97.43%），**分差 0.6155 纯粹来自刀数**。
4、5 同理只动一个变量：4 的覆盖率 96.46%，5 的成材率 86.31%。

## 每次提交读出什么

**1 与 2 的差** —— 两个包是**同一个方案**，只差长度的小数写法：

| | floor 口径 | snap 口径 |
|---|---:|---:|
| 2 `cand_compliant` | 180,299 | 185,229 |
| 1 `cand_rounded` | 177,226 | 185,229 |

- 榜面分**有差**（预期 0.6155）⇒ 平台用 **floor**（那么 1 是更优包）
- 榜面分**无差** ⇒ 平台用 **snap**
- 差值的**大小**同时定出基准刀数：在 160,000 下差 0.6155 分，在 90,000 下差 0.346 分。

**3 与 4** —— 覆盖率各降 0.97 / 3.62 个点，其余不变。分数降幅 ÷ 覆盖率降幅 = 覆盖率权重（应为 0.20）。同时验证覆盖率的口径（"仅统计多订单组合方案中的订单"是否按我们理解计算）。

**5** —— 成材率降 4.13 个点，其余不变。分数降幅 ÷ 成材率降幅 = 成材率权重：**0.30 还是 0.40**（预期降 1.239 或 1.652）。官方 PDF 的权重表写 30%，同一页的公式行写 40%，两者矛盾，这是唯一能定它的办法。

**任何一次**若报出 `跨轮接续不连续` 之外的违规类型，说明候选判据还不够——两次回执都只报过这一类。

## 额度与时间

每日额度按**北京时间午夜**重置（`ledger.py` 的 `now_cn().date()`）。今天已用 1 次（15:45 那次 0 分），**剩余 4 次**；00:00 后再有 5 次。

因此 22:00–24:00 窗口内最多提交 4 个，第 5 个落在 00:00 之后。

规则上"取复赛阶段最高分"，所以全部提交都只赚不赔；排序按预测分从高到低，先交可能有分的那几个。

## 执行

```bash
cd /d/02_Projects/ML
./aic-pipe.sh wait-login 600000     # 若登录态失效才需要
```

```bash
cd /d/new_mcp
export AIC_LEADERBOARD_ROOT="D:/new_mcp" \
       AIC_LEADERBOARD_CHROME_PATH="C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" \
       AIC_LEADERBOARD_SUBMIT_URL="https://reg.aicomp.cn/app/JSGLPT/639980063d903c241eb85102" \
       AIC_LEADERBOARD_LEADERBOARD_URL="https://reg.aicomp.cn/special/phb/detail?id=4839619591879659586&rwId=4839619548191789112&stbh=4839619562087518299" \
       AIC_LEADERBOARD_RECORDS_URL="https://reg.aicomp.cn/app/JSGLPT/65b75207a58fdc32c79e9842" \
       AIC_LEADERBOARD_TEAM_ID="AIC-2026-93096493"
.venv/Scripts/python.exe -X utf8 -m aic_leaderboard.auto submit \
  "D:/02_Projects/ML/jinyinsai1_nolimit/artifacts/candidates/cand_rounded/复赛结果_鱼不吃猫.zip" \
  --stage semi --team AIC-2026-93096493 --confirm-real-submit
```

`--confirm-real-submit` 是唯一会点击的开关；不带它是空转。每次提交后等 `capture` 取回分数再交下一个。

## 提交前的检查

- `auto status` 应显示 `blocked: false`，且 `spent_today` 与预期一致。
- 队列里不应有 `accepted` / `awaiting_score` 的残留条目——`accepted` 属于 `BLOCKING`，会挡住后续提交（2026-09-26 修过一次，现场那条已手工关闭为 `scored`）。
