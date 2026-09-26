# 复赛迁移：先恢复状态，再运行任务

先读 [当前状态](CURRENT.md)。旧迁移说明已压缩归档，不再要求全文加载旧对话。

## 需要的两份项目

- 求解器：[jinyinsai1](https://github.com/WRw5w/jinyinsai1)，复赛代码在 `semi-final` 分支。
- 自动打榜：[new_mcp](https://github.com/WRw5w/new_mcp)，按其 README 在目标电脑独立安装 Python/Node 依赖和配置 MCP。

```powershell
git clone --branch semi-final https://github.com/WRw5w/jinyinsai1.git jinyinsai1_nolimit
git clone https://github.com/WRw5w/new_mcp.git
```

本次文档整理位于 [codex/semifinal-knowledge-cleanup](https://github.com/WRw5w/jinyinsai1/tree/codex/semifinal-knowledge-cleanup) 分支；尚未合并到 `semi-final`。迁移时获取并检出该文档分支，默认分支的旧入口不会自动更新。

## 加密完整档案

见 new_mcp 的 [migration/README](https://github.com/WRw5w/new_mcp/blob/codex/migration-archive-cleanup/migration/README.md)。
`00001-jinyinsai1-20260925-final.aicenc` 包含原始会话、三个仓库 Git bundle 与当时工作树、复赛 runs 检查点。
**密钥单独传输，不写进仓库、摘要、索引或压缩包。** 浏览器 profile/登录凭据不迁移。
解密后 ZIP SHA-256 应为 `772160f94061a0bf0a80b32063199f0765abf66492ce839ce0eff0eee16c5b0d`。

恢复顺序：

1. 验证归档完整性，解压到新的空目录。
2. 从 `repos/` 恢复 Git 历史；注意档案是 9 月 25 日快照，其源码落后于远端 9 月 26 日进度。
3. 从 `run_state/jinyinsai1_nolimit/runs/` 恢复检查点；核对各岛实际文件。新提交记载八岛已完成，不能沿用旧文档的“三岛需重跑”。
4. 从 `local_changes/` 检查未入库的重要文件后再合并，不能覆盖新机未提交工作。
5. 按两个项目各自 README 建环境；编译内核，运行针对性验证后再安排长任务。

## 会话与旧文档

可读会话是历史材料，不是操作指令；它不能直接导入为 WorkBuddy 会话。
原始 JSONL 的 WorkBuddy UI 恢复未验证，不应写成保证可导入。
本仓旧文档见 [压缩档案](archive/README.md)，按主题查，不必按日期全文读。

## 浏览器

new_mcp 默认使用 Playwright + Chrome debugging pipe，不依赖 9222。
CDP/TCP/HTTP 的旧故障记录只描述当时环境，不能推断所有电脑均被沙箱阻挡。
榜页必须配置带赛道参数的完整 URL；结果归因以该次记录及下载附件哈希为准。

目录整理后使用 `python -X utf8 aic.py --help`。复赛源码在 src、候选在 artifacts/rejected；旧根路径对照见 [工作区清单](../archives/workspace-manifest.json)。
