"""The safety property the whole V1 rests on.

The plan is explicit: if V1 cannot prove the agent lacks a send-capable path,
drafting does not launch. OAuth consent is not the boundary, because Gmail
compose permission also authorizes sending. So the absence of a send path is
asserted here against the source itself, and this test is expected to keep
failing loudly if anyone adds one later.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "mailctx"

FORBIDDEN = [
    re.compile(r"messages/send"),
    re.compile(r"drafts/[^\"']*send"),
    re.compile(r"\.send\s*\("),
    re.compile(r"gmail\.send"),
    re.compile(r"https://www\.googleapis\.com/auth/gmail\.(modify|full|settings)"),
    re.compile(r"\bmail\.google\.com\b"),  # the legacy all-powerful scope
]

ALLOWED_SCOPES = {
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
}


class NoSendPathTest(unittest.TestCase):
    def _sources(self):
        return sorted(PKG.rglob("*.py"))

    def test_package_contains_no_send_endpoint(self):
        offenders = []
        for path in self._sources():
            for lineno, line in enumerate(path.read_text().splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue  # prose about not sending is fine; calls are not
                for pattern in FORBIDDEN:
                    if pattern.search(line):
                        offenders.append(f"{path.name}:{lineno}: {stripped}")
        self.assertEqual(offenders, [], "send-capable code found:\n" + "\n".join(offenders))

    def test_declared_scopes_are_narrow(self):
        """Any googleapis auth scope appearing in the package must be allowlisted."""
        scope_re = re.compile(r"https://www\.googleapis\.com/auth/[\w.]+")
        found = set()
        for path in self._sources():
            found.update(scope_re.findall(path.read_text()))
        self.assertTrue(
            found <= ALLOWED_SCOPES,
            f"unexpected OAuth scopes: {sorted(found - ALLOWED_SCOPES)}",
        )


if __name__ == "__main__":
    unittest.main()
