"""Independent semantic candidates for reconstructing reported platform material yield."""
import csv, json, math, statistics, hashlib
from pathlib import Path
from collections import defaultdict

ROOT=Path(__file__).resolve().parent.parent
ORDERS={}
with (ROOT/'data/orders_quarter.csv').open(encoding='utf-8-sig',newline='') as f:
    for r in csv.DictReader(f):
        d=float(r['订单直径(mm)']); s=float(r['订单定尺(mm)'])/1000
        if s>0: ORDERS[r['订单号']]=dict(d=d,s=s,mu=math.pi*(d/1000)**2/4*9860,w=float(r['订单重量(t)'])*1000)
B={1:9613.5,2:6162.5}
CASES={'submission_fixed':(113683,86.63),'submission_optimized':(92913,92.51),'submission_normal_985':(119268,96.71)}

def analyze(path):
    plan=json.loads((path/'初赛结果_棒材优化.json').read_text(encoding='utf-8-sig'))
    totals=defaultdict(float); ratios=defaultdict(list); produced=defaultdict(float)
    for batch in plan:
        bn=br=0
        o0=ORDERS[batch['orders'][0]]
        for scheme,p,nb in zip(batch['length_scheme'],batch['counts'],batch['blank_counts']):
            raw=nb*B[batch['blank_type']]; net=sum(scheme.values()); mu=o0['mu']
            totals['raw']+=raw; br+=raw; rn=0
            for oid,L in scheme.items():
                o=ORDERS[oid]; s=o['s']; k=round(L/s); mass=L*p*mu
                totals['net']+=mass; rn+=mass; bn+=mass; produced[oid]+=mass
                totals['floor_pieces']+=int(L/s)*s*p*mu
                totals['floor_div_pieces']+=int(L//s)*s*p*mu
                totals['floor_length']+=int(L)*p*mu
                totals['floor_piece_weight']+=k*p*math.floor(s*mu)
                totals['round_piece_weight']+=k*p*round(s*mu)
                totals['floor_linear_weight']+=L*p*int(mu)
                totals['round_linear_weight']+=L*p*round(mu)
                totals['floor_diameter']+=mass*(int(o['d'])/o['d'])**2
                totals['round_diameter']+=mass*(round(o['d'])/o['d'])**2
                totals['floor_segment_mass']+=math.floor(L*mu)*p
                totals['round_segment_mass']+=round(L*mu)*p
                totals['segment_trim1']+=(L-1)*p*mu
                totals['segment_trim2']+=(L-2)*p*mu
                totals['floor_K']+=int(L/s)
                totals['round_K']+=round(L/s)
            totals['round_trim1']+=(net-1)*p*mu
            totals['round_trim2']+=(net-2)*p*mu
            totals['physical']+=(net+2)*p*mu
            totals['rounds']+=1
            ratios['row_mean'].append(rn/raw)
        ratios['batch_mean'].append(bn/br)
        ratios['batch_mean_orders'].extend([bn/br]*len(batch['orders']))
    totals['capped_order_mass']=sum(min(w,ORDERS[oid]['w']) for oid,w in produced.items())
    totals['capped_rounded_demand']=sum(min(w,math.ceil(ORDERS[oid]['w']/(ORDERS[oid]['s']*ORDERS[oid]['mu']))*ORDERS[oid]['s']*ORDERS[oid]['mu']) for oid,w in produced.items())
    out={k:100*v/totals['raw'] for k,v in totals.items() if k not in ('raw','floor_K','round_K','rounds')}
    out.update({k:100*statistics.mean(v) for k,v in ratios.items()})
    out['K_floor_plusround']=totals['floor_K']+totals['rounds']
    out['K_round_plusround']=totals['round_K']+totals['rounds']
    out['physical_total']=totals['physical']; out['raw_total']=totals['raw'];out['net_total']=totals['net']
    return out

def historical_overweight():
    path=ROOT/'legacy/platform_before_fix/初赛结果_棒材优化.json'
    plan=json.loads(path.read_text(encoding='utf-8-sig'))
    counts={False:0,True:0}
    for batch in plan:
        d=ORDERS[batch['orders'][0]]['d']
        for trunc in counts:
            mu=math.pi*((int(d) if trunc else d)/1000)**2/4*9860
            for scheme,p in zip(batch['length_scheme'],batch['counts']):
                if (sum(scheme.values())+2)*p*mu>60000+1e-7:
                    counts[trunc]+=1
    return dict(original_diameter=counts[False],integer_diameter=counts[True],official=395,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())

if __name__=='__main__':
    allresults={name:analyze(ROOT/name) for name in CASES}
    for key in next(iter(allresults.values())):
        print(key, *(round(row[key],6) for row in allresults.values()))
    results=dict(
        conclusion='Integer truncation of raw diameter in mm explains all three rounded yields and historical overweight count.',
        formula='sum(net_length_m * parallel * pi * (int(diameter_mm)/1000)**2 / 4 * 9860) / sum(blank_count * blank_mass_kg)',
        fit_parameters=[],
        material_accounting='Net lengths are counted directly, not floored into delivered pieces; blank masses are 9613.5 and 6162.5 kg.',
        official_comparisons={name:dict(official_yield_percent=CASES[name][1],
            predicted_yield_percent=vals['floor_diameter'],predicted_rounded=round(vals['floor_diameter'],2),
            matches=round(vals['floor_diameter'],2)==CASES[name][1],
            sha256=hashlib.sha256((ROOT/name/'初赛结果_棒材优化.json').read_bytes()).hexdigest()) for name,vals in allresults.items()},
        historical_overweight=historical_overweight(),
        caution='Reverse engineered from outputs, not official evaluator source. Preserve raw diameters for conservative physical feasibility.',
        candidate_diagnostics=allresults)
    assert all(v['matches'] for v in results['official_comparisons'].values())
    assert results['historical_overweight']['integer_diameter']==395
    (ROOT/'diagnostics/yield_semantics.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
