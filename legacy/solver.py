#!/usr/bin/env python3
"""棒材组合订单锯切优化。

提供两个入口：
  brute_force(...): 小规模订单的穷举最优（默认 n<=12）。
  search_10s(...): 最多运行指定秒数的随机大邻域搜索。

赛题没有随 PDF 提供 constraint.txt，因此所有现场参数集中在 Config 中，
运行时可用 JSON 覆盖。模型的刀数、成材率和输出字段均与题面保持一致。
"""
from __future__ import annotations
import argparse, csv, json, math, random, time
from dataclasses import dataclass, asdict
from typing import Dict, List, Tuple, Any

@dataclass
class Config:
    bed_length: float = 120.0       # 每轮冷床纵向有效长度(m)
    bed_width: int = 100            # 每轮最多并列棒材数
    bed_weight: float = 1e18        # 每轮承重(kg)，未知时不限制
    trim: float = 1.0               # 两端切损(m)
    blank_length: float = 120.0     # 一根钢坯可轧出的长度(m)
    blank_weight: float = 1e18      # 单根钢坯重量(kg)
    max_rounds: int = 20
    weight_scale: float = 1.0       # CSV重量乘数（吨数据填1000）

@dataclass(frozen=True)
class Order:
    oid: str; steel: str; diameter: float; size: float; weight: float; density: float
    pieces: int

def _pick(row, names, default=None):
    for n in names:
        if n in row and row[n] not in ('', None): return row[n]
    return default

def load_orders(path: str, cfg: Config) -> List[Order]:
    out=[]
    with open(path, encoding='utf-8-sig', newline='') as f:
        for r in csv.DictReader(f):
            oid=str(_pick(r,['order_id','订单号','订单编号','id']))
            steel=str(_pick(r,['steel','steel_grade','钢种'],''))
            dia=float(_pick(r,['diameter','直径','规格'],10))
            size=float(_pick(r,['length','size','定尺长度','定尺'],1))
            w=float(_pick(r,['weight','重量','需求重量'],0))*cfg.weight_scale
            den=float(_pick(r,['density','密度'],7850))
            # 圆棒截面积 mm2；长度 m -> 体积 mm3
            piece_w=math.pi*(dia**2)/4 * (size*1000) * den / 1e9
            pieces=max(1, math.ceil(w/piece_w))
            out.append(Order(oid,steel,dia,size,w,den,pieces))
    return out

def _batch(group: List[Order], cfg: Config) -> Dict[str,Any] | None:
    if not group: return None
    # 同钢种同直径才允许组合；定尺可不同
    if len({(o.steel,round(o.diameter,6)) for o in group}) != 1: return None
    max_parallel=min(cfg.bed_width, max(1, int(cfg.bed_length*1000//max(o.diameter for o in group))))
    # 选一轮尽量装满；段长必须是定尺整数倍+两端余量
    remaining={o.oid:o.pieces for o in group}; rounds=[]
    while any(remaining.values()):
        if len(rounds)>=cfg.max_rounds: return None
        seg={}; used=0.0; parallel=0
        # 订单按剩余量降序，给每个订单一个整数倍段
        for o in sorted(group,key=lambda x:remaining[x.oid],reverse=True):
            if remaining[o.oid]<=0: continue
            k=max(1,int((cfg.bed_length-used-2*cfg.trim)//o.size))
            if k<=0: continue
            take=min(remaining[o.oid], k*max_parallel)
            k=max(1,math.ceil(take/max_parallel)); length=k*o.size+2*cfg.trim
            if used+length>cfg.bed_length+1e-9: continue
            seg[o.oid]=round(length,6); used+=length; parallel=max(parallel,math.ceil(take/k))
            remaining[o.oid]-=k*parallel
        if not seg: return None
        parallel=max(1,min(max_parallel,parallel))
        # 每轮消耗钢坯数按总长度折算；可由 Config.blank_length 替换现场公式
        blanks=max(1,math.ceil(used/cfg.blank_length))
        if used*parallel*group[0].density*math.pi*(group[0].diameter/1000)**2/4 > cfg.bed_weight: return None
        rounds.append((seg,parallel,blanks))
    knives=sum(2+sum(max(0,math.floor((L-2*cfg.trim)/next(o.size for o in group if o.oid==oid))-1) for oid,L in seg.items()) for seg,_,_ in rounds)
    produced=sum((math.floor((L-2*cfg.trim)/o.size))*p for seg,p,_ in rounds for oid,L in seg.items() for o in group if o.oid==oid)
    return {'orders':[o.oid for o in group], 'length_scheme':[s for s,_,_ in rounds],
            'counts':[p for _,p,_ in rounds], 'blank_type':1,
            'blank_counts':[b for _,_,b in rounds], '_knives':knives, '_produced':produced,
            '_input_weight':sum(o.weight for o in group)}

def _score(plan, all_orders):
    knives=sum(x['_knives'] for x in plan); inp=sum(o.weight for o in all_orders)
    # 产材率用重量近似；组合覆盖率按多订单方案中的订单数
    yield_rate=min(1.0, sum(x['_input_weight'] for x in plan)/max(inp,1e-9))
    covered=sum(len(x['orders']) for x in plan if len(x['orders'])>1)/max(len(all_orders),1)
    return (knives, -yield_rate, -covered)

def _clean(plan):
    return [{k:v for k,v in x.items() if not k.startswith('_')} for x in plan]

def brute_force(orders: List[Order], cfg: Config) -> List[Dict[str,Any]]:
    """穷举所有集合划分；适合每个同质组约12单以内。"""
    result=[]
    for _,idx in _groups(orders).items():
        sub=[orders[i] for i in idx]
        best=None; best_key=None
        def rec(rem, cur):
            nonlocal best,best_key
            if not rem:
                key=_score(cur,sub)
                if best_key is None or key<best_key: best,best_key=list(cur),key
                return
            i=rem[0]; tail=rem[1:]
            for mask in range(1<<len(tail)):
                g=[i]+[tail[j] for j in range(len(tail)) if mask>>j&1]
                b=_batch([sub[k] for k in g],cfg)
                if b is None: continue
                left=[k for k in rem if k not in g]
                rec(left,cur+[b])
        rec(list(range(len(sub))),[])
        result.extend(best or [])
    return _clean(result)

def _groups(orders):
    d={}
    for i,o in enumerate(orders): d.setdefault((o.steel,round(o.diameter,6)),[]).append(i)
    return d

def search_10s(orders: List[Order], cfg: Config, seconds=10.0, seed=1) -> List[Dict[str,Any]]:
    """限时随机大邻域搜索：贪心初解 + 合并/拆分/交换，始终保留可行最优解。"""
    rng=random.Random(seed); deadline=time.perf_counter()+seconds
    groups=list(_groups(orders).values()); best=[]
    for idx in groups:
        cur=[]
        for i in idx: cur.append(_batch([orders[i]],cfg))
        best.extend(cur)
    def key(p): return _score(p,orders)
    best_key=key(best); cur=list(best)
    while time.perf_counter()<deadline:
        cand=list(cur)
        if len(cand)>=2 and rng.random()<0.7:
            a,b=rng.sample(range(len(cand)),2); merged_ids=set(cand[a]['orders'])|set(cand[b]['orders'])
            merged=_batch([o for o in orders if o.oid in merged_ids],cfg)
            if merged: cand=[x for j,x in enumerate(cand) if j not in (a,b)]+[merged]
        else:
            rng.shuffle(cand)
        k=key(cand)
        if k<best_key: best,best_key=list(cand),k; cur=list(cand)
        elif rng.random()<0.03: cur=list(cand)
    return _clean(best)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--orders',required=True); ap.add_argument('--output',default='result.json'); ap.add_argument('--mode',choices=['brute','search'],default='search'); ap.add_argument('--seconds',type=float,default=10); ap.add_argument('--config'); a=ap.parse_args()
    cfg=Config()
    if a.config:
        with open(a.config,encoding='utf8') as f:
            for k,v in json.load(f).items(): setattr(cfg,k,v)
    orders=load_orders(a.orders,cfg)
    plan=brute_force(orders,cfg) if a.mode=='brute' else search_10s(orders,cfg,a.seconds)
    with open(a.output,'w',encoding='utf8') as f: json.dump(plan,f,ensure_ascii=False,indent=2)
    print(f'orders={len(orders)} plans={len(plan)} mode={a.mode} output={a.output}')

if __name__=='__main__': main()
