import itertools
import math
import unittest

from normal_group_search import exact_two_split, multi_split


class SplitTests(unittest.TestCase):
    def test_subset_split_matches_exhaustive_billet_minimum(self):
        for sizes, counts in [([6,7.01],[15,13]),([4.525,6.335,9.0],[10,8,5]),([5,6,7],[9,10,11])]:
            for p in [12,25,35]:
                linear=17.5;blank=9613.5;low=48;cap=130;trim=2
                total=sum(k*s for k,s in zip(counts,sizes))
                options=[]
                for left in itertools.product(*(range(n+1) for n in counts)):
                    L=sum(k*s for k,s in zip(left,sizes))
                    if low-1e-8<=L<=cap+1e-8 and low-1e-8<=total-L<=cap+1e-8:
                        options.append(math.ceil((L+trim)*p*linear/blank-1e-10)+math.ceil((total-L+trim)*p*linear/blank-1e-10))
                result=exact_two_split(counts,sizes,p,linear,blank,low,cap,trim)
                self.assertEqual(result is None,not options)
                if result is not None:
                    L=sum(k*s for k,s in zip(result,sizes))
                    score=math.ceil((L+trim)*p*linear/blank-1e-10)+math.ceil((total-L+trim)*p*linear/blank-1e-10)
                    self.assertEqual(score,min(options))

    def test_no_silent_length_rounding(self):
        self.assertIsNone(exact_two_split([30],[5.12345],20,10,9613.5,48,148,2))

    def test_multi_split_conserves_pieces_and_length_bounds(self):
        for counts,sizes,nr in [([100],[5.0],4),([40,40],[4.525,6.335],4),([80,70],[4.5,6.0],6)]:
            parts=multi_split(counts,sizes,10,10,9613.5,48,148,2,nr)
            self.assertIsNotNone(parts)
            self.assertEqual([sum(row[i] for row in parts) for i in range(len(counts))],counts)
            self.assertEqual(len(parts),nr)
            for row in parts:
                length=sum(k*s for k,s in zip(row,sizes))
                self.assertGreaterEqual(length,48-1e-8)
                self.assertLessEqual(length,148+1e-8)


if __name__=='__main__':unittest.main()
