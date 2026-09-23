# 迁移机器说明（2026-09-23）

本文件是 `jinyinsai1_nolimit` 工作区的机器迁移交接说明。迁到新机器后**先读这一份**。

---

## 1. 这是什么

棒材组合订单高效锯切优化（AIC-2026）**复赛**的求解工作区。
- 队名：**鱼不吃猫**　参赛编号：**AIC-2026-93096493**
- 权威远端：`git@github.com:WRw5w/jinyinsai1.git`（本仓把该远端命名为 `upstream`；
  主工作区 `jinyinsai1` 把它命名为 `origin` —— **同一个仓库**）
- 当前分支：`semi-final`
- 赛程：数据集 09-21 11:00 开放 / 提交 09-22 09:00 开始 / **截止 10-05 20:00** /
  代码截止 10-07 23:59；**每天 5 次提交，取复赛阶段最高成绩**

## 2. 新机器上怎么恢复

```bash
git clone git@github.com:WRw5w/jinyinsai1.git jinyinsai1_nolimit
cd jinyinsai1_nolimit
git checkout semi-final
```

然后：

1. **Python**：3.13.x 即可（本机用的是 WorkBuddy 托管 Python，路径 `C:\Users\19811\.workbuddy\binaries\python\versions\3.13.12\python.exe`）。
   调用时一律加 `-X utf8`（Windows 下否则编码会出问题）。
2. **C++ 内核**（可选，只有需要重编搜索内核时）：本机 GCC 13.2.0
   `D:\04_Tools\ACM\gcc-13.2.0\mingw64\bin\g++.exe`，默认 `-std=gnu++20 -O2 -static`。
   `.exe` 不入库，需要时用 `build_kernels.py` 重编。
3. **不需要恢复的东西**：`runs/`（864 MB，全部可复现，已 gitignore）。按下方命令重跑。

## 3. 当前成绩与包的位置

评分口径：`40×min(1, 160000/K) + 30×Y + 20×C + 10`，子分**先四舍五入到两位**再加权。
基准刀数 **B = 160,000**（来源：09-21 15:46 群答疑，见 `evidence/rules/RULES_SOURCES.md`）。

| 包（目录） | 轮数 | 刀数(floor) | 刀数(nearest) | 成材率 | 覆盖率 | **floor 分** | 跨轮接续违规 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `submission_semi_merged_v2/` | 10,742 | **169,785** | 185,495 | 0.900961 | 0.9994 | **94.712** | **0（已修）** |
| `submission_ours_94/` | 11,132 | 173,446 | 186,260 | 0.904656 | 0.9997 | 94.033 | **0（已修）** |
| `submission_semi_safety/` | 12,559 | 175,952 | 190,341 | 0.918081 | 0.9999 | 93.914 | **0（已修）** |
| `submission_ours_seed/` | 11,360 | 175,731 | 186,376 | 0.917885 | 0.9999 | 93.651 | **0（已修）** |
| `submission_semi_nolimit/` | 11,089 | 185,648 | 197,569 | 0.910793 | 1.0000 | 91.798 | **0（已修）** |

**最好成绩 94.712**（`submission_semi_merged_v2/`）。

---

## 3.5 ⛔ 必读：跨轮接续规则（09-23 官方 0 分事故）

### 发生了什么

`submission_semi_merged_v2` 在 2026-09-23 提交后，官方返回：

```
状态 DONE，总分 0，不可行（infeasible）
违规 7030 条，累计扣分 35150 分，类型：跨轮接续不连续
```

从平台"下载"按钮取回的 ZIP 与提交的 SHA-256 完全一致 ⇒ **不是传错包**，
是我们的包**真的违规**。

### 规则的真实含义

`constraints.txt` 的"中间不得插入其他订单"**不是**"相邻两轮包含相同订单"，
而是：**平台把一个方案的各轮读成一条连续的坯料流**，
所以**第 j 轮最后切的那根订单，必须是第 j+1 轮第一根要切的订单**。

我们的求解器让一个方案的**每一轮都带同一批共享冷床订单、且键的顺序完全相同**，
这满足"同订单"但不满足"接续"——因为左轮末尾和右轮开头是同一批订单的**两端**，
不是**首尾相接**。

**7,034 个交界中 7,030 个违规**，只有 4 个碰巧对上。

### 修复（零代价）

对每一轮的键顺序做**循环移位**，让它"闭"到下一轮的第一个订单上：

```bash
python -X utf8 diagnostics/fix_continuity_order.py <包.zip> --out <新包.zip>
```

**已验证**：每轮 `(订单 → 长度)` 多重集**一字未改**，所以
刀数 / 成材率 / 覆盖率 / 得分**逐位相同**（94.71154834617565 前后一致）。

`build_submission.py` 已内建此步骤（`rotate_scheme_rounds`，仅对 `--round semi` 生效），
新打包的包自动为 0 违规。`platform_check.py` 已加 `continuity_seam` 检查，
`test_semi_check.py` 有 3 条回归测试锁住它。

### 3.5.1 判据辨析：`集合相同` 是错的 —— **但 B 也未被唯一确定**

> ⚠️ **2026-09-23 夜更正**：本节标题原文写「判据定案」，那是过强断言。
> 本节证明的只有一点：**`集合相同`（C）是错的**，这一点仍然有效。
> 但「B 就是平台判据」**并未被唯一确定** —— 条款 6 的主语是「同一订单」，
> 一个"每个订单被插入"读法的变体也可能产生 7030。
> **提交风险见 `diagnostics/clause6_OPEN_RISK_20260923.md`；
> 在该风险关闭前不要提交任何包。**

包内**另一条交付线**（`jinyinsai1` 的 `main` 分支）也遇到了这个事故，但他们把
判据校准成了 **`set(左轮) == set(右轮)`**，于是他们的校验器报 0 违规、自认
93.187 分。**这个判据是错的**，两个方向都错：

| 包 | 判据B | 判据C（他们的） | 官方实际 |
|---|---:|---:|---:|
| 我方 `merged_v2`（被拒） | **7030** | 6843 | **7030** ← B 唯一命中 |
| 我方 `FIXED`（已修） | **0** | 6532 | 未提交 ← C 误伤 6532 |
| **对方 `371f209`（自称 0 违规）** | **6230** | **0** | 未提交 ← C 全盲 |

- 在 `merged_v2` 上，B 精确复现官方 7030，C 差 **187** 条 ⇒ **C 被证伪**。
- 在对方现包上，B 报 **6230** 条违规。若官方判据是 B，对方这发会再吃一次 0 分；
  但**我方同样不能确认自己的包安全**（见风险文档）。
- C 之所以在早期的 7559 事故包上"看起来对"，是因为那个包相邻两轮集合
  **完全相同**，B 与 C 恰好重合（7559 == 7559），**样本无区分度**。

**对我方的影响**：`FIXED` / `nolimit` 在 B 下是 0 违规。**但 B 本身待确认。**
不要在迁移后的新环境里把 `continuity_seam` "简化"成集合比较——那会让
`FIXED` 报出 6532 条假违规，并诱使你去修一个不需要修的包。

完整辨析与复算脚本：
- `diagnostics/clause6_predicate_resolved_20260923.md`
- `diagnostics/verify_clause6_readings.py`（三判据 × 五包对照，含 7030 锚点断言）
- `evidence/rival_semi_371f209.json`（对方重交版方案，用于交叉验证）

### 3.5.2 ⚠️ `.zip` 与 `.json` 漂移（第二个 0 分陷阱）

每个交付目录同时放着一份 `.json` 和一份 `.zip`，**真实上传的是 `.zip`**。
修复连续性时只重建了 `.zip`，`.json` 留在旧版：

| 目录 | `.zip`（要交的） | `.json`（曾是旧的） |
|---|---:|---:|
| `submission_semi_merged_v2` | 0 | **7030** |
| `submission_semi_nolimit` | 0 | **7033** |
| `submission_semi_safety` | 0 | **7559** |
| `submission_semi_FIXED` | 0 | 0 |

只要能取到旧 `.json` 的路径（脚本、人工、未来重打包）拿到它，交上去就是
0 分 + 3 万扣分。**已处理**：三份 `.json` 全部重写为对应 `.zip` 内的修复版；
旧版归档到 `diagnostics/pre_fix_backups/`（同时充当判据 B 的 7030 锚点证据）。

**迁移后请只信 `.zip`**，`.json` 仅作人工查看。`verify_clause6_readings.py`
已加自动检查，任何目录出现漂移会立刻 FAIL。

### 教训

1. **本地校验器 ≠ 官方语义**。旧 `platform_check.py` 只查"同订单轮号相邻"，
   对这个真规则**完全瞎**，所以本地报 0 违规、官方报 7030。
2. **反推判定式要交叉验证**。当前判定式是从这一次反馈反推的；
   报告建议确认三类边界（共同订单多于一个 / 两轮无共同订单 / 同订单跳轮）。
3. **提交前必须对"实际 ZIP 字节"跑独立校验**，不能只信求解器的自检。

## 4. ⚠️ 迁移后第一件要事：取整口径未决，值 2.75 分，且会翻转包排序

同一个方案在两种读法下刀数差很大：

| 包 | floor | nearest | 差 |
|---|---:|---:|---:|
| `submission_semi_merged_v2` | 169,785 | 185,495 | **+9.25%** |
| `submission_ours_94` | 173,446 | 186,260 | +7.39% |
| `submission_ours_seed` | 175,731 | 186,376 | +6.06% |
| `submission_semi_nolimit` | 185,648 | 197,569 | +6.42% |

- **floor 口径**（我方采用，`solver.py:194–215`、`platform_score.py:160–177`）：
  依据 2026-09-16 官方 **92.02** 反馈实测复现
  （平台回报 `108671 = Σk(k≥2) + 2·#{k==1}`，35718 段）。
- **nearest 口径**（另一条 AI 线采用，见 `COMPARISON_in_package_two_tracks.md` §4）：
  依据一致性论证。
- **机制**：不是刻意构造，是十进制字面量的双精度除法 ——
  `24.5 // 4.9 → 4`（因 `24.5/4.9 = 4.999999999999999`）。
- **后果**：floor 下 `merged_v2` 第一；nearest 下 **`ours_seed` 反超 0.357**。
  **minimax 最优是 `ours_seed`（91.874），不是 `merged_v2`（91.517）。**
- **判定实验建议**：利用「每天 5 次、取阶段最高」，**同一天把 `merged_v2` 与 `ours_seed` 各提一发**，
  看榜面回分即可判出口径。详细论证见 `COMPARISON_in_package_two_tracks.md`。

## 5. 怎么重跑（不带 runs/ 的前提下）

```bash
# 复赛数据准备（隔离异常订单 B20270281，非正重量 -25.418 t）
python -X utf8 prepare_semi.py

# 单个岛（示例：锚岛，36 组 × 183 块，块大小 60）
python -X utf8 -m solve_semi --help        # 先看参数
python -X utf8 supervise_island.py --help  # 带守护器

# 跨岛逐块择优合并（岛跑完后）
python -X utf8 diagnostics/merge_chunks.py \
    --runs runs/semi_anchor_v1 runs/semi_anchor_s2 runs/semi_anchor_s3 \
           runs/semi_anchor_s4 runs/semi_anchor_60 runs/semi_refine_s1 runs/semi_refine_s2 \
    --output runs/semi_merged

# 打包交付
python -X utf8 build_submission.py --help

# 测试
python -X utf8 -m unittest test_semi_solver test_solver   # 23 tests
```

**已跑完的岛**（迁移前状态，09-23 10:44）：
`semi_anchor_v1` 183/183、`semi_anchor_s2/s3/s4` 各 184/184、`semi_nolimit_v1` 183/183（已完成）；
`semi_anchor_60` 124/184、`semi_refine_s1` 118/183、`semi_refine_s2` 118/183（**未跑完，需重跑**）。

## 6. 已知的坑（迁移后不要重踩）

1. **`-X utf8` 必须加**：Windows 下否则 stdio 走 cp936，中文路径会 mojibake。
2. **一轮容量上限**：床长 50–150 m、宽 2 m、承重 60 t、同钢种同规格；**每方案 ≤6 轮**且**相邻轮连续**。
3. **计刀口径**：复赛是「每轮一组头尾」，即 `段数 + 1`，**不乘棒材根数**；
   但 `platform_check` 判交付时用 `nearest`。两处口径不同，别混。
4. **成材率分子用截断到整数毫米的直径**；物理承重校验用**原始小数直径**（保守）。
5. **异常订单 `B20270281`**：原始重量 −25.418 t，必须隔离，不能补值。
6. **`supervise_island.py` 防不住「守护器自己被一起杀」**：09-23 09:21 三个岛被整批杀死过
   （`rc=1073807364` / `rc=3221226091`）。重启时会从 chunks 缓存秒回。
7. **打榜链路**：本机在 `auto_review/`（另一个工作区），见该仓库 `README` 与
   `~/.workbuddy/skills/bar-cutting-submission-acceptance/SKILL.md`。
   浏览器段（CDP）**只能在用户自己的终端跑**，WorkBuddy 沙箱拦 loopback HTTP。

## 7. 迁移包里应该有什么

- **本仓库源码与 git 历史**（`git clone` 或本目录压缩包）
- **对话记录**：`conversation/` 下的 `.jsonl`（可被 WorkBuddy 恢复）+ `.md`（可读版）
- **记忆**：`.workbuddy/memory/`（每日工作日志，git 已跟踪）
- **不含**：`runs/`（864 MB，重跑即可）、`*.exe`（编译器相关，重编即可）
