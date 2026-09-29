# 棒材组合订单高效锯切优化 —— 复赛求解工作区

AIC-产业命题赛-AI＋钢铁 · 复赛 · 队伍 **鱼不吃猫**（参赛编号 AIC-2026-93096493）。

**先读 [当前状态](docs/CURRENT.md)**；结论会随实验更新，那里才是权威。

## 当前战绩

| | 分数 | 说明 |
|---|---:|---|
| 官方最高 | **92.58** | 2026-09-28 23:01:52 出分（刀 184,444 / 材 94.69% / 覆 100.0%） |
| 本地最好计划 | **92.6085** | `artifacts/runs/requant/plan_r14.json`，已跨 92.6051 ⇒ 预计官方 92.61 |
| 官方提交 | 15 次真实提交，全部受理为**可行**，无一次结构性失败 |

评分口径（由五张官方回执逐位标定，见 [SCORE_MODEL](docs/SCORE_MODEL.md)）：

```text
总分 = 0.4 × (100 × 160000 / 刀数) + 0.4 × 成材率(%) + 0.2 × 覆盖率(%)
官方分 = round(本地预测分, 2)        无时间项
```

## 方法一句话

以一张基准计划为底，只在**批次（订单组合）粒度**做局部替换。因为刀数与用料都是批次上的和、
覆盖率恒为满分，互不冲突的批次补丁可以**精确相加** —— 于是「搜索更优计划」变成
「并行、增量、可回滚地寻找单批次改进」。换种子链扫描是当前唯一仍有产出的杠杆。
完整论述见 **[技术报告](docs/REPORT_TECH_20260929.md)**（PDF：[`docs/REPORT_TECH_20260929.pdf`](docs/REPORT_TECH_20260929.pdf)）。

## 目录

```text
aic.py       统一命令入口
src/         复赛求解、校验、打包与监督代码
tests/       回归测试，以及仍被测试使用的初赛样本
data/        输入、规范化数据与复赛原始数据包
artifacts/   候选包与计划（产物，不是源码）
evidence/    官方 PDF、原始违规样本及校准证据
tools/       跨岛合并、审查与报告生成工具
runs/        扫描中间件与日志（约 41 MB，忽略入库）
docs/        当前状态、规则、证据解释、计划与迁移
archives/    整理前完整受跟踪工作区的压缩快照和路径清单
```

在仓库根执行（相对路径均相对于项目根，也可传绝对路径）：

```bash
python -X utf8 aic.py --help
python -X utf8 aic.py test          # 回归测试
python -X utf8 aic.py check <json> --round semi --data data/semi
```

**本机注意**：Windows 应用控制策略会拦截 `.venv/Scripts/python.exe`，
上述命令需改用真实解释器（如 `C:\Users\s1706\AppData\Roaming\uv\python\...\python.exe`），
详见 [当前状态](docs/CURRENT.md) 的运维条目。

## 打包一个候选（两步，缺一不可）

```bash
python -X utf8 src/build_submission.py --input artifacts/runs/requant/plan_r14.json \
    --team 鱼不吃猫 --output-dir artifacts/candidates/cand_r14 --round semi --data data/semi
python -X utf8 tools/resync_reports.py artifacts/candidates/cand_r14 --data data/semi
```

第二步会把报告重写成测试与漂移检查期望的 schema，并恒把 `submission_allowed` 置为 `false` ——
**本地校验通过不等于平台认证**，任何查看候选包的人都应按这个前提理解那些数字。

## 文档索引

| 任务 | 文档 |
|---|---|
| 接手与下一步 | [当前状态](docs/CURRENT.md)、[解决顺序](docs/PLAN.md) |
| 技术路线与实验记录 | [技术报告](docs/REPORT_TECH_20260929.md)、[逐日状态](docs/STATUS_20260928.md) |
| 评分公式与逐位对账 | [评分模型](docs/SCORE_MODEL.md) |
| 实现/审查约束 | [复赛规则](docs/RULES.md)、[证据边界](docs/EVIDENCE.md) |
| 提交操作步骤 | [窗口手册](docs/WINDOW_RUNBOOK_20260928.md) |
| 命令与测试 | [运行说明](docs/RUNBOOK.md) |
| 恢复机器 | [迁移说明](docs/MIGRATION.md) |
| 查旧路径、恢复初赛程序 | [工作区档案](archives/README.md) |

MCP 位于独立的 [new_mcp 仓库](https://github.com/WRw5w/new_mcp)。

## 诚实边界

- **本地校验通过 ≠ 平台接受**。本地校验器是对约束的再实现，「相邻轮次连续锯切」的判据
  由四次官方回执认证为正确，但仍是对语义的推断而非对平台源码的读取。
- 换种子扫描的收益**噪声主导**：单轮整卷收益在 +0.0006…+0.0047 之间，
  任何「再一轮就够了」的推断都不成立（文档里被实测打脸三次）。
- 「官方分 = 本地分四舍五入」在真值距 `.005` 边界极近时存在两种口径的歧义，
  当前五包无法区分（最近的真值离边界 0.0010）。
- 实际提交须有用户授权；本工作区不代用户消费提交名额。
