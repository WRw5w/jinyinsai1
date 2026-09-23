import json
import math
import subprocess
import tempfile
import unittest
from pathlib import Path


class HeterogeneousTests(unittest.TestCase):
    def test_two_order_exact_delivery_and_material_lower_bound(self):
        # Required net mass is 300kg; 180kg billets require at least two.
        # Existing plan consumes three. A feasible two-billet allocation exists.
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);inp=root/'input.txt';out=root/'output.txt'
            inp.write_text('2 1 1\n50 50 2 1 2\n100 100 2 1 2\n180\n1 2 2\n0 1\n1 1 1 0 50\n2 2 1 1 50\n')
            subprocess.run([str(Path('heterogeneous_search.exe').resolve()),str(inp),str(out),'0.2','91','48'],check=True,capture_output=True)
            lines=out.read_text().splitlines();self.assertEqual(lines[:2],['1','2'])
            supplied=[0,0];raw=0
            for line in lines[2:]:
                p,nb,n,*pairs=map(int,line.split());self.assertIn(p,[1,2]);self.assertEqual(len(pairs),2*n)
                length=2
                for i,k in zip(pairs[::2],pairs[1::2]):
                    supplied[i]+=k*p;length+=k*2
                self.assertTrue(50<=length<=150)
                self.assertGreaterEqual(nb*180,length*p)
                raw+=nb*180
            self.assertEqual(supplied,[50,100]);self.assertEqual(raw,360)

    def test_full_dataset_candidate_independent_validation(self):
        from platform_check import check
        from solver import Config,load_orders,load_blanks,validate_plan
        root=Path('runs/normal_heterogeneous')
        if not (root/'result.json').exists():self.skipTest('normal search not completed')
        cfg=Config(**json.loads((root/'config.json').read_text()))
        plan=json.loads((root/'result.json').read_text())
        metrics=validate_plan(plan,load_orders('data/orders.normalized.csv',cfg),cfg,load_blanks('data/blanks.normalized.csv'))
        self.assertTrue(check(plan)['passed'])
        report=json.loads((root/'report.json').read_text())
        self.assertGreaterEqual(metrics['platform_score_estimate'],report['before']['platform_score_estimate']-1e-8)


if __name__=='__main__':unittest.main(verbosity=2)
