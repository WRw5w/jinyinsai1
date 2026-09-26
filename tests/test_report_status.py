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
