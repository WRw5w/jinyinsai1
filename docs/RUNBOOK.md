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

## 在既有工作区上拉取之后：先跑一次全量测试

`.gitattributes` 声明 `* -text`（字节精确，不做换行符转换），但**它只对这次之后的签出生效**。
在一个早于该文件就存在的 clone 上快进到当前版本，工作区会残留 16 个曾被
`core.autocrlf=true` 转成 CRLF 的文件（集中在 `data/` 与 `evidence/`），于是

```
python -X utf8 aic.py test
FAIL: test_relocated_competition_artifacts_keep_original_bytes (path='data/blank_used.csv')
... 共 16 条哈希不符
```

**这不是回归**，是旧工作区没跟着 `.gitattributes` 重新签出。修法：

> ⚠️ 下面的 `git checkout -- .` **会丢弃未提交的改动**：先确认 `git status` 里除了那些
> `data/`、`evidence/` 文件之外没有别的东西，否则先提交或 stash。

```bash
git update-index --refresh   # 旧索引缓存会让 git 误报“干净”，先让它重新核对
git status --short           # 现在应只列出那些 data/、evidence/ 文件
git checkout -- .            # 按 blob 重写；-text 下不做换行转换
python -X utf8 aic.py test   # 应恢复 75 passed, 1 skipped
```

若 `status` 仍为空而测试仍红，直接删掉与库内字节不符的文件再重签出（已实测可行）：

```bash
git ls-files -z | while IFS= read -r -d '' f; do
  cmp -s "$f" <(git show "HEAD:$f") || rm -f "$f"
done
git checkout -- .
python -X utf8 aic.py test
```

注意 `git checkout-index -f` **不管用**（它不按 `-text` 走，仍会写出 CRLF，且返回 0）。
动手前务必确认差异**只有换行符**——逐字节比一次，出现任何其它差异都停下来人工核对：

```python
wt.replace(b"\r\n", b"\n") == blob   # 必须为真
```

## 测试与检查点

- 活跃回归在 `tests/`；初赛反馈样本移入 `tests/fixtures/prelim/`，它们仍验证基础计分逻辑，没有作为无用数据删除。
- `runs/platform_fix/result.json` 是未入库的可选初赛运行产物；没有时该项显式跳过。整理前这一项因缺文件报错。
- 退役初赛内核/实验测试随完整工作区归档；需复现实验时解压快照，不要求复赛工作区安装旧 exe。
- 先恢复实际 runs，再检查岛配置、chunks 和完成状态；不能按旧日志自动重跑。
- 所有测试只约束现有实现，不证明未知官方判据已解决。
