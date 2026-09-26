# 历史档案索引

归档日期 2026-09-26；来源提交 `39957819cee78ab43355d053140778a4785a53ea`。原文含已撤回结论，只用于溯源。

- [压缩包](knowledge-before-20260926.zip)：70 份原 Markdown，459,035 B → 259,382 B（含清单）。
- [逐文件清单](manifest.json)：原路径、字节数、行数、SHA-256、分类和处理方式；ZIP 内也有 MANIFEST.json。
- ZIP SHA-256：`e54d74d6c5864311b7de2fee700888cafdcf9c9126908d08931175b1ce363376`。
- 原件在改写前已逐项回读验证哈希。数据、源码、平台 JSON、提交 ZIP 和 PDF 保留原路径。

| 需要追溯 | ZIP 内路径 |
|---|---|
| 初赛分数、刀数和投坯探索 | `README.md`、`HANDOFF.md`、`evidence/20260916/*.md`、初赛 submission 说明 |
| 赛题组答疑原转录 | `RULES.md`，尤其 §0、§11–13 |
| 条款 6 错误推断和撤回经过 | `diagnostics/clause6_*.md` |
| v4=7342 的提交叙述 | `diagnostics/clause6_rotation_falsified_20260926.md` |
| 两条算法路线、取整敏感性 | `COMPARISON_in_package_two_tracks.md` |
| 旧候选生成与评分说明 | `submission_semi_*/提交说明.md`、`submission_ours_*/提交说明.md` |
| 每日事件和旧自动化日志 | `.workbuddy/memory/` |

在仓库根执行（目标为新的独立目录，不能覆盖当前仓库）：

```powershell
python -m zipfile -t docs/archive/knowledge-before-20260926.zip
python -m zipfile -l docs/archive/knowledge-before-20260926.zip
python -m zipfile -e docs/archive/knowledge-before-20260926.zip ../jinyinsai1_history_20260926
```

查阅后只更新有价值的现行事实，不把整份历史重新粘入入口。
旧路径保留短指针；初赛拒绝标记、legacy README、专用算法说明保留原文供定向使用。

整理依据与验收结果见 [信息密度评估](../ARCHIVE_AUDIT.md)。
