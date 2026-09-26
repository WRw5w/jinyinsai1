# 整理前的完整工作区

来源提交 `cfe33a3dc16c0ff2600b4a1d1375af32c52fc27f`；归档 2026-09-26。
[压缩包](workspace-before-20260926.zip) 保存 **383 个原受跟踪文件**，原路径不变。
包括退役初赛程序、C++ 实验内核、初赛候选、中间诊断结果和旧目录结构；不含 Git 元数据、忽略的 runs/缓存。
完整原文为 39,642,058 B，ZIP 为 13,209,016 B。
SHA-256：`b14d0bba4b735207c7debcdcd4457c1602403ef1e0f8fd1be6c940c9316f7d6d`。

[路径清单](workspace-manifest.json) 逐文件记录原路径、原始哈希、整理后路径或 archived 状态；ZIP 内保留整理当时的 MANIFEST.json；外部清单的 `current_path` 随后续迁移更新。七份原验证报告现在映射到 `validation_report_legacy_B.json`，原路径、字节数、SHA-256 不变。
原程序的动态路径可能依赖整个旧结构，因此保留完整快照，而不是零散删除实验文件。
当前源码维护于 src；从快照恢复的源码是历史版本，不作为现行修复成果。

在项目根执行，恢复目标须为新的空目录，不能覆盖当前工作区：

```powershell
python -m zipfile -t archives/workspace-before-20260926.zip
python -m zipfile -l archives/workspace-before-20260926.zip
python -m zipfile -e archives/workspace-before-20260926.zip ../jinyinsai1_before_layout_20260926
```

解压后仍需按旧环境准备编译器/可选 runs。Git 历史未改写，远端克隆大小不会因本次压缩而同比减少。
工作区数据和原始比赛样本只按比赛规则使用；该归档仅重打包仓库已有文件。
