# 工作区整理验收

日期：2026-09-26；基于文档整理提交 `cfe33a3dc16c0ff2600b4a1d1375af32c52fc27f`。
本次在 jinyinsai1_docs 与 new_mcp_docs 原地整理，没有再创建新的项目副本。
旧 jinyinsai1/main、nolimit 和其他用户项目没有覆盖。

## 目录变化

- 根目录总条目：128 → 15；其中可见入口 12 个，说明/命令文件 4 个，分类目录 8 个。
- 原受跟踪文件 383 个；282 个旧路径退役归档，69 个路径迁入新的分类，32 个路径保留。
- 旧完整工作区 39,642,058 B → 13,209,016 B 的压缩快照；逐项 SHA-256 和恢复验证通过。
- 新工作树包含保留代码、数据、候选和上述归档，约 32.2 MB；原来约 39.6 MB。
- Git 历史未改写，以上不表示整个 Git 对象库或其他原工作目录同比缩小。

原路径与新路径对照见 [manifest](../archives/workspace-manifest.json)。
恢复说明见 [archives](../archives/README.md)。原始数据、复赛候选 JSON/ZIP 和回归样本字节均保持一致。

## 调用方式变化

```powershell
python -X utf8 aic.py test
python -X utf8 aic.py solve --help
python -X utf8 aic.py build --help
python -X utf8 aic.py watch --help
```

过去的根目录模块已进入 src；原 diagnostics 中保留的工具进入 tools/analysis。
统一入口设置导入路径和项目根目录，从其他工作目录也能启动。相对参数相对于项目根。
默认打包目录改为 artifacts/candidates；监督回执记录 src 与复赛 data/semi 的源码/数据哈希。
`audit-clause6` 使用明确未认证的候选判据；原排序结论脚本已停用，完整原文仍在工作区 ZIP。`audit-packages` 仅保留历史模型对照，不能放行；见 [证据边界](EVIDENCE.md)。

## 验证

- 复赛原 64 项回归在整理前有 1 项失败：缺少未入库 runs/platform_fix/result.json。
- 首轮整理后 67 项；审核修正后 73 项：72 通过，1 项可选历史运行缺失而显式跳过。增加候选判据反例及旧模型不能重新放行的回归。
- 13 个带参数帮助的运行入口从项目外目录启动通过。
- 383 个归档文件实际解压后逐项哈希匹配，101 个保留/迁移映射均有实际文件。
- MCP 首轮整理移动测试入口；后续增加 Git blob 归档验证，完整 37 项离线测试通过，服务/插件入口未移动。
- 没有运行长时间求解、联网浏览器或比赛提交。

## 工作区维护

源码进 src，测试进 tests，实验输出进 runs，新包进 artifacts/candidates。
旧产物保留证据后转归档，不再把数十个 submission 目录平铺到根部。
历史压缩包按需解压到独立目录；不要解压覆盖当前工作区。
