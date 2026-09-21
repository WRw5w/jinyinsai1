"""Conservative grid rectangles for a continuous K/trim/production relaxation."""
import bisect
import json
import math
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from solver import Config,Model,load_orders


def main():
    start=time.monotonic()
    out=Path(__file__).resolve().parent
    previous=json.loads((out/'model_bounds.json').read_text())
    cfg=Config(**json.loads((ROOT/'runs/normal_heterogeneous/config.json').read_text()))
    orders=load_orders(str(ROOT/'data/orders.normalized.csv'),cfg)
    model=Model(orders,cfg)
    fs=[o.linear_weight*o.size for o in orders]
    minimum_f=sum(o.pieces*f for o,f in zip(orders,fs))
    maximum_f=sum(cap*f for cap,f in zip(model.caps,fs))
    specs={}
    for i,o in enumerate(orders):
        key=(o.diameter,o.size,o.density)
        if key in specs:continue
        values=[]
        for p in range(1,model.parallel_limit([i])+1):
            L=min(148,60000/(p*o.linear_weight)-2)
            if L<48:continue
            a=1/p+o.size/(p*L)
            h=2*o.linear_weight*o.size/L
            values.append((a,h))
        specs[key]=values
    # Each nonnegative beta is independently a valid dual certificate. A finite
    # collection remains valid: it need not optimize beta to produce an upper bound.
    betas=[10**(-5+4*j/60) for j in range(61)]
    cells=400
    f_grid=[minimum_f+(maximum_f-minimum_f)*i/cells for i in range(cells+1)]
    certificates=[]
    for beta in betas:
        values={key:min(a+beta*h for a,h in choices) for key,choices in specs.items()}
        base=0
        additions=[]
        for o,cap,f in zip(orders,model.caps,fs):
            m=values[o.diameter,o.size,o.density]
            base+=o.pieces*m
            if cap>o.pieces:
                additions.append((m/f,(cap-o.pieces)*f))
        additions.sort()
        masses=[0.0];costs=[0.0]
        for unit,mass in additions:
            masses.append(masses[-1]+mass)
            costs.append(costs[-1]+mass*unit)
        lower_costs=[]
        for F in f_grid:
            delta=F-minimum_f
            j=min(len(additions)-1,bisect.bisect_right(masses,delta)-1)
            cost=base+costs[j]+(delta-masses[j])*additions[j][0]
            # Relax lower cost by a substantial roundoff guard before dividing by beta.
            lower_costs.append(cost-1e-6)
        certificates.append((beta,lower_costs))
    klow=previous['bounds']['knives_lower_bound']
    yupper=previous['bounds']['discrete_blank_and_size_yield_upper_bound']
    khigh=92000
    best=(-math.inf,None)
    per_k=[]
    for K in range(klow,khigh+1):
        kbest=(-math.inf,None)
        for j in range(cells):
            # C_beta(F) is nondecreasing. For all F in [Flo,Fhi], this lower
            # bound on H is valid, and F/(F+H) <= Fhi/(Fhi+Hlower).
            h_lower=max(0.0,max((costs[j]-K)/beta for beta,costs in certificates))
            yup=min(yupper,f_grid[j+1]/(f_grid[j+1]+h_lower))
            bound=3600000/K+30*yup+30
            if bound>kbest[0]:
                kbest=(bound,{'knives':K,'finished_kg_interval':[f_grid[j],f_grid[j+1]],'trim_lower_bound_kg':h_lower,'yield_upper_bound':yup})
        if kbest[0]>best[0]:best=kbest
        if K in (klow,91000,91100,91200,91300,91400,91500,91600,91700,91800,91900,92000):
            per_k.append({'K':K,'score_upper_bound':kbest[0],'maximizing_relaxed_cell':kbest[1]})
    tail_upper=3600000/(khigh+1)+30*yupper+30
    global_upper=max(best[0],tail_upper)
    results={
        'scope':'Only current local net_shared_trim physical model and inferred capped40/30/20/10 score; not an official bound.',
        'previous_independent_upper_bound':previous['bounds']['score_upper_bound_at_coverage1'],
        'new_coupled_relaxation_upper_bound':global_upper,
        'improvement':previous['bounds']['score_upper_bound_at_coverage1']-global_upper,
        'local_99_excluded_by_relaxation':global_upper<99,
        'computed_grid_maximum_upper_bound':best[0],
        'maximizing_relaxed_cell':best[1],
        'tail_K_above':khigh,
        'tail_score_upper_bound':tail_upper,
        'minimum_finished_kg':minimum_f,'maximum_finished_kg':maximum_f,
        'knife_interval_enumerated':[klow,khigh],'finished_mass_cells':cells,'beta_certificates':betas,
        'method':'For fixed F, minimize K+beta*H in a fractional assignment relaxation: each order chooses p with smallest a_ip+beta*h_ip; required pieces contribute mandatory cost, optional pieces fill by increasing cost per product kg. Then H >= (C_beta(F)-K)/beta. For an F cell, C_beta(Flo) is a valid lower cost and Fhi upper-bounds the yield numerator. Max over rectangles is an upper bound, not sampled feasible performance.',
        'formulas':{'L_p':'min(148,60000/(p*mu)-2)','a_ip':'1/p+s_i/(p*L_p)','h_ip':'2*mu*s_i/L_p','F_i':'mu*s_i*q_i','score':'3600000/K+30*F/(F+H)+30; coverage and time optimistically full'},
        'relaxations':['Order production can be fractional between required pieces and5% cap.','Different orders may independently choose parallel counts without integral round packing.','Uses aggregate round-length-cap lower bounds on cuts and trim.','Blank-rounding losses discarded, but previous discrete yield bound remains enforced.','Integer width lower bound used only in aggregate K>=90947.','Coverage and time allowed to reach100.'],
        'numerics':'Double precision with1e-6 downward guard on each cost certificate; conservative cell inequalities, but no interval-arithmetic formal verification. Do not promote numerical exclusion to a mathematical proof without checking robustness.',
        'per_k_examples':per_k,'elapsed_seconds':time.monotonic()-start,
    }
    (out/'coupled_bounds.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    conclusion='数值松弛界低于99；需要独立复核与严格数值界后才可称数学排除。' if global_upper<99 else '该加强界仍高于99，不能排除本地99，也不能证明它可达。'
    lines=['# 刀数与头尾损失的耦合界','',f'原独立乐观界：**{previous["bounds"]["score_upper_bound_at_coverage1"]:.8f}**；耦合连续松弛界：**{global_upper:.8f}**。', '',conclusion,'','只适用于当前本地模型与推测评分公式，**不是官方评分上界**。', '', '对每个非负β，放宽为订单可分配给不同并列支数、可分数生产、可分数轮次，建立 `K+βH≥Cβ(F)`。固定产量F时，先满足必需支数，再按单位成品重量的成本升序分配额外生产，即得Cβ(F)。', '', '计算对整数刀数逐一枚举，产量划分400个区间；使用区间左端成本作为H下界、右端F作为成材率分子的上界。每个区间得到的是保守上界，不是只在网格点抽样。大刀数尾部用原独立界封住。', '',f'最大放宽单元：K={best[1]["knives"]}，F∈[{best[1]["finished_kg_interval"][0]:.3f},{best[1]["finished_kg_interval"][1]:.3f}]kg，H≥{best[1]["trim_lower_bound_kg"]:.3f}kg。', '', '仍忽略实际轮次装箱、每订单整数段数的更强耦合、整数钢坯余料等限制，因此松弛可明显乐观。浮点计算加入向保守方向的余量，但没有做区间算术形式证明。', '', f'运行时间：{results["elapsed_seconds"]:.3f}秒。完整数值见[coupled_bounds.json](coupled_bounds.json)。']
    (out/'coupled_bounds.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({key:results[key] for key in ('previous_independent_upper_bound','new_coupled_relaxation_upper_bound','improvement','local_99_excluded_by_relaxation','maximizing_relaxed_cell','elapsed_seconds')},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
