import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from optimize_patterns import estimated_score
from solver import Config, _key


class PatternTests(unittest.TestCase):
    def test_cpp_dp_matches_python_oracle(self):
        # Exhaust all exact/ranged demands under several material tradeoffs.
        patterns=[(2,1,2,1,2,7),(3,1,3,1,2,11),(4,2,2,1,3,9),(7,1,7,2,2,18)]
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);inp=p/'input';out=p/'output'
            text='1\n0 1 30 4 25\n'
            text+='\n'.join(' '.join(map(str,row)) for row in patterns)+'\n'
            text+=' '.join(f'{q} {q+2}' for q in range(2,27))+'\n'
            inp.write_text(text)
            for lam in [0,0.1,0.5,2]:
                subprocess.run([str(Path('pattern_dp.exe').resolve()),str(inp),str(out),str(lam)],check=True)
                dp=[0]+[float('inf')]*30
                for n in range(1,31):
                    dp[n]=min((dp[n-r[0]]+r[4]+lam*r[5] for r in patterns if r[0]<=n),default=float('inf'))
                lines=out.read_text().splitlines()[2:]
                for line in lines:
                    q,n,*ids=map(int,line.split());rows=[patterns[i] for i in ids]
                    self.assertEqual(n,len(rows))
                    produced=sum(r[0] for r in rows)
                    self.assertTrue(q<=produced<=q+2)
                    self.assertAlmostEqual(sum(r[4]+lam*r[5] for r in rows),min(dp[q:q+3]))

    def test_observed_score_weights(self):
        self.assertAlmostEqual(.4*79.17+.3*86.63+.2*99.98+.1*100,87.653)
        cfg=Config(objective='platform_score',baseline_knives=90000)
        score=-_key((100000,95,100,100),cfg,100)[0]
        self.assertAlmostEqual(score,94.5)
        # Once knife subscore is capped, reduce waste rather than seek more cuts.
        self.assertLess(_key((90000,95,100,100),cfg,100),_key((80000,90,100,100),cfg,100))

    def test_finished_credit_can_choose_extra_pieces(self):
        # Negative material credit is valid in the acyclic quantity DP.
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);inp=p/'input';out=p/'output'
            inp.write_text('1\n0 1 6 2 1\n2 1 2 1 2 -4\n3 1 3 1 2 -8\n4 6\n')
            subprocess.run([str(Path('pattern_dp.exe').resolve()),str(inp),str(out),'1'],check=True)
            q,n,*ids=map(int,out.read_text().splitlines()[2].split())
            self.assertEqual((q,n,ids),(4,2,[1,1]))

    def test_continued_search_improves_accepted_baseline(self):
        from refine_patterns import group_vector, score
        from solver import load_orders,load_blanks,validate_plan
        from platform_check import check
        root=Path('runs/continued_refined')
        if not (root/'result.json').exists():self.skipTest('continued run has not completed')
        cfg=Config(**json.loads((root/'config.json').read_text()))
        orders=load_orders('data/orders.normalized.csv',cfg);blanks=load_blanks('data/blanks.normalized.csv')
        before=json.loads(Path('submission_optimized/初赛结果_棒材优化.json').read_text(encoding='utf-8'))
        after=json.loads((root/'result.json').read_text())
        old=validate_plan(before,orders,cfg,blanks);new=validate_plan(after,orders,cfg,blanks)
        self.assertGreaterEqual(estimated_score(new),estimated_score(old))
        self.assertTrue(check(after)['passed'])
        v=group_vector(after,{o.oid:o for o in orders},{b.bid:b for b in blanks})
        self.assertAlmostEqual(score(v),estimated_score(new),places=8)

    def test_candidate_limits_and_independent_checks(self):
        from platform_check import check
        from solver import load_orders,load_blanks,validate_plan
        for directory in ['runs/pattern_dp_exact','runs/pattern_dp_2pct','runs/pattern_dp_5pct',
                          'runs/pattern_dp_pool_2pct','runs/pattern_dp_pool_5pct',
                          'runs/continued_credit','runs/continued_refined','runs/continued_pairs',
                          'runs/continued_final','runs/continued_triples']:
            root=Path(directory)
            if not (root/'result.json').exists():continue
            cfg=Config(**json.loads((root/'config.json').read_text()))
            orders=load_orders('data/orders.normalized.csv',cfg)
            blanks=load_blanks('data/blanks.normalized.csv')
            plan=json.loads((root/'result.json').read_text())
            validate_plan(plan,orders,cfg,blanks)
            self.assertTrue(check(plan)['passed'])


if __name__=='__main__':
    unittest.main(verbosity=2)
