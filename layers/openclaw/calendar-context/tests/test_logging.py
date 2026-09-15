"""Calendar content must not reach a log, and there must be only one door.

The plan's rule is: log ids, counts, durations, and error classes only. A rule
like that decays the moment someone adds a convenient f-string, so it is tested
two ways -- the helper refuses unknown fields, and no other module in the
package is allowed to import `logging` at all.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

from calctx import logs

PKG = Path(__file__).resolve().parent.parent / "calctx"


class LoggingWhitelistTest(unittest.TestCase):
    def test_allowed_fields_render(self):
        line = logs.log("sync_done", kind="initial", added=3, elapsed_ms=120)
        self.assertEqual(line, "sync_done added=3 elapsed_ms=120 kind=initial")

    def test_a_meeting_title_cannot_be_logged(self):
        for field in ("summary", "location", "attendees", "title", "description",
                      "calendar_id", "email"):
            with self.subTest(field=field):
                with self.assertRaises(logs.ForbiddenLogField):
                    logs.log("oops", **{field: "Dinner with Dr. Silva"})

    def test_the_raw_calendar_id_is_not_a_loggable_field(self):
        """The primary calendar's id is the user's email address."""
        self.assertNotIn("calendar_id", logs.ALLOWED_FIELDS)
        self.assertIn("calendar_ref", logs.ALLOWED_FIELDS)

    def test_calendar_ref_is_stable_and_not_reversible(self):
        ref = logs.calendar_ref("caio@example.invalid")
        self.assertEqual(ref, logs.calendar_ref("caio@example.invalid"))
        self.assertNotIn("caio", ref)
        self.assertNotIn("@", ref)
        self.assertTrue(ref.startswith("cal:"))

    def test_the_error_message_names_the_offending_field(self):
        with self.assertRaises(logs.ForbiddenLogField) as cm:
            logs.log("oops", summary="x", added=1)
        self.assertIn("summary", str(cm.exception))
        self.assertNotIn("added", str(cm.exception))


class SingleLoggingDoorTest(unittest.TestCase):
    def test_no_other_module_imports_logging(self):
        offenders = []
        for path in sorted(PKG.rglob("*.py")):
            if path.name == "logs.py":
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    offenders += [f"{path.name}: import {a.name}" for a in node.names
                                  if a.name.split(".")[0] == "logging"]
                elif isinstance(node, ast.ImportFrom):
                    if (node.module or "").split(".")[0] == "logging":
                        offenders.append(f"{path.name}: from {node.module} import ...")
        self.assertEqual(offenders, [],
                         "logging must go through calctx.logs:\n" + "\n".join(offenders))

    def test_no_module_prints_or_formats_calendar_text(self):
        """print() belongs to the two operator CLIs, nowhere else. A print in a
        library module is how a meeting title reaches a journal."""
        allowed = {"syncrun.py", "authorize.py"}
        offenders = []
        for path in sorted(PKG.rglob("*.py")):
            if path.name in allowed:
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "print"):
                    offenders.append(f"{path.name}:{node.lineno}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
