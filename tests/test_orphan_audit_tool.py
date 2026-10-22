from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_orphans.py"
SPEC = importlib.util.spec_from_file_location("audit_orphans", SCRIPT)
assert SPEC and SPEC.loader
AUDITOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDITOR)


class OrphanAuditToolTests(unittest.TestCase):
    def test_relative_import_and_main_guard_are_not_false_orphans(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "sample_pkg"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "used.py").write_text("VALUE = 1\n", encoding="utf-8")
            (package / "caller.py").write_text("from . import used\n", encoding="utf-8")
            (package / "standalone.py").write_text(
                '"""The strings __name__ and __main__ in this docstring are not a guard."""\n'
                'if __name__ == "__main__":\n    print("run")\n',
                encoding="utf-8",
            )
            (package / "orphan_candidate.py").write_text("VALUE = 2\n", encoding="utf-8")

            report = AUDITOR.audit(root)
            candidates = {item["path"] for item in report["python_unreferenced_candidates"]}

            self.assertNotIn("sample_pkg/used.py", candidates)
            self.assertNotIn("sample_pkg/standalone.py", candidates)
            self.assertIn("sample_pkg/orphan_candidate.py", candidates)
            self.assertEqual(report["python_parse_issues"], [])

    def test_command_based_optional_dependency_is_not_called_unused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "requirements-separation.txt").write_text(
                "audio-separator[cpu]>=0.30,<1\n", encoding="utf-8"
            )
            (root / "run_separation.py").write_text(
                "import shutil\n"
                'import subprocess\n'
                'tool = shutil.which("audio-separator")\n'
                "subprocess.run([tool])\n",
                encoding="utf-8",
            )

            report = AUDITOR.audit(root)
            unused = {item["distribution"] for item in report["possible_unused_dependencies"]}
            self.assertNotIn("audio-separator", unused)


if __name__ == "__main__":
    unittest.main()
