import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('resync_reports', ROOT / 'tools/resync_reports.py')
resync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resync)


class ReportStatusTests(unittest.TestCase):
    def test_resync_cannot_reapprove_a_legacy_model_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory)
            with zipfile.ZipFile(package / 'result.zip', 'w') as archive:
                archive.writestr('result.json', '[]')
            legacy = package / 'validation_report_legacy_B.json'
            legacy.write_bytes(b'{"passed": true}\r\n')
            (package / 'validation_report_source.json').write_text('{}')
            with patch.object(resync, 'platform_check', return_value={'passed': True}), \
                    patch.object(resync, 'platform_evaluate', return_value={'violations': {}}):
                result = resync.resync(package, ROOT / 'data/semi')
            self.assertFalse(result['passed'])
            self.assertFalse(result['platform_check_passed'])
            self.assertFalse(result['submission_allowed'])
            self.assertIsNone(result['violation_count'])
            self.assertTrue(result['legacy_model_result']['platform_check_passed'])
            self.assertEqual(legacy.read_bytes(), b'{"passed": true}\r\n')
            self.assertEqual(json.loads((package / 'validation_report_source.json').read_bytes()), result)


class BuildReportStatusTests(unittest.TestCase):
    """`build_submission` writes its own report; it must be gated the same way.

    The resync path above was already covered.  The build path was not, and it is
    the one that runs when a package is actually produced -- a fresh build used to
    emit `independent_platform_check.passed: True` for a package the platform scores
    at zero.
    """

    def test_build_report_cannot_approve_itself(self):
        from validation_status import uncertified_build_report

        report = {
            'package': 'submission_x',
            'package_created': True,
            'official_acceptance_verified': False,
            'independent_platform_check': {'passed': True, 'error_counts': {}},
            'calibrated_prediction': {'score_capped': 95.087},
            'combination_coverage_over_source': 0.9999,
        }
        out = uncertified_build_report(report)

        self.assertEqual(out['status'], 'unverified')
        self.assertFalse(out['passed'])
        self.assertFalse(out['platform_check_passed'])
        self.assertFalse(out['submission_allowed'])
        self.assertIsNone(out['violation_count'])
        # The model's verdict survives, but only under the legacy key.
        self.assertTrue(out['legacy_model_result']['independent_platform_check']['passed'])
        self.assertNotIn('independent_platform_check', out)
        # Fields the 提交说明 template still reads must not be dropped.
        self.assertEqual(out['combination_coverage_over_source'], 0.9999)
        self.assertEqual(out['package'], 'submission_x')

    def test_build_submission_routes_its_report_through_the_gate(self):
        """The failure mode is a raw write coming back, so pin the call site."""
        source = (ROOT / 'src/build_submission.py').read_text(encoding='utf-8')
        self.assertIn('uncertified_build_report(report)', source,
                      'build_submission must route its report through the downgrade')
        gate = source.index('uncertified_build_report(report)')
        write = source.index("atomic_json(output / 'validation_report.json'", gate)
        self.assertLess(gate, write, 'the downgrade must run before the write')
