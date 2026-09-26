# 复赛产物

- `rejected/`：七个既有复赛候选，仅作反馈锚点/回归样本，不可按目录名 FIXED/safety 直接提交。
- `candidates/`：未来打包输出默认目录，生成后须做实际 ZIP 校验和官方结果归因。

现行报告明确返回 `status=unverified`、`passed=false`、`platform_check_passed=false`、`submission_allowed=false`；`violation_count=null` 表示未完成有效认证。旧 B 结果位于 `legacy_model_result`，原报告字节保存在 `validation_report_legacy_B.json`。`resync` 保持相同的禁止放行状态。见 [当前状态](../docs/CURRENT.md)。
目录名 rejected 表示禁止直接放行，不表示每个版本都已取得官方拒绝回执；具体反馈按 SHA 区分。
整理只移动既有候选，所有原方案 JSON/ZIP 哈希保持不变；原验证报告以 legacy 文件名保留；原路径映射见 [清单](../archives/workspace-manifest.json)。
