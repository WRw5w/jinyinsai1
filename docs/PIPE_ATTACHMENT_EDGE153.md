# result-records 附件下载失效：根因与修复（Edge 153）

2026-09-26 深夜定位。影响面：`leaderboard_pipe.mjs result-records` 的附件 SHA-256 核验
（回分主体——分数与刀/材/覆明细——不受影响）。**已在工作区修复并端到端验证。**

## 症状

四次回分全部报 `download.saveAs: Target page, context or browser has been closed`
（`auto_submit/capture_20260926-2*.json` 的 `attachmentError`）。

## 根因：Edge 153 在下载开始约 1 秒后崩溃整个浏览器进程

不是管道的问题，也不是 AIC 平台的问题。证据链（均为只读实验）：

1. **最小复现**：在空白页（`page.setContent`，无任何平台 JS）点一个指向同一 OSS 链接的 `<a>`，
   1.2 秒后页面关闭 → context 关闭 → 全部 API 报"browser has been closed"；
   外部进程计数显示该 profile 的 msedge 进程树 11 → 0。
2. **换宿主同样复现**：下载 npmmirror 的 `playwright-1.63.0.tgz`（与 AIC 无关），同样 ~1.6 秒崩。
3. **headless 同样复现**。
4. **Crashpad 转储 15 份**（`%TEMP%\aic_leaderboard_pipe_profile\Crashpad\reports\*.dmp`），
   指纹完全一致：`EXCEPTION_ACCESS_VIOLATION (0xC0000005)`，故障地址恒为
   `msedge.dll+0x9a4cd1b`（读 `0x0+0x18`，空指针），Edge 版本 `153.0.4234.48`。
   → 确定性缺陷，不是随机 flake。
5. 旁证：微软已公开承认 Edge 153 存在已知故障，建议临时降级 152。

Crashpad 目录创建于 20:23（今晚第一次真正触发浏览器下载），此前从未崩溃过——下载动作今晚才第一次走到。

## 修复（`D:\new_mcp\tools\leaderboard_pipe.mjs`，工作区，未提交）

不改浏览器，让**浏览器不再负责保存文件**：

1. 点击"下载"后照常等 `download` 事件（事件在崩溃前 ~1 秒触发，可靠）；
2. 取 `download.url()`——附件是**公开 OSS 对象**，与登录态无关；
3. node 自带 `fetch`（v24）直接抓取同一 URL 的字节，写临时文件算 SHA-256、比对 `expected`；
4. 记录新增 `attachmentSource: "url-fetch"` 与 `attachmentUrl`；临时文件照旧删除。
5. 附带加固：`finally` 里的 `context.close()` 改为 `close().catch(() => {})`，崩溃后的上下文关闭不再抛错。

字节等价性：curl/ fetch 取回的 OSS 内容与账本 `sha256`、本地候选包**逐位一致**
（`06b7db30…` = cand_covprobe25 本地包 = 20:37 记录附件）。

## 验证（2026-09-26 20:5x）

```
AIC_LEADERBOARD_EXPECTED_SHA256=06b7db30a74321ccd021a685cc964ffba5a17ffb4c48a8a41358adf7eb726937 \
  ./aic-pipe.sh result-records
```

结果：`attachmentSource: url-fetch`、`attachmentMatches: true`、`attachmentBytes: 116468`、退出码 0。
修复后不再产生新崩溃转储（我们在崩溃计时器前正常关闭浏览器）。

## 遗留与备选

- **转储堆积**：修复前每下载一次留 ~10 MB 转储（现有 15 份 ≈ 160 MB，在 `%TEMP%` 下）。
  确认无误后可整体删除该目录。
- **备选路径**：Playwright 自带 Chromium 已装好（`%LOCALAPPDATA%\ms-playwright\chromium-*\chrome-win\chrome.exe`），
  Edge 若持续不稳可把 `AIC_LEADERBOARD_CHROME_PATH` 整体切过去（登录态在 profile 里，切换需重新登录）。
- **降级 Edge 152** 是微软给用户侧的解法，需要管理员权限，未动。
- `D:\new_mcp\tools\aic_attribute_score.py` 仍保留为哈希缺失时的补录旁路
  （默认拒绝无哈希行，旁路需 `--accept-unverified-hash --reason`）。
