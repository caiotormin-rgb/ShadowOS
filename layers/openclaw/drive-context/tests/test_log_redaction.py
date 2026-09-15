"""Logs carry ids, counts, durations and error classes. Nothing else.

A file name, a folder name, an owner, an address or a link in the journal is a
copy of the index in a place with different permissions, a different retention
period, and no encryption story at all. The plan says logs must not contain
them; this asserts it structurally rather than trusting a reviewer to notice.

Structural, not textual: every logging-shaped call is located in the AST and its
*interpolated* expressions are inspected. Literal prose is left alone -- a
message may say the word "name" as long as it does not print one.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "drivectx"

# Identifying values drawn from Drive. Aggregate counts -- including the sharing
# exposure summary -- are bookkeeping and are safe to print, which is the whole
# point of reducing sharing to numbers in the first place.
FORBIDDEN_TOKENS = ("name", "owner", "email", "addr", "link", "display", "segment",
                    "web_view")

LOG_FUNCS = {"print", "_progress"}
LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "write"}


def _is_log_call(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in LOG_FUNCS
    if isinstance(func, ast.Attribute):
        return func.attr in LOG_METHODS or func.attr in LOG_FUNCS
    return False


def _interpolated(call: ast.Call):
    """The expressions whose *values* end up in the output."""
    for arg in list(call.args) + [kw.value for kw in call.keywords]:
        for sub in ast.walk(arg):
            if isinstance(sub, ast.FormattedValue):
                yield ast.unparse(sub.value)
        if not isinstance(arg, (ast.Constant, ast.JoinedStr)):
            yield ast.unparse(arg)


def _log_calls():
    for path in sorted(PKG.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_log_call(node):
                yield path, node


class LogRedactionTest(unittest.TestCase):
    def test_no_logging_call_interpolates_drive_content(self):
        offenders = []
        for path, call in _log_calls():
            for expr in _interpolated(call):
                low = expr.lower()
                for token in FORBIDDEN_TOKENS:
                    if token in low:
                        offenders.append(f"{path.name}:{call.lineno}: {expr}")
                        break
        self.assertEqual(offenders, [], "logging call leaks Drive data:\n" + "\n".join(offenders))

    def test_the_scan_actually_finds_the_logging_calls(self):
        """A redaction check that matched nothing would pass forever."""
        found = list(_log_calls())
        self.assertGreater(len(found), 5, "expected the CLI's output calls to be found")

    def test_the_check_fires_on_a_leak(self):
        """Prove the tripwire works before trusting it."""
        tree = ast.parse('print(f"indexed {row[\'name\']} for {owner_email}")')
        call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
        self.assertTrue(any(t in e.lower() for e in _interpolated(call)
                            for t in FORBIDDEN_TOKENS))

    def test_prose_naming_a_field_is_not_a_leak(self):
        """The check must distinguish saying 'name' from printing one, or it
        will be silenced the first time it cries wolf."""
        tree = ast.parse('print("file names are never logged")')
        call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
        self.assertFalse(any(t in e.lower() for e in _interpolated(call)
                             for t in FORBIDDEN_TOKENS))


if __name__ == "__main__":
    unittest.main()
