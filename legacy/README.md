# 棒材组合订单锯切优化

`solver.py` 给出两种算法：

* `brute_force`：对每个“钢种 + 直径”组枚举所有订单集合划分，并枚举每轮的可行锯切段。在当前配置模型内，它返回字典序最优解：总刀数最少、成材率最高、组合覆盖率最高。复杂度为 Bell 数，建议每个同质组不超过 12 个订单。
* `search_10s`：先为每个订单生成可行单订单方案，再在时间预算内随机合并订单，使用大邻域搜索保留最好可行解。默认严格运行不超过 10 秒（参数 `--seconds` 可调整）。

## 运行

```powershell
python solver.py --orders sample_orders.csv --mode brute --output brute.json
python solver.py --orders sample_orders.csv --mode search --seconds 10 --output result.json
```

真实数据：

```powershell
python solver.py --orders order_quarter.csv --config config.json --mode search --seconds 10 --output result.json
```

订单 CSV 支持中英文列名：`order_id/订单号`、`steel/钢种`、`diameter/直径`、`length/定尺长度`、`weight/重量`、`density/密度`。题面中的 `constraint.txt` 未随 PDF 提供，故现场参数集中在 `Config` 或 `config.json`：冷床长宽承重、两端切损、钢坯有效长度等必须按赛方文件替换。

输出是题面要求的 JSON 数组，字段为 `orders`、`length_scheme`、`counts`、`blank_type`、`blank_counts`。内部评估字段不会写入输出。

## 模型说明

一轮中每个订单段长度为 `k * 定尺长度 + 2 * trim`，`k` 为整数；并列支数由冷床宽度限制。刀数按每轮头尾 2 刀，加上每个定尺段内部的分切刀数计算。钢坯消耗按 `ceil(本轮段总长 / blank_length)` 折算，这是在缺少赛方工艺转换参数时的可替换默认模型。拿到正式 `constraint.txt` 后，应优先修改 `_batch` 中的钢坯、承重和轮数校验。
