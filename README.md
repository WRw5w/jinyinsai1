# 棒材组合订单锯切优化

**继续工作请先读 [HANDOFF.md](HANDOFF.md)。** 当前已打包的新口径候选为 `submission_calibrated/初赛结果_棒材优化.zip`：90000刀、95.37106%成材率、本地预测98.60732分，物理与独立校验通过，尚未提交。历史最佳官方反馈仍为96.49分。下方历史优化记录包含旧评分数字及命令，不作为当前续跑入口。

**2026-09-16 10:02 官方回退反馈：此前标为“本地98.63”的 `submission_normal_985` 实际为89.19分（119268刀），不要再次提交。** 已验证的最佳历史包仍是 `submission_optimized/初赛结果_棒材优化.zip`（96.49分），其备份在 `submission_restore_9649/`。下文所有旧“本地评分”、99.04/99.18等上界只针对已经失配的旧模型，不能用于预测官方成绩。

已用三份提交精确复现官方刀数：**每个订单段分别计 `int(length // size) + 1`**，不是每轮统一加一刀。官方成材质量表现为把订单直径先截断到整数毫米，再计算圆截面；同时复现了三次成材率与历史395条超重。证据见 `diagnostics/knife_calibration.json`、`diagnostics/yield_semantics.json`。物理校验继续采用原始小数直径，因此仍保守满足设备承重和宽度。

新的评分预测器为 `platform_score.py`，新DP入口使用 `optimize_patterns.py --calibrated`。三份历史反馈及相关边界行为的5项回归测试已通过；不再根据旧估分推荐提交。刀数子分是否封顶、其他隐藏规则仍未完全确认；不能把公式复现说成已取得官方评分器源码。

提供两种可以直接运行的算法：**穷举当前模型的全局最优解**，以及**以 10 秒为预算的随机邻域搜索**。Python 3.10+，仅使用标准库，可在 Windows / Linux 运行。

## 2026-09-16 历史优化记录（旧评分已废弃）

**已确认失配：`submission_normal_985/初赛结果_棒材优化.zip` 官方实际89.19分。** 当时旧模型误报98.632530、91596刀、97.778347%成材率。虽然当时29项测试和物理校验通过，但没有验证官方评分口径，因此这些测试不能支持旧估分。不要重新提交此包。

用户提供的前20榜单、两次官方反馈和对应提交文件快照已经存放在 `evidence/20260916/`，入口 `EVIDENCE.md`。前三行榜单排名保留为空；源JSON/ZIP保留SHA256。`model_bounds.md/.json` 保存当前条件模型的乐观界及推导，不把它称为官方界。

这轮新增 `heterogeneous_search.cpp/.py`：两轮允许不同的并列支数，用C++束搜索重新分配整数支数；`normal_group_search.py` 和 `normal_component_pack.py` 扩大到跨旧方案及整个真实订单组件；`material_refine.py` 优化组件坯型与多轮整坯容量。每步必须通过完整物理校验才保留。`run_normal_pipeline.py` 自动组合候选、继续搜索并打包，`runs/normal_target_99_watch/task_status.json` 记录后续冲99任务的真实终态。本轮追加三次搜索已完成，最后组合结果见 `runs/normal_final/`，本地99尚未达到。

99仍是目标，并未保证能达到；在正常阶段完成前，评分边界/100分实验保持待办。可继续运行：

```powershell
g++ -O3 -std=c++17 heterogeneous_search.cpp -o heterogeneous_search.exe
python -X utf8 heterogeneous_search.py --initial runs/normal_combined_release/result.json --output runs/normal_next --seconds 120 --beam 64
python -X utf8 material_refine.py --initial runs/normal_next/result.json --output runs/normal_next_material --seconds 15 --passes 2 --repack-seconds 60
python -m unittest -v test_solver test_competition test_platform test_patterns test_heterogeneous test_normal_group_search
```

**上一轮待提交包：`submission_continued/初赛结果_棒材优化.zip`。** 多权重候选组合、单轮调整、两轮及三轮联合重排已完成。本地 92043 刀、95.8684% 成材率、99.98% 有效订单组合覆盖率，估分 97.8687；相比已提交包的本地 95.8414 提高 2.0272 分，少 3225 刀、少用钢坯 2922.011 吨。每单额外支数上限仍为 5%，实际按重量汇总增产 1.8324%（相对向上取整的整支需求）。24 项测试及实际 ZIP 独立校验全部通过。**这不是官方新成绩**；完整对比在 `submission_continued/comparison.json`。

复现打包：

```powershell
python -X utf8 build_submission.py --input runs/continued_triples/result.json --config runs/continued_triples/config.json --audit runs/continued_triples/data_audit.json --output-dir submission_continued
```

平台最新反馈：`submission_optimized/初赛结果_棒材优化.zip` **可行，96.49 分，第 28 名**；官方刀数 92913、成材率 92.51%、覆盖率 99.98%，子分 96.86/92.51/99.98/100。反馈及对应 JSON 哈希已保存到 `submission_optimized/official_feedback.json`，该包保留为已验证基线。下表 95.84 仍是这一包的本地估分。

继续搜索新增：`--yield-credit 1.065` 将产出重量计入材料目标（最小化 `刀数 + lambda * (钢坯重量 - 1.065 * 产出重量)`），改善原来仅按投入钢坯重量选型的偏差；`refine_patterns.py` 在同钢种、同直径的完整订单组之间选择候选，再逐轮调整并列支数与倍尺；`pair_refine.py` 联合重排两轮，可合并成一轮或重新分为两轮，逐次保留总估分改善。每单额外支数上限保持 5%，每个订单仍只出现在一个方案中。

后台使用 `task_watcher.py` 原生阻塞等待，日志和真实退出码保存在 `runs/continued_*_watch/`，没有创建定时轮询。两轮重排的 `--seconds` 是邻域搜索预算，不包含随后最多四遍单轮收尾和文件校验；按墙钟截止导致不同机器上探索次数可能不同。

2026-09-16 续搜过程已得到本地 97.7252 分的可行检查点（92215 刀、95.6333% 成材率），压缩包保存在 `submission_continued_checkpoint/`。这不是官方新成绩。一次用户中断停止了前台进程树，已经从检查点恢复；新的三轮搜索使用隐藏独立进程托管。`pair_refine.py --source-rounds 3` 或 `4` 可扩大联合重排范围，仍逐订单检查需求和超产边界。已验证的 96.49 分包在 `submission_optimized/`，不会被这些命令覆盖。

续搜命令示例（后台托管时给 `task_watcher.py` 传递同样的 worker 参数）：

```powershell
python -X utf8 pair_refine.py --initial runs/continued_combined/result.json --output runs/continued_pairs_next --seconds 90 --source-rounds 3 --seed 20260917
```

每接受一项变更都会更新实际交付支数；每 100 次接受保存可行检查点，结束时重新逐行校验整数倍、床长/床宽/承重、整单唯一性、钢坯供应和每单 5% 上限，并调用独立校验器。该启发式不提供全局最优证明。

上一轮包为 **`submission_optimized/初赛结果_棒材优化.zip`**。该轮新增 C++ 整数动态规划、跨订单短轮拼接及刀数/耗材权重扫描，已跑完并打包；当时 22 项测试通过，实际 ZIP 的独立物理校验零违规。4999 单的覆盖范围与已被平台接受的旧修正版一致。

以下全部为同一本地口径，不能直接当作官方得分：

| 方案 | 刀数 | 成材率 | 估分 | 实际额外产量比例 |
| --- | ---: | ---: | ---: | ---: |
| 原修正版 | 116330 | 87.58% | 87.22 | 0% |
| 严格整支需求备选 | 115558 | 92.40% | 88.87 | 0% |
| 每单额外支数上限 2% | 96838 | 93.40% | 95.19 | 0.39% |
| 推荐：每单额外支数上限 5% | 95268 | 93.52% | 95.84 | 0.43% |

额外产量相对各单向上取整的需求支数，按重量汇总。推荐包实际单单最大增产 2.8436%，总增产约 1081.615 吨；题面四条约束未列出超产上限，配置上限是搜索设置，仍需平台确认。备选在 `submission_optimized_2pct/` 和 `submission_optimized_exact/`，后者名称表示不额外超产，不表示已求得全局最优。

用户反馈的旧修正版官方成绩是 **87.65 分、113683 刀、86.63% 成材率、99.98% 覆盖率**。本地仍有计分口径差异；新方案估分使用 40/30/20/10 权重，推测刀数基准 90000、刀数子分封顶 100、时间满分。后续 `submission_optimized` 已由用户提交得到 96.49 分，最新 `submission_continued` 尚未取得官方成绩。每个包附 `validation_report.json`，包括实际超产、源文件和 SHA-256。

复现推荐方案（需要 C++17 编译器；Python 无第三方依赖）：

```powershell
g++ -O3 -std=c++17 pattern_dp.cpp -o pattern_dp.exe
python -X utf8 optimize_patterns.py --output runs/pattern_dp_pool_5pct --overproduction 0.05 --pool-short --lambdas 0.00015 0.0003 0.0006
python -X utf8 build_submission.py --input runs/pattern_dp_pool_5pct/result.json --config runs/pattern_dp_pool_5pct/config.json --audit runs/pattern_dp_pool_5pct/data_audit.json --output-dir submission_optimized
python -m unittest -v test_solver test_competition test_platform test_patterns
```

按规格和坯型共享动态规划，枚举合法倍尺/并列数模式，最小化 `刀数 + lambda * 钢坯重量`；随后拼接同钢种、同直径、同并列数的短轮，不能补足 50m 的组件全部回退到合法构造。最后按完整方案的估分挑选。DP 只在单规格标量子问题上精确，完整算法仍为启发式。设每个子问题生产上限为 Q、去重模式数为 P，单次 DP 时间 O(QP)、DP 数组空间 O(Q)；还需原始模式存储、各订单方案重建及最坏二次的轮次配对，多个权重分别运行。这里没有把全局组合问题称为多项式精确算法。

此次三权重搜索含初始化约几十秒，不是严格 10 秒版本，也没有重跑半小时。历史运行指标中的 `elapsed_seconds` 从模式枚举后开始；新代码另记录包含枚举的 `total_elapsed_seconds`。原 10 秒与半小时入口仍见下节。

正式压缩包现已解压到 `data/`，实际文件名为 `constraints.txt`、`orders_quarter.csv` 和 `blank_used.csv`。其约束只有四条：床长 50–150 m、宽度 2 m、承重 60 t、同钢种同规格组合。正式数据共 5000 单，其中 `A20260949` 的定尺为 **-1100 mm**，已隔离并保留原值，当前求解覆盖其余 **4999 单**；旧修正版已获平台可行反馈，仍不声称包含全部原始订单。

平台反馈已确认长度表示与旧版不同：`length_scheme` 必须填不含切损的净定尺整数倍，整轮统一增加 2m 计算辊道长度和承重。正式配置已改为 `length_mode=net_shared_trim`。冷床轮数、钢坯逐根分段等未明确规则仍采用下文列出的模型假设。精确算法的“最优”仅针对该契约和所选目标；搜索算法不声称全局最优。旧程序和被拒包保留在 `legacy/platform_before_fix/`，只能用于回归诊断。

## 正式数据：10 秒与半小时版本

旧修正版基线包为 **`submission_fixed/初赛结果_棒材优化.zip`**，根目录只包含同名 JSON。旧 `submission/` 包已被平台判为不可行，不应继续使用。可用 `python -X utf8 build_submission.py --team 实际队名` 重新校验并按队名打包该基线；优化包须使用上节完整命令。

修复先将旧半小时解转为净长度，再重排转换后存在 180 个过短轮次的 143 个方案，随后搜索 10 秒。修复后打包共 130 个方案、8236 轮；模型刀数 116330、成材率约 87.5820%、有效订单组合覆盖率约 99.98%，原始 5000 单口径 99.96%。刀数口径也已改为每轮共用头尾刀，不能把旧错误模型的指标直接当作可行基线。详见 `runs/platform_fix/repair_report.json`、`submission_fixed/validation_report.json` 和 `submission_fixed/platform_check.json`。

独立的 `platform_check.py` 从原始 CSV 与最终 ZIP 重算，未调用求解器公式。它复现旧包 11315 条非整数倍、44 条长度错误及报告示例位置；重量采用数学 pi 计算的含余量质量，可检出 437 处，比平台报告的 395 处更保守，尚未获得平台私有重量判断实现。修正版三类检查均为零，物理长度范围 50–150m、最大床上质量约 59996.847kg。新包尚待平台复测，不以本地通过冒充官方接受。

**旧修正版包含 4999 单，用户已反馈平台判定可行。** `A20260949` 仍按异常数据过滤，没有推测其正确定尺；新增优化包需要另行提交验证。

```powershell
# 按原始表头单位转换；保留原文件并生成异常审计记录
python -X utf8 prepare_data.py

# 10 秒：稳健单订单构造 + 局部搜索
python -X utf8 competition_solver.py --seconds 10 --output runs/quick_10s/result.json

# 半小时：从 10 秒结果继续搜索，每分钟保存最优解
python -X utf8 competition_solver.py --seconds 1800 --initial runs/quick_10s/result.json --output runs/half_hour/result.json

# 验证正式数据结果；必须用规范化数据和正式配置
python solver.py --orders data/orders.normalized.csv --blanks data/blanks.normalized.csv --config data/competition.config.json --validate runs/quick_10s/result.json

python -m unittest -v test_solver test_competition

# 将旧半小时结果迁移到已确认的净长度口径并修复
python -X utf8 repair_platform_result.py
python -X utf8 build_submission.py
python -X utf8 platform_check.py 'submission_fixed/初赛结果_棒材优化.zip'
python -m unittest -v test_solver test_competition test_platform
```

`competition_solver.py` 增加了满足 50 m 下限的整支拆轮构造：预计算合法的单订单倍尺/并列模式，用整轮与一轮或两轮余量配对实现足额交付，避免贪心最后剩下不足 50 m 的尾单。组合方案还会融合并列支数相同的轮次。两种预算均使用合并、拆分、搬移、交换和重排邻域；半小时版通过暖启动与更长搜索获得更多改进机会。

原始钢坯长度是 **mm**，订单重量是 **t**，密度由原表头明确给出 **9.86 g/cm³**，统一换算为 m、kg、9860 kg/m³。两种坯重分别为 9613.5 kg、6162.5 kg。`data/data_audit.json` 记录异常与剩余假设，`data/competition.config.json` 保存本次参数。

历史半小时运行目录：`runs/half_hour_20260915_2114/`，使用旧长度口径，只作为迁移来源，不能直接用新配置续跑。修复结果在 `runs/platform_fix/`；如需继续搜索，应以其中 `result.json` 暖启动。历史目录的 `source/` 是启动时源码快照，保持不变。

本会话没有可调用的 `task_watcher` MCP 工具，使用项目内 `task_watcher.py` 后台托管子进程，通过原生阻塞等待获取真实退出码。状态保存在 `task_status.json`，完成或失败后写入终态；它不承诺自动唤醒当前对话。进程在隐藏窗口后台运行，35 分钟看门狗只会停止其自己创建的超时工作进程。

10 秒运行指标与 1800 秒搜索计时包含构造和求解函数内校验，CSV 读取、外层结果写盘、进程启动不包含在预算内；普通系统不提供硬实时保证。长任务使用的默认目标仍是刀数优先的字典序目标，不是假定的赛方评分。

## 直接运行

```powershell
# 精确最优，小数据使用
python solver.py --orders sample_orders.csv --mode brute --output brute.json --metrics brute.metrics.json

# 10 秒预算，包含可行初解构造
python solver.py --orders sample_orders.csv --mode search --seconds 10 --seed 1 --output search.json --metrics search.metrics.json

# 独立验证已保存的结果，不重新求解
python solver.py --orders sample_orders.csv --validate search.json

# 使用钢坯尺寸、配置，以及 PDF 第 6 页写出的客观评分公式
# 此处基准刀数 8 仅用于演示，正式运行须填写赛方基准值
python solver.py --orders sample_orders.csv --blanks sample_blanks.csv --config config.example.json --mode brute --objective score --baseline-knives 8 --output brute.score.json

# 回归测试；包含独立、无剪枝的小实例枚举对照
python -m unittest -v test_solver

# 2 万条合成订单性能测试，不代表正式数据上的得分
python benchmark.py --n 20000 --seconds 10 --output benchmark.20000.metrics.json
```

通用格式数据可使用旧入口；本次正式数据优先使用上面的 `competition_solver.py`：

```powershell
python solver.py --orders order_quarter.csv --blanks blank_used.csv --config config.json --mode search --seconds 10 --output result.json --metrics result.metrics.json
```

结果保留题面五个字段：`orders`、`length_scheme`、`counts`、`blank_type`、`blank_counts`。指标单独打印或写入 `--metrics`，不混入提交 JSON。失败时返回退出码 2，说明原因，不输出缺单的“成功解”，也不覆盖已有结果文件。

## 目标函数

- `lex`（默认）：先最小化**总刀数**，刀数相同再最大化**全局成材率**，然后最大化**组合覆盖率**。这是明确选择的字典序目标，不等于赛方综合评分。
- `score`：最大化 PDF 第 6 页给出的 `40 * 基准刀数 / 总刀数 + 40 * 成材率 + 20 * 组合覆盖率`。必须设置 `baseline_knives` 或 `--baseline-knives`。只考虑可行解，因此违规罚分为 0。
- `platform_score`：按反馈权重估算 `40 * min(1, 基准刀数 / 总刀数) + 30 * 成材率 + 20 * 组合覆盖率 + 10`，必须设置基准刀数；封顶与时间满分均为估算假设。

PDF 同页文字写了 40% / 30% / 20% / 10%，随后公式却使用 40 / 40 / 20，且没有时间项。本实现的 `score` 采用该明示公式，不擅自补入时间分。获得正式评分器后应核对。浮点目标比较将成材率和分数保留到小数点后 12 位，避免加法顺序噪声改变排序。

## 已实现的有限模型

设订单 `i` 的定尺为 `s_i` 米、直径 `d_i` 毫米、密度 `rho_i` 千克/立方米，则米重为 `mu_i = pi * (d_i/1000)^2 / 4 * rho_i`。

1. **数量与超产。** 需求支数 `q_i = ceil(订单重量 / (mu_i * s_i))`。实际交付支数在 `[q_i, q_i + floor(q_i * max_overproduction_ratio)]` 内，默认比率为 0，即恰好满足向上取整后的支数。重量换成整支已可能略有超产，不额外强制“重量完全相等”。不允许跨组合方案拆分一个订单，同一订单可出现在本方案的多轮中。
2. **同质性。** 同方案内钢种、直径必须一致。同钢种同直径订单密度不一致会报错；不按直径近似合并。
3. **每轮参数。** 为每个订单选择非负整数 `k_i`，至少一个大于 0。正式模式 `net_shared_trim` 输出净长度 `L_i = k_i*s_i`，JSON 不含切损。全轮共享一个并列支数 `p`，该订单交付 `k_i*p` 支。物理棒材长度为 `T = sum(L_i) + 2*trim`，每轮只加一次头尾余量。
4. **设备限制。** 检查物理长度 `T` 的上下限与床上重量 `T*p*mu` 的承重上下限。不能只检查净长度而漏掉 2m 余量。`bed_width` 表示**支数上限**；可另设 `bed_width_mm` 与 `bar_gap_mm`，校验 `p*d+(p-1)*gap <= width`。每个方案最多 `max_rounds` 轮。
5. **刀数。** 与共用头尾余量一致，每轮按 `sum(k_i)-1` 次内部切分加 2 次头尾切，得到 `sum(k_i)+1`，不乘并列支数。PDF 的物理 50m、定尺 3m 例子输出净长度 48m，校验物理长度 50m，刀数为 17。具体官方计分细节仍以评分器为准。
6. **钢坯假设。** 同一方案使用同一种钢坯。钢坯重量 `M = 宽度(m)*厚度(m)*长度(m)*密度`，可轧长度 `U = M*rolling_yield/mu`。每个订单段加共用头尾余量不能超过 `U`。每轮数量按 `ceil(T*p/U)` 计算，包含并列支数和整轮余量。精确枚举只考虑不会恶化目标的最小数量。
7. **物料口径边界。** 第 6 项是每轮汇总物料模型，尚未处理各支钢坯逐根分段装箱、跨轮余料复用、坯型库存、轧制节奏及隐藏的轮次限制；例如总长度足够不一定意味着逐根装箱可实现。不能据此宣称正式生产可行。若不传 `--blanks`，用配置 `blank_length` 作为有效轧长；`blank_weight=null` 时按米重和轧制收得率反算示例坯重。此时 `blank_type=1` 是各同质组的演示占位类型，并非真实统一规格。
8. **真实成材率。** 分子是所有实际产出的定尺支数乘单支重量；分母是实际消耗的所有钢坯重量。组合覆盖率只统计多订单方案里的订单。同一方案允许不同订单分处不同轮，这一点也应与正式组合约束核对。

长度、重量采用双精度，边界判断有微小数值容差，不支持无限精度的输入。正式数据若要求毫米或克级精确整数计量，可在约束明确后替换相关换算。

为保持旧演示数据和回归测试可复现，通用 `Config()` 默认仍为 `trimmed_segments`；此模式只用于历史示例，提交打包器明确拒绝。正式命令必须使用含 `net_shared_trim` 的 `data/competition.config.json`。独立提交校验不接受切换成旧模式来绕过整数倍检查。

## 暴力算法为什么能够证明最优

精确入口为 `brute_force(orders, cfg, blanks=None)`，没有把贪心结果当作穷举：

1. 按钢种和直径分组，对组内每个非空订单子集分别生成方案。
2. 枚举钢坯类型、`p=1..并列上限`、所有受需求上限和床长约束的整数倍尺向量。长度剪枝只去掉不可能可行的向量。
3. 以“已用轮数、各订单累计交付支数”为状态，枚举所有合法下一轮，直至 `max_rounds`。同一状态只删除刀数及钢坯重量都不更优的方案；仍保留刀数和物料消耗之间的取舍。达到最低需求后若仍允许超产，会继续探索，不提前漏掉有价值的方案。
4. 枚举所有订单集合划分。固定当前最小编号订单所在子集，消除分组顺序重复；对子问题记忆化。
5. 各同质组之间也保留并组合 Pareto 候选，最后以**全局**目标选最优。成材率是全局比值，不能简单地把各组最高成材率方案拼起来。

任何可行解都可还原为上述子集划分、坯型和轮次序列；被删方案都有对所有目标不劣的替代，因此枚举完成后最优值有保证。

复杂度不只是订单集合划分的 Bell 数：还包含倍尺向量枚举、生产状态数 `product(cap_i+1)`、轮数、坯型数及 Pareto 前沿大小。**仅用于小规模校验**，订单少但需求支数巨大时也会爆炸。默认每个同质组最多 10 单、最多 100 万次受计数的搜索扩展。超限抛出 `ExactLimitError`，不把中途结果标成最优；可按需要提高配置上限。

## 10 秒搜索

入口保持兼容：`search_10s(orders, cfg, seconds=10.0, seed=1, blanks=None)`。

先生成全部订单的可行初解，再随机选择同质组，执行合并、拆分、搬移、交换、重排倍尺/并列数五类操作。邻域方案从不同并列支数、订单顺序和填充程度中重建，并用退火式接受准则允许暂时变差；始终单独保留迄今最优完整方案。单个搜索组合默认最多 8 单，这是启发式范围限制，不限制精确算法。

每次只更新受影响方案的指标，保存最优解用变更记录，避免每次邻域尝试都复制或重算几万条订单。最终从 JSON 独立复算所有约束和指标。

计时从函数入口开始，包含初始化，并预留 `min(预算*20%, 0.01+订单数*0.00005)` 秒给输出和校验。CSV 读取、命令行写文件及启动 Python 的时间不计入搜索预算。内部多处使用单调时钟检查截止时间，不进行外部轮询。普通操作系统调度与校验时间仍使它不能承诺硬实时的绝对 10.000 秒上限；具体耗时由 `elapsed_seconds` 记录。

若时间耗尽前没有完整可行初解，会明确报错，不返回部分订单；贪心初解失败只表示“搜索未找到”，不会误报成数学上无解。已有完整初解时，超时返回迄今最优解。随机种子固定随机数流，但按墙钟停止使不同机器的迭代数与最终结果可能不同。

## 输入、配置和验证

- 订单 CSV：`order_id,steel,diameter,length,weight,density`，支持示例中的中文别名与 UTF-8 BOM。直径 mm、定尺 m、密度 kg/m³；重量默认 kg，吨数据用 `weight_scale=1000`。
- 钢坯 CSV：`blank_type,width_mm,thickness_mm,length_m,density`；宽厚 mm、长度 m、密度 kg/m³。可用 `width,thickness,length` 等别名，但单位口径不变。未提供类型 ID 时使用从 1 开始的数据行号。`sample_blanks.csv` 仅为人工演示数据。
- `config.example.json` 包含通用配置字段，正式数据使用 `data/competition.config.json`。求解器拒绝未知字段、重复订单 ID、负数/零需求、NaN、无效规格等数据；预处理脚本将已发现的非正定尺写入独立审计记录后隔离，不擅自修正原值。提供的 `constraints.txt` 只有四条自然语言约束，已逐条映射到配置。
- `validate_plan` 独立从提交 JSON 检查全订单覆盖、跨方案唯一性、同质性、轮数组长度、整数并列支数、倍尺整数性、需求/超产、床长宽承重、坯型、钢坯供料和成材率；不读取求解过程中的缓存指标。

`test_solver.py` 的独立基准直接枚举轮次组合和分组，不调用求解器的轮次生成、状态 DP 或 Pareto 剪枝。包括随机微型实例、多个钢坯类型、两种目标、跨同质组全局比较、允许超产、缺单/坏 JSON、PDF 17 刀例子和限时行为。`benchmark.py` 只生成工程测试数据，不用于宣称赛题得分。

2026-09-15 本机验证记录（耗时取自 Python 函数调用，包含最终校验，不含命令行启动与读写）：

| 测试 | 结果 |
| --- | --- |
| 10 项自动测试，含 36 组随机独立穷举对照 | 全部通过 |
| 示例 4 单精确算法 | 8 刀，成材率约 7.0076%，组合覆盖率 75%，约 0.0032 秒 |
| 示例 4 单，10 秒预算搜索 | 同样 8 刀、约 7.0076%、75%，约 9.9903 秒 |
| 合成 20,000 单，10 秒预算搜索 | 全部订单通过校验，59,987 刀，成材率约 71.0554%，组合覆盖率 46.955%，约 9.4927 秒 |

示例订单重量很小，默认演示钢坯较长，因此示例成材率低，这是按实际耗材计算的结果。2 万单只有性能与可行性验证，没有最优性或真实比赛得分结论；不同机器、负载和搜索迭代次数会影响结果。对应结果存于 `brute.metrics.json`、`search.metrics.json`、`benchmark.20000.metrics.json`。

## 规则与答疑

**完整、可溯源的官方口径汇总见 [`RULES.md`](RULES.md)**（含 2026-09-14 ~ 09-20 群内答疑逐条记录、
初赛/复赛差异表、以及"哪些实现必须改"的清单）。

已确认的部分（原先列在这里的悬置问题，现已由答疑解决）：

- 床长 50~150m、床宽 2m、承重 60t、同钢种同厚度才能组合 —— 来自正式 `constraints.txt`。
- **长度口径**：`length_scheme` 只含分配给该订单的部分，**不含切头切尾**（评分脚本自动补），
  且写的是**每根棒材上的分配长度**（两根 140m 棒材 → 写 140，不是 280）；单位米。
- **刀数**：初赛评分脚本给**每个订单**都加了头尾；**复赛修正为"连接处一刀 + 每轮冷床一组头尾"**，
  即一轮刀数 = 总段数 + 1，且**不乘棒材根数**。基准 90000，子项封顶。
- **超产**：初赛有惩罚规定但脚本代码有误 ⇒ **实际无惩罚**；复赛改为**欠产惩罚**，
  超产不扣分但**不计入成材率分子**。
- **成材率分母** = 各轮申报钢坯重量之和（两端各 1m 切损计入）。
- **钢坯不允许跨轮**；每轮坯料重量不低于棒材重量。
- **冷床语义**：一轮冷床 = 一个 scheme；一组方案可对应多轮冷床；**复赛轮数上限 5~8**。
- **覆盖率（复赛收紧）**：必须**同轮次共同上冷床**才算有效组合；另有**连续性约束**（跳轮违规）。
- **违规惩罚**：每条违反**从总分扣 5 分**（连续性按轮次计、跳轮按订单计）。

仍需确认：辊道超重判定依据、订单完成后停产是否允许、复赛轮数上限确切值、复赛新评分权重。
主要适配点是 `Model.make_round`、`Model.blank_length`、`platform_score.row_metrics` 与
`validate_plan`；变更后继续用 `test_solver.py` 的独立小实例枚举对拍。
