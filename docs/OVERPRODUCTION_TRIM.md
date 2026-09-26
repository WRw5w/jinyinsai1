# 复赛超产转换（2026-09-26 夜 → 09-27 凌晨）

**一句话**：计划里"多切出来的"片数与申报质量按 1:1 兑换。把 220,141 片超产花掉一大半，
本地预测分 **91.20 → 91.80**（同一结构，未动批次/订单集/刀数上界）；再叠三个新遍历到不动点，
**92.11**（`runs/_rf3.json`，刀 184,521、材 93.65、覆 99.83，零违规）；再叠跨批重打包、共享遍历与
轨迹/循环，本地最高 **92.1572**（`runs/_cy1.json`，刀 184,504、材 93.68、覆 99.98，零违规）。

## 为什么这是纯收益

- 成材率分母是申报质量、无欠交时分子是 Σ 需求 —— **多切的每一片只抬高分母，不抬高分子**。
- 刀数 = Σ 片段 + 轮数；多余的片还挂在片段上（砍一个片段 = −1 刀）。
- 单片兑换率 ≈ **61 kg**（平均 定尺长 × 线密度）；对 184,922 刀的盘面 ≈ 7.4e-6 分/片。
- 起点 `cand_rounded` 的交付超产 = **220,141 片 ≈ 13.5M kg**。

## 三类花费工具（`tools/analysis/shift_cuts.py`）

| 工具 | 动作 | 材料 | 刀数 | 门槛 |
|---|---|---:|---:|---|
| 片段删除（`drop_pieces`） | 某订单在本轮 k→k−1 | −size×count×lin | **−1** | 该订单 slack ≥ count |
| 降 count（`repack_counts` drop 相） | 本轮 count−1 | −(net+2)·lin | 0 | 轮内**每个**订单 slack ≥ 自己的 k |
| 搬 count（`repack_counts` xfer 相） | 同批次内把 1 个 count 从长轮次搬到短轮次 | −(net长−net短)·lin | 0 | 接收轮不跨坯料边界 |

兑换率：片段删除 ≈ **61 kg/片 + 1/count 刀**；count 类 ≈ **38 kg/片、无刀**。
→ **先删片段（顺手买刀），再用 count 花剩下的余量。**

## 流水线与守门

```
coarsen → coarsen_mixed → reshape → drop_pieces → reshape2 → repack_counts → reshape3 → retype_and_requant
        → requantize_cuts → recount_rounds → split_schemes      （后三个 = 09-27 凌晨新增）
```

- 每一相后跑 `reshape_plan`（切分点平移），把新腾出的交付余量再对齐一次。
- 守门：严格校验器必须通过；net ∈ [48,148]；**总代价**（`2,830 × 刀数增量 + 申报增量`）不得上升；
  覆盖率不得下降；included 订单数不变；每方案轮数 ≤ 6；
  count 的每次变化先用精确账单表 `bill(net,c)=⌈(net+2)·c·lin/w − 1e-7⌉·w`（Decimal ROUND_CEILING，
  最终由 `requantize_cuts` 精确写入）预结算，**接收轮跨界整跳 +w ≈ 8,000 kg 的转移一律拒绝**。

## 三个新遍历（09-27 凌晨，`tools/analysis/shift_cuts.py`）

超产转换吃完之后，剩下的余量碎得动不了；能再赚的都是**换轴**的遍历 —— 每一个都不动前一个动过的东西：

| 遍历 | 动作 | 用一次搜索换 | 实测 |
|---|---|---|---:|
| `requantize_cuts` | 每批**统一 count** 重扫 `count ∈ [max(1,min−3), ⌊2000/dia⌋]`，片段大小由交付下限导出（`lay_pieces` 最短路 DP） | 刀数 ↑/↓ 与申报的联立最优 | 91.80 → 91.99（刀 −242，申报 +0.4M） |
| `recount_rounds` | 每轮 count 降到**该轮下限** `c*ₙ = max_o ⌈pieces_o/segments_o⌉`（RMQ 表取区间最大） | 刀数（轮 +35）换材料（申报 −943k kg） | 91.99 → 92.08 |
| `split_schemes` | 在**无订单横跨**的轮边界把方案切成续段，每段自己选坯料重量（区间 DP，`best(i,j) = min_w Σ bill`） | 申报（取整浪费） | 92.08 → 92.11（申报 −441k kg，刀数不变） |

- 三者都只重写 `length_scheme`/`counts`/`blank_counts`/`blank_type`，**订单集与轮数上界留给前一个遍历**；
  `requantize` 与 `recount` 各自要求严格改进（1.0 kg 余量），`split` 要求严格小于单段成本。
- 换算汇率见 [SCORE_MODEL](SCORE_MODEL.md)：**1 刀 ≈ 2,830 kg 等效申报**，所以
  "省 35 轮 + 943k kg" 与 "省 242 刀" 是同一把尺子，`main()` 的总门控按它判。
- 坑：接受条件写"刀数或材料任一下降"会引入**零增量抖动**（每轮重写 1,300+ 批次、净变化 ≈ 0，永不收敛）；
  加 1.0 kg 余量后两轮内停在不动点。

## 混合 count 批次的重切（`coarsen_mixed`，2026-09-26 夜半）

`coarsen` 只吃 count 均匀的方案，trim 之后的盘面里 **1,880 个批次有 1,818 个是混合 count**，
10,349 轮里 10,137 轮在这些批次里 —— 轮数余量大头恰好被这道门挡住。放开它的代价必须自己付清：
重切会移动切分点，混合 count 下每个订单的交付 `Σ_r k_r·c_r` 会跟着变。

**解法：每轮取该轮订单的交付下限。** 记 `c*_o = ⌈pieces_o / segments_o⌉`（订单自己的下限），
重切时令每轮 count = 该轮所有订单的 `c*_o` 的最大值。订单的轮次连续，于是
`Σ_r k_r·c_r ≥ Σ_r k_r·c*_o = segments_o·c*_o ≥ pieces_o` —— **交付下限由构造保证，不靠事后检查**。
又因账单对 count 单调，每轮的最省 count 就是这个下限本身（低 count 还换来更高的 net 上限）。

- 目标函数用分数自己的汇率：省 1 轮 ≈ 2,830 kg 等效申报；`2830×省轮 > 账单增量` 才改。
  （等价物换算见 [SCORE_MODEL](SCORE_MODEL.md)；只用"省轮数>0"当判据会放进 187 个"省轮但材料反涨"的净亏批次。）
- 额外守门：**覆盖率不得下降**（重切可能把共享轮拆开）。实测一条也未被挡。
- 实测：`_iter1`（91.71）单跑此相 → **91.78**；接全管线再收敛 → **91.80**
  （刀 184,922→184,730，申报 544.87M→544.17M kg，覆盖 99.71→99.76）。1 秒跑完（窗口内 DP，早停）。

## 实测（2026-09-26 夜）

| 阶段 | 刀数 | 成材率 | 覆盖率 | 本地分 |
|---|---:|---:|---:|---:|
| `cand_rounded`（官方实发 90.59） | 185,229 | 91.39 | 97.43 | 90.5938 |
| +coarsen 128 轮 +448 次切分点平移（`_cand_shift2`） | 185,101 | 91.75 | 99.64 | 91.20 |
| +超产转换（`_cand_full7`） | 184,922 | 92.80 | 99.70 | 91.67 |
| +固定点收敛（`_iter1`） | 184,922 | 92.90 | 99.71 | 91.71 |
| **+混合批次重切（`_iter11` = `cand_iter11`）** | **184,730** | **93.01** | **99.76** | **91.80** |
| +`requantize_cuts`（`_rq3` = `cand_rq3`） | 184,488 | 93.34 | 99.82 | 91.99 |
| +`recount_rounds`（`_rc3`） | 184,515 | 93.56 | 99.83 | 92.08 |
| **+`split_schemes`（`_rf3` = `cand_rf3`）** | **184,521** | **93.65** | **99.83** | **92.11** |
| +跨批重打包（`_rp3`，组成重排、每批从零切） | 184,510 | 93.65 | 99.85 | 92.1147 |
| **+级联到不动点（`_rp4` = `cand_rp4`；与 `_rp2` 同字节）** | **184,510** | **93.65** | **99.85** | **92.1155** |
| +共享遍历后级联到不动点（`_rp5` = `cand_rp5`） | 184,515 | 93.65 | **99.98** | 92.1406 |
| +换轨迹：`--split-schemes` 早开（`_sp1` = `cand_sp1`） | 184,515 | 93.67 | 99.98 | 92.1508 |
| **+换轨迹：`--recount-rounds` 放最后（`_qs1` = `cand_qs1`）** | **184,505** | **93.68** | **99.98** | **92.1554** |
| **+同一轨迹再跑一圈（`_cy1` = `cand_cy1`）** | **184,504** | **93.68** | **99.98** | **92.1572** |
| +第三圈（`_tcyc27`） | 184,504 | 93.68 | 99.98 | 92.1572（净变化 0，整机不动点） |

`_cand_full7` 细节：申报质量 551.68M → **545.43M kg**（−6.25M）；count 移动 7,142 次
（6,967 降低 + 175 转移，账单增益 2.29M kg）；片段删除 179 + 切分点平移 418；剩余交付余量 **95,084 片**。

**质量分解（`runs/_mass_split.py _rf3.json`）**：申报 540.50M kg 的去向 ——

| 项 | kg | 占申报 | 折分 | 性质 |
|---|---:|---:|---:|---|
| 需求（分子，被 cap 到订单重量） | 506,157,994 | 93.65% | — | 分子 |
| 修整（每轮 2 m × count × lin，10,218 轮） | 11,306,992 | 2.09% | 0.84 | 规则强制 |
| 坯料取整（⌈⌉ 到整根坯料，每轮均 1,632 kg） | 16,680,386 | 3.09% | 1.23 | 几何 |
| 超产（交付片 × 单片质量 − 需求） | 6,357,242 | 1.18% | 0.47 | 可花（余量 95,514 片） |

每轮均价 **2,739 kg** 的取整+修整浪费：省 1,000 轮 ≈ 2.7M kg + 1,000 刀 ≈ 合计 0.4 分。

## 三个坑（全部实测踩过）

1. **用即时锯齿增益选招 → 91.34**：Δ=1 的 count 步大多当步不跨锯齿边界（即时增益 0），但期望为正。
   排序必须用材料节省率，不能用即时增益。
2. **无跨界守门 → 91.47**：接收轮 count 上升会跨坯料边界整跳 +w，账面"赚"了、申报反而涨 2.75M。
3. **精确账单符号写反 → 放行全部不安全转移**：接收轮成本必须是 `tab_s[c+1] − tab_s[c]`（减号）；
   写成加号时账单增益虚高到 12.9M，修复后 2.29M 才合理。

## 跨批重打包：重排组成只买到 8 轮

规则 10 只禁止一个订单出现在两个方案里，**不规定谁和谁同批** —— 批次组成是一个此前没动过的自由度。
`tools/analysis/repack_batches.py` 把每个 (钢种, 直径) 组内的订单重新装箱、每批从零重切：

- **装箱**：订单按"自身最高 count 下的链长"降序首次适应，每批 ≤ 6×148 m；封口前必须能被切出来，
  切不出来的批把最轻订单退回下一批；链长不足一轮的订单必须与伙伴同批，否则整组回退。
- **重切**：每批重建 count（1..辊道上限全扫，取 5 个短名单真跑 `lay_pieces`），估价用分数自己的汇率
  （1 刀 ≈ 2,830 kg 申报质量）。**拒收链长填不满一轮的 count** —— 这是高 count 端的陷阱：
  片数最少、却切不出任何一轮，早期估价把它排在最前，会让整组"优化"成更贵的方案。
- **门控**：整组重切后按同一汇率比价，更贵就保留原批（36 组里 33 组被保留）。

实测 **92.1087 → 92.1147**（轮 10,218→10,210、刀 184,521→184,510），级联到不动点 **92.1155**。
两条独立谱系（先修 count 窗口、后修估价排序）产出**同字节**的计划——这个不动点是稳的。

### 伪靶：`_repack_sim.py` 的 9,040 轮

早期脚本 `runs/_repack_sim.py` 按"每单最高 count + 长度/床重装箱"算出 10,218 → 9,040 轮（差 1,178），
`runs/_group_floor.py` 给的同组地板 8,658 是同一类下界。两者**都不可及**，因为它们忽略了：

1. **`cap(c)` 与塔高 count 反向走**：一轮最多载 `cap(c) = min(148, 60000/(c·lin) − 2)` 米净长
   （床重 60 t，外加每轮 2 m 修整）。count 越高 `cap` 越低 —— 高 count 省片段（`Σ⌈P/c⌉`），
   却把轮数推上去，而每多一轮还多一份 `2 × count × lin` 的账单。
2. **每交付片的账单与 count 无关**：一轮的账单 `⌈(net+2)·c·lin/w⌉·w` 除以该轮交付片数 `net·c`，
   得 `lin·size·(1 + 2/net)` —— **只由轮净长决定**，count 整个约掉了。

实测（`tools/analysis/round_fill.py runs/_rp4.json`，逐轮统计）：

| 事实 | 数 |
|---|---:|
| 轮数 / 平均净长 / 平均 `net/cap(c)` | 10,210 / **96.1 m** / **85.8%** |
| 受床重挡（`cap(c) < 148`）的轮 / 受长度挡 | **9,037** / 1,173 |
| 正好顶到 `cap` 的轮 | **1** |
| 轮距 `cap` 的平均差（中位） | 15.8 m（11.3） |
| **轮距"账单多付一整根坯料"的平均余量（中位）** | **3.1 m（2.5）** |
| 轮距 `cap` 的差 ≥ 距账单整根的余量 | **8,445 / 10,210** |

两行相比就是结论：要填满到 `cap` 平均还差 15.8 m，但**平均只剩 3.1 m 就会跳到下一个整根台阶**，
82.7% 的轮都是台阶先到 —— 净长再加几米就会整跳一根坯料（数千至上万 kg），比多切的那点长度贵得多。
所以这些轮不是"没装满"等在那里：它已经停在账单 `⌈·⌉` 的台阶下沿，
"把每轮铺满到 148 m"既不可达、也不是目标。真正的杠杆是**降低 `(net+2)·c·lin` 让账单掉台阶**
（`requantize_cuts` / `recount_rounds` 做的正是这件事），至于组成重排，整套只值 8 轮。

## 从不共享一轮的订单：15 单买到 13 单（09-27 凌晨）

**机制**：覆盖率分子只数**与别人同轮**的订单。`platform_score` 读 `len(set(scheme)) > 1` 才算覆盖，
所以一个独占一轮的订单，不管交付多少钢，都白丢一份 `COVER_BONUS_KG`。"未覆盖"不是"没在计划里"，
而是"从不与别人同轮"——`_rp4` 里这样的订单有 15 个（覆盖率 99.85%）。

前三条遍历都够不着它：`reshape_plan` **按构造跳过单订单批**（它只在两个订单的切点之间挪），
`repack_batches` 的**组级**门控赌的是"一组整体更便宜"，一个批单独能省下的钱会被同组其他批的波动淹掉。
`tools/analysis/share_orders.py` 改成**逐批**动手：

- 从不共享的订单按片数升序排；对每一单，先把**它自己那个批**重切一遍（`cut_batch` 同一订单表），
  再把它的批与同 (钢种, 直径) 组内**链长最接近的 40 个批**逐个求并、用 `cut_batch` 从零切（合并）。
- 门控就是 `cost_of` 自己的汇率：一把刀 `KNIFE_KG = 2,830 kg` 等效申报质量、一份共享 `COVER_BONUS_KG`。
  **只有严格更便宜才动手** —— 丢掉的共享也按同样的价扣，所以这一遍历**不会拿我们已有的共享去换另一份**。
- 轮、count、片数与坯料类型全部由 `repack_batches` 同一套排布代码重新导出，不新造任何一刀；
  `main()` 写盘后仍要过严格校验 + `evaluate`，不通过就 `SystemExit('… keep the input')`。

**实测**（`runs/_rp4.json` → `runs/_sh1.json` → 级联 `runs/_rp5.json`）：

| 步 | 动作 | 结果 |
|---|---|---|
| `_rp4` → `_sh1` | 4 次重切 + 6 次合并，**13 单转为共享** | 92.1155 → **92.1392**（申报 −355,620 kg，覆 99.85 → 99.98，刀 184,510 → 184,515，2 趟扫描，1.0 秒） |
| `_sh1` → `_rp5` | 同一三级联（`--requant-cuts --recount-rounds --split-schemes`）再跑一遍 | 92.1392 → **92.1406**（净 −20,631 kg 等效；轮到 10,211） |
| `_rp5` → `_rp6` | 再跑一遍 | 净变化 **0.0** —— 分数不动点 |

`_rp5` 的盘面：**184,515 刀 / 10,211 轮 / 成材率 93.65% / 覆盖率 99.98% / 本地 92.1406**，
严格校验零违规。它进队列为 `cand_rp5`（`a1753376`），比 `cand_rp4` 高 **+0.0251**。
**不动点对「批次顺序」不敏感**（对「flag 顺序」敏感，见下面「轨迹」一节）：把 `_rp5` 的**批次顺序整体打乱**
（两个随机种子）再跑完整条级联（shift → repack → shift → share → shift → shift），六步之后仍落在同一分数
（两个种子都是 184,515 刀 / 99.98% / 92.1406）；同一顺序再跑一遍（`_rp6`）净变化 0.0。
`share_orders` 的伙伴选择按"链长差 + 下标"定序，本来是唯一对输入顺序敏感的一步，实测也没有借顺序多买到一分。

剩下 2 单买不到邻居，原因是**轮数预算**而不是价格：

| 残余未共享 | 所在批 | 链长 / 六轮上限 | 为什么 |
|---|---|---|---|
| B20271919（1,626 片 NP01⌀43×4.9） | 6 单、已用满 6 轮 | 516 m / 888 m | 任何伙伴加进来都溢出 6 轮；单独重切该批排布不变（Δ = 0 kg），该单仍独占轮 |
| B20275372（2,171 片 C60⌀26.5×4.9） | 5 单、已用满 6 轮 | 847 m / 888 m | 同上，且单独重切更贵（+8,490 kg） |

覆盖率这条靶就此基本打光：2 单 × 0.002 分 ≈ **0.004 分**的余量，且被"每方案 ≤6 轮"挡住，
不是排序或价格问题。

### 两条顺带被否证的路线（负结果，别再追）

1. **换坯料类型**：每批的 `blank_type` 是五选一、与轮/切点无关，看起来是免费的午餐。逐批实测
   （`runs/_blank_check.py`）**now == best == 540,492,311 kg，可省 0 kg** —— 现在的计划每批都已经
   落在最省的那一种坯料上。
2. **"任意切点分割"的申报上界 3,159,724 kg 不可达**：把每个批在任意切点处劈成两个方案会省下
   3.16M kg（1,430 / 2,107 批受益），但它**违反规则 10**（同一订单不得跨方案重复出现）——
   劈开意味着同一个批的订单出现在两个方案里。合法版本（只在批内**连通分量**处切）只值
   **39,128 kg ≈ 0.003 分**，还要为 22 个批重排轮序，性价比为零。

## 轨迹：flag 的开启顺序决定落在哪个不动点（09-27 凌晨）

上面那些分数都出自**一条**轨迹：先 `--min-rounds/--mixed-rounds`，再 `--requant-cuts`，再 `--recount-rounds`，
最后 `--split-schemes`（每个 flag 各自迭代到不动点），然后才是跨批重打包与共享遍历。
92.14 是这条路选出来的，还是这组遍历的地板？`runs/_traj.py <name>` 从同一个起点 `_iter11` 出发
（`fromseed` 除外，它从 `_p_full4` 出发），把同一组遍历按不同顺序各跑到不动点
（`runs/_traj_<name>.log`），落点如下（按分数降序，M/X 之后的顺序）：

| 轨迹 | flag 顺序 | 最终本地分 | 刀 | 覆 |
|---|---|---:|---:|---:|
| **`cyc2`**（`cyc` 的落点再跑第三圈） | **Q → S → R（净变化 0）** | **92.1572** | 184,504 | 99.98 |
| **`cyc`**（`qsplit` 的落点再跑一圈） | **Q → S → R（R 最后）** | **92.1572** | 184,504 | 99.98 |
| `qsplit` | Q → S → R（R 最后） | 92.1554 | 184,505 | 99.98 |
| `rsplit` | 重打包最先，再 S → R → Q | 92.1516 | 184,505 | 99.98 |
| `splitfirst` | S → R → Q | 92.1508 | 184,515 | 99.98 |
| `sfonly`（≡ `splitfirst`） | 先单开 S，再 S → R → Q | 92.1508 | 184,515 | 99.98 |
| `splitq` | S → Q → R | 92.1477 | 184,517 | 99.98 |
| `recountfirst` | R → Q → S | 92.1429 | 184,511 | 99.98 |
| `sharefirst` | 一次全开、共享优先 | 92.1415 | 184,515 | 99.98 |
| `staged`（对照 = stored） | Q → R → S 分阶段 | 92.1406 | 184,515 | 99.98 |
| `onebyone` | 逐面开（Q→R→S） | 92.1406 | 184,515 | 99.98 |
| `rsq` | R → S → Q | 92.1402 | 184,515 | 99.98 |
| `fromseed`（从 `_p_full4` 出发） | 只跑重打包+共享级联 | 92.1344 | 184,547 | 99.98 |

四条结论：

1. **对照轨迹逐位复现 stored 谱系**（`_rq*`/`_rc*`/`_rf*`/`_rp*` 每步的 `cost_delta` 与分数都对上），
   所以表里的差异是搜索的差异，不是 harness 的。`sfonly` 与 `splitfirst` 同分同刀同覆（`_iter11`
   已是 M/X 的不动点，单开 S 与 [M,X,S] 等价），这条冗余轨迹证实了 M/X 是空操作。
2. **读法：S 放最后最差，R 放最后最好。** `--split-schemes` 放最后的四条（`staged`/`onebyone`/
   `recountfirst`/`rsq`）都停在 92.1402–92.1429；把 S 挪到 Q/R 之前，落点整档跳到 92.15；三个 flag 里
   **R 放最后、S 在 Q 之后**（`qsplit`）最好。机制上讲得通：S 改的是**方案窗口的边界**，Q 与 R
   都是在既定窗口里重解 —— 窗口一固定，`⌈·⌉` 的台阶就把它们锁住了，先分段 = 先给它们更好的窗口；
   而 R（把每轮 count 压到下限）最后跑，等于把前两步腾出的余量再收一遍。
3. **循环还能再赚 0.0018，然后停。** 把 Q→S→R 整条链以它自己的落点 `_qs1` 为输入再跑一遍（`cyc`），
   分数从 92.1554 涨到 **92.1572** —— 增益全部来自第二阶段的 `[M,X,Q,S]`（两轮 −21,520 / −4,628 kg，
   其余阶段 `cost_delta` 全是 0）；第四圈不存在：`cyc` 的落点再跑一遍（`cyc2`）每一阶段都是 0。
   读法：**ALL 的"不动点"只是它自己内部顺序的不动点**（组合不可交换），换一序再走仍能找到活；
   但两级之后整台机器（含重打包与共享）就锁死了，92.1572 是这条轨迹的天花板。
4. **盆地宽 0.0170**（92.1402–92.1572）：92.14 不是这组遍历的地板，而是**一条轨迹的落点**。
   在这台机器上，选对轨迹比再迭代一轮更值钱；下次搜索应从多条轨迹的不动点**集合**里挑最高，
   而不是把单条轨迹迭代到死。`fromseed` 是同一条级联从**另一条谱系的种子**（`_p_full4`，92.0798）出发：
   它能被抬升 0.055 到 92.1344，但抬不进 `_iter11` 盆地 —— 轨迹差在**级联之前**就定下了。

### 第二遍与闭合：92.1572 是这台机器的顶（09-27 凌晨）

把赢得比赛的顺序（Q→S→R）当作**第二遍**，接在每条轨迹自己的落点之后（四个入口实测）：

| 入口（第一遍落点） | 第二遍落点 | 增益 |
|---|---:|---:|
| `_qs1` 92.1554（`cyc`） | **92.1572** | +0.0018 |
| `_trsplit6` 92.1516（`cycrs`） | 92.1535 | +0.0019 |
| `_sp1` 92.1508（`cycsp1`） | 92.1527 | +0.0019 |
| `_tsplitq7` 92.1477（`cycsq`） | 92.1494 | +0.0017 |

- **第二遍普遍有效**：每个入口都能再赚 0.0017–0.0019，且四个入口的增益里都含**同一笔
  −4,628.464 kg**（另一笔按入口在 −20.8k ~ −22.7k kg 之间）——这些是各自第一遍的迭代顺序
  漏掉的"必得项"，机制与 `cyc` 相同：ALL 的不动点只是它内部顺序的不动点。
- **但台阶由入口决定**：入口排序与第二遍落点排序完全一致（92.1554 > 92.1516 > 92.1508 > 92.1477
  → 92.1572 > 92.1535 > 92.1527 > 92.1494）。第二遍是整体抬升，抹不平入口差 ——
  **选入口（第一遍轨迹）仍然比多跑一遍重要**。

**闭合测试**：从 `_cy1` 出发，把**六个排列**各再走一遍完整级联（含重打包与共享）——
QSR（`cyc2`）、SRQ（`spcy`）、QRS（`cyqrs`）、RQS（`cyrqs`）、RSQ（`cyrsq`）、SQR（`cysqr`）——
六个方向全部分毫不动。其中 S 最先的两个（`spcy`/`cysqr`）的 S 阶段只找到一笔**净亏**的招式
（−5 刀、+29,389 kg 申报 = +15,239 kg 等效，被同一把汇率门拒收）——这恰好反证了 R-last/S-middle
的读法：在 92.1572 上，"S 挪到最前"的收益空间已经被吃干。**这台机器（这组遍历 + 这组入口）
到此为止，92.1572 是它的顶**；想再往上要换招式（新的遍历/新的搜索），不是换顺序。

最高的两个落点都严格校验零违规、都在窗口五发清单里（见 `docs/CURRENT.md` 与
`docs/WINDOW_RUNBOOK_20260927.md`）：`runs/_cy1.json`（= `cand_cy1`，队列 `e07bd9ea`，
sha256 `13b8b017434f9139928ec6a6aef2d368eca593a4bf66c3da259b60407cda1a20`，本地
**92.15723882698761**，2,275 批）与 `runs/_qs1.json`（= `cand_qs1`，队列 `6822d5cd`，
sha256 `562747594a78de8ea230aac43741b3073a2cccc56c87bafef140bca241a49876`，本地
92.15543350668058）。`rsplit` 的落点 `runs/_trsplit6.json` 已于 09-26 深夜打包为
`cand_trsplit6`（`artifacts/candidates/cand_trsplit6/`，本地 92.15160570665073，sha256
`117901965088ada8…`，过 MCP 独立校验），列在窗口回退表。
**轨迹是可逐字节复现的**：`splitfirst` 整条链重跑一遍，最后一阶段的产物与第一次的哈希完全相同
（`4db8d009f46b76de…`）；`_cy1` 就是 `cyc` 第七阶段的 `_tcyc7.json`。复现（每阶段跑到 stats 的
`cost_delta_kg_equivalent == 0.0` 即停；其余轨迹见 `runs/_traj.py` 的 `TRAJECTORIES`）：

```bash
# qsplit：Q 先、S 居中、R 最后（产出 runs/_tqsplit7.json = runs/_qs1.json）
.venv/Scripts/python.exe tools/analysis/shift_cuts.py runs/_iter11.json --min-rounds --mixed-rounds \
  --requant-cuts --output runs/_tqsplit1.json --stats runs/_tqsplit1.stats.json
.venv/Scripts/python.exe tools/analysis/shift_cuts.py runs/_tqsplit1.json --min-rounds --mixed-rounds \
  --requant-cuts --split-schemes --output runs/_tqsplit2.json --stats runs/_tqsplit2.stats.json
.venv/Scripts/python.exe tools/analysis/shift_cuts.py runs/_tqsplit2.json --min-rounds --mixed-rounds \
  --requant-cuts --recount-rounds --split-schemes \
  --output runs/_tqsplit3.json --stats runs/_tqsplit3.stats.json
.venv/Scripts/python.exe tools/analysis/repack_batches.py runs/_tqsplit3.json \
  --output runs/_tqsplit4.json --stats runs/_tqsplit4.stats.json
.venv/Scripts/python.exe tools/analysis/shift_cuts.py runs/_tqsplit4.json --min-rounds --mixed-rounds \
  --requant-cuts --recount-rounds --split-schemes \
  --output runs/_tqsplit5.json --stats runs/_tqsplit5.stats.json
.venv/Scripts/python.exe tools/analysis/share_orders.py runs/_tqsplit5.json \
  --output runs/_tqsplit6.json --stats runs/_tqsplit6.stats.json
.venv/Scripts/python.exe tools/analysis/shift_cuts.py runs/_tqsplit6.json --min-rounds --mixed-rounds \
  --requant-cuts --recount-rounds --split-schemes \
  --output runs/_tqsplit7.json --stats runs/_tqsplit7.stats.json   # 即 runs/_qs1.json = cand_qs1
```

把同一组七条命令以 `runs/_qs1.json` 为输入**再跑一遍**，即 `runs/_tcyc7.json` = `runs/_cy1.json` =
`cand_cy1`（92.1572，2,275 批）；第三遍净变化 0。`cyc` 的全部增益来自其中第二条命令
（`[M,X,Q,S]` 阶段，两轮 −26,149 kg），其余阶段 `cost_delta` 都是 0。

**这条链现在是仓库里的 tracked 工具**（09-27 凌晨落地，代码截止前可复现的获胜配方）：

```bash
.venv/Scripts/python.exe aic.py trajectory runs/_iter11.json --order QSR --passes 2 \
  --output runs/_repro.json --stats runs/_repro.stats.json
```

`tools/analysis/trajectory.py` 把 `--order` 展开成**累积前缀阶段**（`[M,X,Q]` → `[M,X,Q,S]` →
`[M,X,Q,S,R]`），每阶段**迭代到自己的 `cost_delta` 为 0**，再跑重打包 → 全 flag → 共享 → 全 flag 级联，
整个轨迹跑 `--passes` 遍；每阶段的中间产物、`reps`、`delta` 写进 `--stats`，最终分低于输入分就删掉输出并以
非零码退出。`tests/test_trajectory.py`（14 例）钉住阶段展开、四类停止规则（keep-input / 零 delta /
无 delta 工具分数走平 / `--max-reps` 上限）与"不得低于输入"的守门。

**09-27 凌晨复核结论**：(a) **命令行上的 flag 顺序不影响结果**——`--requant-cuts --split-schemes
--recount-rounds` 与 `--… --recount-rounds --split-schemes` 两种写法产出**同哈希**
（`0a66a8dbc2cddd24…`），因为 `shift_cuts` 内部按固定顺序执行各相，命令行只开关；
(b) **"每阶段迭代到不动点"不是可选项**：`_tqsplit2.json` 单独跑一次第 3 阶段只到 92.1135（10,214 轮），
再迭代两轮（−112,022 kg、−4,072 kg）才落在 92.1215，其产物与第一次的 `runs/_tqsplit3.json`
**逐字节相同**（`01d830d052e7fca4…`，10,215 轮 / 2,280 批）——即一次调用可能离该阶段的不动点还差
0.008 分，级联里"一阶段一跑"的写法会漏掉它。

## 结构性天花板

- 剩余 **95,084 片（≈5.8M kg）在当前工具集下花不动**：每个订单余量中位数只有 18 片，
  而一次 count 步最小消耗 ≈ 轮内 k（中位 16–20）、一次片段删除要 count（55–75）。
  余量**按订单隔离**、且每单只够约 1.2 个轮次单位 —— 碎片化，不是排序问题（换 tightest-first 无改善）。
- 理论理想（每轮 count 都降到底）可花 174,752 片；实测片段删除捕获率 ≈ 80%。
- 上界：220,141 片 × 61 kg ≈ 13.5M kg ≈ +1% 成材率 ≈ +0.4 分；本轮已实现 6.25M kg 的申报下降。

## 下一阶段的靶（09-26 深夜按 `_rp4` 重量的地板；09-27 凌晨按 `_cy1` 修订）

刀数 = 174,297 片段 + 10,207 轮 = 184,504（`_cy1`）：

- **片段地板** Σₒ ⌈piecesₒ/wcapₒ⌉ ≈ **173,769**（差 **528** ≈ 0.10 分）—— 片段这一项已经接近理论值，
  不值得再投。*（更正：早期文档写的 168,836 是浮点 ⌈⌉ 的假象，同一个和用整数上取整算出来就是 173,769，
  两者相差 4,933 片；不要再按 1.07 分去追。）*
- **轮数地板**：**按现批次**重排（不动批次组成）是 **9,792 = 10,210 − 418**（`_batch_floor.py` 对 `_rp4`
  报 418 轮的可省量 ≈ 0.16 分；它是"每批链长/床重下界"的和，还没算 `cap(c)` 与账单台阶，所以同样偏乐观）。
  **跨批重排的地板 6,587（`_group_floor.py`，按宽度上限切、"每轮填满 148 m"计算）已在 2026-09-26 夜
  被实测否证**：真正重排一遍组成只买到 8 轮（见上文"跨批重打包"），因为 `cap(c)` 与账单台阶都不允许把轮填满。
  **不要再按 0.62 分去追这个靶。**
- **覆盖率**：`_rp4` 曾有 15 单从不共享（99.85%），**已被 `share_orders` 买到只剩 2 单**（99.98%）；
  余量 ≈0.004 分，且这 2 单被"每方案 ≤6 轮"挡住（见上文"从不共享一轮的订单"）。**这条靶打光了。**
- 装填：轮的净长平均 **96.1 m**（p50 88 / p95 142）、**85.8%** 的自身 `cap(c)`，10,210 轮里只有 1 轮顶到
  `cap`；`count` 平均到**辊道宽度上限的 97.6%**；9,037/10,210 轮受床重挡（`cap(c) < 148`）；
  dia43 等粗规格的轮容量 ≈89 m×46 根，而非 148 m。
  未顶满的原因**不是排序问题**：8,445/10,210 轮的净长已经停在"再切就要多付一根坯料"的账单台阶下沿
  （平均只剩 3.1 m）。
- 剩余交付余量 95,514 片（每单中位 18 片）：一次 count 步最小消耗 ≈ 轮内 k（中位 16–20），
  碎片化挡住，理论可花 174,752 片里实测捕获 ≈ 80%。

## 复现

```bash
# 一、每次只多开一个 flag，每阶段迭代到自己的不动点（这一段是 stored 谱系的真实走法）
.venv/Scripts/python.exe tools/analysis/shift_cuts.py runs/_iter11.json --min-rounds --mixed-rounds \
  --output runs/_rq1.json --stats runs/_rq1.stats.json
.venv/Scripts/python.exe tools/analysis/shift_cuts.py runs/_rq1.json --min-rounds --mixed-rounds --requant-cuts \
  --output runs/_rq2.json --stats runs/_rq2.stats.json      # 再跑一遍即 _rq3（= cand_rq3），净变化 0.0 即停
.venv/Scripts/python.exe tools/analysis/shift_cuts.py runs/_rq3.json --min-rounds --mixed-rounds --requant-cuts \
  --recount-rounds --output runs/_rc1.json --stats runs/_rc1.stats.json       # → _rc2 → _rc3
.venv/Scripts/python.exe tools/analysis/shift_cuts.py runs/_rc3.json --min-rounds --mixed-rounds --requant-cuts \
  --recount-rounds --split-schemes --output runs/_rf1.json --stats runs/_rf1.stats.json  # → _rf2 → _rf3
```

**这条链不能塌成一次调用**：五个 flag 一次开全再迭代到不动点，落的是**相邻的另一个不动点
92.1415**（见下面「轨迹」一节），不是 `_rf3`。92.11/92.14 这条线是**分阶段**走出来的，复现也要分阶段；
`runs/_traj.py staged` 把每一阶段逐步打印出来（并把 `cost_delta` 与 stored 逐位对照）。
`_cand_shift2.json` 由 `cand_rounded` 经同一脚本（`--min-rounds`）产生；
`_iter1.json` 是 `_cand_full7` 的固定点；加 `--mixed-rounds` 后连跑即 `_iter11.json`（= `cand_iter11`）。
其余四条谱系（`_cand_full2/full4/full8/shift2`）用同一条全命令各跑一遍，得到 `_p_*.json`
（92.01–92.08，结构互不相同，作为窗口内的多样性保险）。

跨批重打包是独立入口，接在 `_rf3` 之后；它自己跑严格校验，分数不升就以非零码退出（"keep the input"）：

```bash
.venv/Scripts/python.exe tools/analysis/repack_batches.py runs/_rf3.json --output runs/_rp3.json \
  --stats runs/_rp3.stats.json
.venv/Scripts/python.exe tools/analysis/shift_cuts.py runs/_rp3.json --min-rounds --mixed-rounds \
  --requant-cuts --recount-rounds --split-schemes \
  --output runs/_rp4.json --stats runs/_rp4.stats.json
```

`runs/_rp2.json` 是同一流程从 `_rp1.json`（同一重打包器的前一版估价排序）出发的结果，
与 `_rp4.json` 逐字节相同——估价排序的修复只影响退化链（测试里钉住），对这个计划改不动结果。

让从不共享的订单共享一轮也是独立入口，接在 `_rp4` 之后，同样自带严格校验与"不升就退出"：

```bash
.venv/Scripts/python.exe tools/analysis/share_orders.py runs/_rp4.json --output runs/_sh1.json \
  --stats runs/_sh1.stats.json
.venv/Scripts/python.exe tools/analysis/shift_cuts.py runs/_sh1.json --min-rounds --mixed-rounds \
  --requant-cuts --recount-rounds --split-schemes \
  --output runs/_rp5.json --stats runs/_rp5.stats.json      # 再跑一遍得 _rp6，净变化 0.0
```

`runs/*.stats.json` 保留每一相的计数；`tests/test_shift_cuts.py`（9 个用例）钉住混合重切的交付下限、
`requantize`/`recount`/`split` 三条不变量（交付下限、轮数不升、分数不降）与覆盖率守门，
并在工作树里对真实计划跑一遍严格校验器。
`tests/test_repack_batches.py`（6 个用例）钉住"两个批次共享一条链就该折成一"的合成例、
订单恰好落一个批、更贵方案回退原批的门控、count 扫描的辊道上限与"高 count 陷阱"，
并在工作树里对真实计划最小的两组跑一遍重打包。
`tests/test_share_orders.py`（7 个用例）钉住 `lone_orders` 的语义（只报从不与别人同轮的订单）、
一次合并**买卖双方各自的价**（两单转共享、轮数不升、不超床重）、**一份共享正好值 `COVER_BONUS_KG`**
（同订单同钢种、一份共享排布 vs 一份独占排布，差价 = 1 刀 + 1 根坯料 + 2 份共享）、
更贵候选被门控拒收后计划原封不动、无单可买时一趟都不扫，并在工作树里对真实计划跑一遍共享遍历
（订单集不变、共享数不降、价格不升、每一轮仍合法）。
