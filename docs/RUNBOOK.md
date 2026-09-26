# 运行入口与验证边界

Python 命令在仓库根运行；Windows 使用 `python -X utf8`。C++ 内核需在目标机器编译，`.exe` 不迁移。
先看 [CURRENT](CURRENT.md)，源码中的旧算法仍能运行，但不是可提交认证。

| 任务 | 入口 |
|---|---|
| 复赛原始输入与规范化数据 | `data/semi/`；`prepare_semi.py`（会写数据，先保留原件） |
| 求解与恢复参数 | `python -X utf8 solve_semi.py --help` |
| 进程监督 | `python -X utf8 task_watcher.py --help`；长期任务不要密集轮询 |
| 岛级监督 | `python -X utf8 supervise_island.py --help` |
| 跨岛择优 | `python -X utf8 diagnostics/merge_chunks.py --help` |
| 打包参数 | `python -X utf8 build_submission.py --help`；当前仍有旋转步骤，输出需新判据复核 |
| 本地模型回归 | `python -X utf8 -m unittest test_platform_score test_semi_rules test_semi_check test_semi_solver` |
| 自动打榜 | [new_mcp README](https://github.com/WRw5w/new_mcp/blob/main/README.md) |

测试通过只说明现有实现满足其测试，不能证明条款 6 已正确或某包可提交。
`diagnostics/clause6_rotation_falsified.py` 仍仅演示旧 B 与排序的差异，不实现本次审查的候选式；
`diagnostics/verify_clause6_readings.py` 的“safe”输出仍有旧含义，不能直接采信。

## 复用检查点

1. 先从迁移档案恢复 runs，列出岛、chunks 数量及各自配置/种子。
2. 以实际文件核对完整性，不能根据 9 月 23 日未完成列表直接重跑。
3. 对候选应用修正后的独立校验；旧模型高分不能作为择优唯一条件。
4. 旧命令配方在 [历史档案](archive/README.md) 中，按路径取用；缺少输入时先恢复，避免意外从头长跑。
