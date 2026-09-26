# 复赛证据与判据边界

更新 2026-09-26。以下区分平台反馈记录、本次独立复算、尚待验证的解释。

## 原件位置

| 证据 | 路径 / 说明 |
|---|---|
| 12 条原始约束 | [constraints.txt](../data/semi/constraints.txt) |
| 原始 v2=7030 | [official_feedback.json](../submission_semi_merged_v2/official_feedback.json)；对应原上传 ZIP SHA-256 `852c9b8498d4205e5e69ce656b22c2389e927490132f5ce5fa367842cf1cb165` |
| 原始 v2 JSON | [pre_fix_backups](../diagnostics/pre_fix_backups/merged_v2__复赛结果_鱼不吃猫.json)；不要拿已旋转版冒充原上传版本 |
| v4=7342 的记录 | [历史报告入口](../diagnostics/clause6_rotation_falsified_20260926.md)；完整原文在档案同路径。记载 2026-09-26 15:45:18 评分且 attachmentMatches=true；缺原始回读 JSON，需补归档 |
| v4 实际 ZIP | `submission_semi_merged_v4/复赛结果_鱼不吃猫.zip`，SHA-256 `97955f360f3ea26c8f501e0d736676aa966b2817943beec8632861eba173bb12` |
| 官方可行示例 | [PDF](../evidence/rules/AIC竞赛规则.pdf) 第 18–19 页：A20260105→A20260104，下一轮 A20260104 |
| 初赛校准 | `evidence/20260916/` 的 JSON/ZIP 保留原路径；仅用于历史回归 |

## 能同时解释两个反馈的候选判据

对每对相邻轮的每个共享订单，要求它同时处于左轮末尾和右轮开头；只要一个共享订单不满足，该交界记一次违规。另行检查同一订单跳轮。

```python
shared = set(left) & set(right)
bad_boundary = any(
    order != next(reversed(left)) or order != next(iter(right))
    for order in shared
)
```

| 样本 | 旧首尾 B | sorted / orders 顺序 | 逐订单、按交界计数 | 平台反馈记录 |
|---|---:|---:|---:|---:|
| 原始 v2 | 7030 | 7030 | 7030 | 7030 |
| 已旋转 v4 | 0 | 7344 | 7342 | 7342 |
| 已旋转 v2 | 0 | 7030 | 7028 | 无该版本对应实测 |
| 官方示例 | 0 | 1 | 0 | PDF 称可行 |

计数于 2026-09-26 从远端固定版本的 JSON/ZIP 独立复算；候选式未并入生产校验器。
吻合两个反馈不等于取得官方算法，也没有证明平台完全忽略或必然依赖 JSON 键顺序。

反例：`A,B → B,A` 的 B=0，但 A 被 B 隔开；候选式记该交界 1 条。
`A,B → B → B,C` 可满足连续区段，因此“同一订单不能跨三轮”也不是正确约束。
v4 排序判据多算的两处为 0-based `(方案721,批4)` 与 `(方案1698,批4)`：
二者仅共享一个订单，原 JSON 中已经首尾接续，排序后反而破坏它。

## 未实现的工程修正

- 校验器仍使用 B；`validation_report.json` 的 passed/0 违规仅描述旧本地模型，不能认证可提交。
- `verify_clause6_readings.py` 的副本检查只比较 B 计数；它不能验证完整 JSON 内容一致，读取错误也可能被跳过。
- v4 的同轮组合订单数为 9996，多订单方案内订单数为 9999；覆盖率两定义有差异。
- 新规则报告关于“重新满足 10 秒”“初赛证明复赛权重”“预测95分所以基线无风险”的结论已撤回；见 [RULES](../RULES.md)。
- 官方拒绝记录与候选 SHA 必须绑定；仅修改 Markdown 不会让 MCP 的机器闸门自动阻止旧包。

以上是文档审查结果。修复顺序见 [PLAN](PLAN.md)，不能因本文存在就声称相关代码已修好。
