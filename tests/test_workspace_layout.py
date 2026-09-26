"""Integration checks for the relocated entry point and evidence paths."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WorkspaceLayoutTests(unittest.TestCase):
    def test_entrypoint_resolves_data_and_evidence_outside_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-X", "utf8", str(ROOT / "aic.py"), "audit-clause6"],
                cwd=directory, capture_output=True, text=True, encoding="utf8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "candidate_not_certified")
        self.assertFalse(report["submission_allowed"])
        self.assertEqual({case["name"]: case["boundary_count"] for case in report["cases"]},
                         {"original_v2": 7030, "rotated_v4": 7342, "official_example": 0})

    def test_relocated_competition_artifacts_keep_original_bytes(self):
        manifest = json.loads((ROOT / "archives/workspace-manifest.json").read_text(encoding="utf8"))
        checked = 0
        for entry in manifest["files"]:
            path = entry["current_path"]
            if path and path.startswith(("artifacts/", "evidence/", "data/", "tests/fixtures/")) \
                    and not path.endswith(".md"):
                with self.subTest(path=path):
                    self.assertEqual(hashlib.sha256((ROOT / path).read_bytes()).hexdigest(), entry["sha256"])
                checked += 1
        self.assertGreater(checked, 30)

    def test_build_defaults_to_artifacts(self):
        from build_submission import parser
        args = parser().parse_args([])
        self.assertEqual(args.output_dir, "artifacts/candidates/submission_fixed")


if __name__ == "__main__":
    unittest.main()
