"""The trajectory driver: the stage list it builds, and when a stage stops.

The three search flags do not commute.  The six orders of
`--requant-cuts/--recount-rounds/--split-schemes` land on fixed points 0.017 points
apart, and the winner (Q -> S -> R, recount last) still buys ~4.6 t of declared mass
when the whole trajectory is re-entered on its own output -- so the order string is
not a convenience, it is the search.  `tools/analysis/trajectory.py` drives that on
purpose, and two things have to hold: the stage list it builds for an order string,
and the rule that ends a stage (a stage repeats while it still buys something, is
never retried after it refuses its input, and the run as a whole may not end below
the plan it was handed).
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    'trajectory', ROOT / 'tools/analysis/trajectory.py')
traj = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(traj)

# A stand-in for a pass: 'keep' refuses its input, 'seq' copies it through and reports
# the next delta from STUB_DELTAS (the last one repeats), 'nod' reports no delta at all
# -- which is what `repack_batches` and `share_orders` do.
STUB = '''\
import json, os, shutil, sys
from pathlib import Path
log = Path(os.environ['STUB_LOG'])
n = int(log.read_text()) if log.exists() else 0
log.write_text(str(n + 1))
if os.environ['STUB_MODE'] == 'keep':
    print('stub: the rewrite is not an improvement: keep the input', file=sys.stderr)
    raise SystemExit(2)
argv = sys.argv[1:]
out = Path(argv[argv.index('--output') + 1])
if Path(argv[0]).resolve() != out.resolve():
    shutil.copyfile(argv[0], out)
if os.environ['STUB_MODE'] != 'nod':
    deltas = [float(v) for v in os.environ['STUB_DELTAS'].split(',')]
    Path(argv[argv.index('--stats') + 1]).write_text(
        json.dumps({'cost_delta_kg_equivalent': deltas[min(n, len(deltas) - 1)]}))
'''


class StageListTests(unittest.TestCase):
    """`--order QSR` is the winning recipe; its stage list must be exactly this."""

    def test_the_winning_order_expands_to_its_prefixes_then_the_cascade(self):
        # the three search stages are cumulative prefixes of "QSR", so the last one
        # spells the flags in that order -- argparse reads the same set as ALL_FLAGS
        stages = traj.stages_of('QSR', cascade=True, lead=False)
        self.assertEqual([tool for tool, _ in stages],
                         [traj.SHIFT, traj.SHIFT, traj.SHIFT, traj.REPACK,
                          traj.SHIFT, traj.SHARE, traj.SHIFT])
        self.assertEqual([flags for _, flags in stages], [
            [*traj.LEAD_FLAGS, '--requant-cuts'],
            [*traj.LEAD_FLAGS, '--requant-cuts', '--split-schemes'],
            [*traj.LEAD_FLAGS, '--requant-cuts', '--split-schemes', '--recount-rounds'],
            [],
            list(traj.ALL_FLAGS),
            [],
            list(traj.ALL_FLAGS),
        ])

    def test_a_lead_stage_is_prepended_and_the_cascade_can_be_dropped(self):
        self.assertEqual(traj.stages_of('QSR', cascade=False, lead=True), [
            (traj.SHIFT, list(traj.LEAD_FLAGS)),
            (traj.SHIFT, [*traj.LEAD_FLAGS, '--requant-cuts']),
            (traj.SHIFT, [*traj.LEAD_FLAGS, '--requant-cuts', '--split-schemes']),
            (traj.SHIFT, [*traj.LEAD_FLAGS, '--requant-cuts', '--split-schemes',
                          '--recount-rounds']),
        ])

    def test_the_order_shows_in_each_prefix_and_every_flag_is_reached(self):
        for order in ('QSR', 'RQS', 'SRQ'):
            stages = traj.stages_of(order, cascade=False, lead=False)
            self.assertEqual(len(stages), 3, order)
            self.assertEqual(sorted(stages[-1][1]), sorted(traj.ALL_FLAGS), order)
            self.assertEqual(stages[0][1][-1], traj.FLAG[order[0]], order)
            for (_, before), (_, after) in zip(stages, stages[1:]):
                self.assertEqual(after[:-1], before, order)
                self.assertEqual(len(after) - len(before), 1, order)


class ArgumentTests(unittest.TestCase):
    """Refusals that must happen before anything is read or run."""

    def run_main(self, *argv):
        with mock.patch.object(sys, 'argv', ['trajectory.py', *argv]):
            traj.main()

    def test_a_repeated_or_unknown_letter_is_refused(self):
        for order in ('QQS', 'QSX', ''):
            with self.assertRaises(SystemExit) as caught:
                self.run_main('plan.json', '--order', order, '--output', 'out.json')
            self.assertIn('--order', str(caught.exception))

    def test_zero_passes_is_refused(self):
        with self.assertRaises(SystemExit) as caught:
            self.run_main('plan.json', '--order', 'Q', '--passes', '0', '--output', 'out.json')
        self.assertIn('--passes', str(caught.exception))


class StageRunnerTests(unittest.TestCase):
    """A stage's own stopping rule, with the scoring stubbed out."""

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix='trajectory_test_'))
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)
        self.tool = self.work / 'stub.py'
        self.tool.write_text(STUB, encoding='utf-8')
        self.log = self.work / 'calls.txt'
        self.plan = self.work / 'plan.json'
        self.plan.write_text('{"batches": []}', encoding='utf-8')
        patcher = mock.patch.object(traj, 'read_plan', lambda path: str(path))
        patcher.start()
        self.addCleanup(patcher.stop)

    def stage(self, mode, deltas='0.0', max_reps=6):
        records = []
        with mock.patch.dict(os.environ, {'STUB_MODE': mode, 'STUB_LOG': str(self.log),
                                          'STUB_DELTAS': deltas}):
            src = traj.run_stage(self.tool, [], self.plan, self.work / 'out.json',
                                 self.work / 'out.stats.json', 'data/semi', 'semi', max_reps,
                                 'stub', records)
        return src, records

    def test_a_refused_stage_is_kept_and_never_retried_or_scored(self):
        with mock.patch.object(traj, 'score_of',
                               side_effect=AssertionError('a kept stage must not be scored')):
            src, records = self.stage('keep')
        self.assertEqual(src, self.plan, 'the input is passed on untouched')
        self.assertEqual(self.log.read_text(encoding='utf-8'), '1')
        self.assertEqual(records[0]['outcome'], 'kept its input')
        self.assertEqual(records[0]['reps'], 1)
        self.assertEqual(records[0]['delta_kg_equivalent'], 0.0)

    def test_a_reported_zero_delta_is_the_fixed_point_and_stops_at_once(self):
        with mock.patch.object(traj, 'score_of',
                               return_value=(184504, 99.98, 92.1572)) as score:
            src, records = self.stage('seq', deltas='0.0')
        self.assertEqual(src, self.work / 'out.json')
        self.assertEqual(self.log.read_text(encoding='utf-8'), '1')
        self.assertEqual(records[0]['outcome'], 'fixed point')
        self.assertEqual(records[0]['reps'], 1)
        self.assertEqual(score.call_count, 1)

    def test_deltas_accumulate_while_the_stage_keeps_buying(self):
        with mock.patch.object(traj, 'score_of', return_value=(184504, 99.98, 92.15)):
            src, records = self.stage('seq', deltas='-1000.0,-2000.0,0.0')
        self.assertEqual(self.log.read_text(encoding='utf-8'), '3')
        self.assertEqual(records[0]['outcome'], 'fixed point')
        self.assertEqual(records[0]['reps'], 3)
        self.assertEqual(records[0]['delta_kg_equivalent'], -3000.0)

    def test_a_pass_that_reports_no_delta_stops_when_its_score_goes_flat(self):
        with mock.patch.object(traj, 'score_of',
                               side_effect=[(184515, 99.98, 92.14), (184515, 99.98, 92.14)]):
            src, records = self.stage('nod')
        self.assertEqual(self.log.read_text(encoding='utf-8'), '2')
        self.assertEqual(records[0]['outcome'], 'no further gain')
        self.assertEqual(records[0]['delta_kg_equivalent'], 0.0)

    def test_a_stage_that_keeps_buying_is_cut_off_at_max_reps(self):
        with mock.patch.object(traj, 'score_of',
                               side_effect=[(184500, 99.98, 92.0 + i / 10) for i in range(3)]):
            src, records = self.stage('nod', max_reps=3)
        self.assertEqual(self.log.read_text(encoding='utf-8'), '3')
        self.assertEqual(records[0]['outcome'], 'stopped at 3 reps')
        self.assertEqual(records[0]['reps'], 3)


class BelowInputGuardTests(unittest.TestCase):
    """The one thing the driver must never do: hand back a plan worth less."""

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix='trajectory_test_'))
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)
        self.source = self.work / 'in.json'
        self.source.write_text('{"batches": []}', encoding='utf-8')
        self.output = self.work / 'out.json'

    def run_main(self, final_score, *extra):
        def fake_run_stage(tool, flags, src, out, *rest):
            shutil.copyfile(self.source, out)
            rest[-1].append({'label': 'stub'})
            return out
        with mock.patch.object(traj, 'read_plan', lambda path: str(path)), \
                mock.patch.object(traj, 'score_of',
                                  side_effect=[(184504, 99.98, 92.0),
                                               (184504, 99.98, final_score)]), \
                mock.patch.object(traj, 'run_stage', fake_run_stage), \
                mock.patch.object(sys, 'argv',
                                  ['trajectory.py', str(self.source), '--order', 'QSR',
                                   '--output', str(self.output), *extra]):
            traj.main()

    def test_a_run_that_ends_below_its_input_is_refused_and_the_output_removed(self):
        with self.assertRaises(SystemExit) as caught:
            self.run_main(91.0)
        self.assertIn('below its input', str(caught.exception))
        self.assertFalse(self.output.exists(), 'the losing plan must not be left on disk')

    def test_a_run_that_holds_its_ground_writes_the_plan_and_its_stats(self):
        stats = self.work / 'stats.json'
        self.run_main(92.5, '--stats', str(stats))
        self.assertTrue(self.output.is_file())
        report = json.loads(stats.read_text(encoding='utf-8'))
        self.assertEqual(report['order'], 'QSR')
        self.assertEqual(report['final']['path'], str(self.output))
        self.assertEqual(report['final']['score'], 92.5)
        self.assertEqual(len(report['stages']),
                         len(traj.stages_of('QSR', cascade=True, lead=False)))


class WorkingTreeTests(unittest.TestCase):
    """The real passes, when the (gitignored) run artifacts are still around."""

    def setUp(self):
        self.data = ROOT / 'data/semi'
        if not self.data.is_dir():
            self.skipTest('data/semi missing')
        self.work = Path(tempfile.mkdtemp(prefix='trajectory_test_'))
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)

    def run_one_stage(self, source, tool, flags, name):
        if not source.is_file():
            self.skipTest(f'{source.name} missing (gitignored tree)')
        records = []
        out = self.work / f'{name}.json'
        src = traj.run_stage(tool, flags, source, out, self.work / f'{name}.stats.json',
                             str(self.data), 'semi', 1, name, records)
        return src, out, records[0]

    def test_the_winning_first_stage_still_buys_on_the_seed_it_was_found_on(self):
        # _iter11 + [M, X, Q] is _rq1/_rq2 in the docs: the pass takes 91.80 to 91.99
        src, out, record = self.run_one_stage(
            ROOT / 'runs/_iter11.json', traj.SHIFT,
            [*traj.LEAD_FLAGS, traj.FLAG['Q']], 'q')
        self.assertEqual(src, out)
        self.assertNotEqual(record['outcome'], 'kept its input')
        self.assertLess(record['delta_kg_equivalent'], 0.0,
                        'an accepted stage must have bought something')

    def test_the_machines_fixed_point_refuses_every_flag_at_once(self):
        # _cy1 is the two-level fixed point: the whole flag set, re-run on it, moves
        # nothing and reports a delta of exactly zero (the stop every trajectory uses)
        src, out, record = self.run_one_stage(
            ROOT / 'runs/_cy1.json', traj.SHIFT, list(traj.ALL_FLAGS), 'all')
        self.assertEqual(src, out)
        self.assertEqual(record['outcome'], 'fixed point')
        self.assertEqual(record['delta_kg_equivalent'], 0.0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
