# 棒材锯切复赛工作区

先读 [当前状态](docs/CURRENT.md)。本项目处理 1 万单复赛；现有七个候选尚不能直接放行。

```text
aic.py       统一命令入口
src/         复赛求解、校验、打包与监督代码
tests/       回归测试，以及仍被测试使用的初赛样本
data/        输入、规范化数据与复赛原始数据包
artifacts/   候选与历史拒绝包（产物，不是源码）
evidence/    官方 PDF、原始违规样本及校准证据
tools/       跨岛合并和审查工具
docs/        当前状态、规则、证据解释、计划与迁移
archives/    整理前完整受跟踪工作区的压缩快照和路径清单
```

在任意目录使用本项目 `aic.py` 的绝对路径，或在仓库根执行：

```powershell
python -X utf8 aic.py --help
python -X utf8 aic.py test
python -X utf8 aic.py solve --help
python -X utf8 aic.py merge --help
```

命令中的相对路径统一相对于项目根；调用者可传绝对路径。搜索输出放 `runs/`（忽略入库），打包默认放 `artifacts/candidates/`。

| 任务 | 文档 |
|---|---|
| 接手与下一步 | [当前状态](docs/CURRENT.md)、[解决顺序](docs/PLAN.md) |
| 实现/审查约束 | [复赛规则](docs/RULES.md)、[证据边界](docs/EVIDENCE.md) |
| 命令与测试 | [运行说明](docs/RUNBOOK.md) |
| 恢复机器 | [迁移说明](docs/MIGRATION.md) |
| 查旧路径、恢复初赛程序 | [工作区档案](archives/README.md) |
| 查旧文档原文 | [知识档案](docs/archive/README.md) |
| 查看整理验收 | [工作区整理](docs/WORKSPACE.md)、[前次文档压缩](docs/ARCHIVE_AUDIT.md) |

本次调整目录与调用路径；其后已按候选判据**修正两个生产校验器**，并加了一组对照四个官方回执的锚点测试（`tests/test_clause6_anchors.py`）。条款 6 的结构修复仍是原型，见 [当前状态](docs/CURRENT.md)。仍未提交比赛，本地检查通过也不等于官方认证。
MCP 位于独立的 [new_mcp 仓库](https://github.com/WRw5w/new_mcp)。
