# 命令入口与验证边界

统一使用 `python -X utf8 aic.py <命令>`。所有相对路径以项目根为基准；工具子进程继承 src 导入路径。
`aic.py` 不需要 pip 安装，使用 Python 标准库；它支持从仓库外通过绝对路径启动。

| 任务 | 命令 |
|---|---|
| 参数帮助 | `python -X utf8 aic.py --help` |
| 全部现行回归 | `python -X utf8 aic.py test` |
| 指定回归 | `python -X utf8 aic.py test test_platform_score test_semi_check` |
| 数据预处理 | `python -X utf8 aic.py prepare --help`（会写 data，执行前保留原件） |
| 求解 | `python -X utf8 aic.py solve --help` |
| 单进程监督 | `python -X utf8 aic.py watch --help`（使用原 task_watcher，禁止密集轮询） |
| 岛级监督 | `python -X utf8 aic.py supervise --help` |
| 跨岛合并 | `python -X utf8 aic.py merge --help` |
| 打包 | `python -X utf8 aic.py build --help` |
| 校验/估分/ZIP验证 | `python -X utf8 aic.py check --help` / `score --help` / `verify --help` |
| 候选判据复算 | `python -X utf8 aic.py audit-clause6`（锚点复现，不代表认证） |
| 历史 B/C/G 对照 | `python -X utf8 aic.py audit-packages`（不能据此放行） |
| 重建历史报告 | `python -X utf8 aic.py resync --help`（会写未认证报告；不改 ZIP/legacy 原报告；退出 1 表示不可放行） |

输入在 `data/semi/`；七个旧复赛候选在 `artifacts/rejected/`；新打包默认写入 `artifacts/candidates/`。
`src/build_submission.py` 仍有旋转步骤；`tools/analysis/` 中两个旧审查脚本的“safe”“no-op”等结论仍有历史局限。
运行它们只用于复算旧模型，不能放行包；候选新判据见 [EVIDENCE](EVIDENCE.md)。

## 测试与检查点

- 活跃回归在 `tests/`；初赛反馈样本移入 `tests/fixtures/prelim/`，它们仍验证基础计分逻辑，没有作为无用数据删除。
- `runs/platform_fix/result.json` 是未入库的可选初赛运行产物；没有时该项显式跳过。整理前这一项因缺文件报错。
- 退役初赛内核/实验测试随完整工作区归档；需复现实验时解压快照，不要求复赛工作区安装旧 exe。
- 先恢复实际 runs，再检查岛配置、chunks 和完成状态；不能按旧日志自动重跑。
- 所有测试只约束现有实现，不证明未知官方判据已解决。
