"""The safety property the whole V1 rests on.

The real boundary is the OAuth scope: under `drive.metadata.readonly` Google
itself refuses a content read, so the dangerous capability is absent rather than
merely uncalled. Prefer that over a grant where the download endpoint would
succeed and only our discipline stops it -- the second depends on every future
edit to this package continuing to behave, and the first does not.

This file is the tripwire that tells us when someone tried anyway.

One structural subtlety: the transport's own ceiling has to *name* the things it
forbids, so a naive grep flags the guard as the violation. The scan therefore
skips the lines that define the ceiling constants -- located by AST, not by
eyeballing line numbers -- and separately asserts that those constants live only
in the transport, so the exemption cannot be borrowed by naming a variable
`FORBIDDEN` somewhere else.
"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "drivectx"

# Constants whose whole job is to spell out what must never be requested.
CEILING_NAMES = {"ALLOWED", "FORBIDDEN", "FORBIDDEN_FIELDS", "PARAMS"}
CEILING_MODULE = "drive.py"

FORBIDDEN = [
    re.compile(r"alt\s*=\s*[\"']?media"),          # the media-download switch
    re.compile(r"/export\b"),                       # Docs/Sheets/Slides export
    re.compile(r"\buploadType\b"),
    re.compile(r"\backnowledgeAbuse\b"),
    re.compile(r"\bthumbnailLink\b"),
    re.compile(r"\bwebContentLink\b"),
    re.compile(r"\bmd5Checksum\b|\bsha1Checksum\b|\bsha256Checksum\b"),
    re.compile(r"\bexportLinks\b"),
    re.compile(r"\brevisions\b"),
    re.compile(r"method\s*=\s*[\"'](POST|PUT|PATCH|DELETE)"),
]

ALLOWED_SCOPES = {"https://www.googleapis.com/auth/drive.metadata.readonly"}


def _prose_lines(path: Path) -> set[int]:
    """Line numbers occupied by docstrings.

    Prose is allowed to name what it forbids -- the alternative is a module that
    cannot explain its own ceiling. A bare string expression executes nothing, so
    nothing can hide in one.
    """
    lines: set[int] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return lines


def _ceiling_lines(path: Path) -> set[int]:
    """Line numbers occupied by the ceiling-constant definitions in one file."""
    lines: set[int] = set()
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = {t.id for t in targets if isinstance(t, ast.Name)}
            if names & CEILING_NAMES:
                lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return lines


class NoContentPathTest(unittest.TestCase):
    def _sources(self):
        return sorted(PKG.rglob("*.py"))

    def test_the_ceiling_exemption_applies_only_to_the_transport(self):
        """Otherwise the tripwire could be silenced by naming a list FORBIDDEN."""
        exempt = {p.name for p in self._sources() if _ceiling_lines(p)}
        self.assertEqual(exempt, {CEILING_MODULE})

    def test_package_has_no_content_reading_code(self):
        offenders = []
        for path in self._sources():
            skip = _ceiling_lines(path) | _prose_lines(path)
            for lineno, line in enumerate(path.read_text().splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#") or lineno in skip:
                    continue  # prose about not reading content is fine; calls are not
                for pattern in FORBIDDEN:
                    if pattern.search(line):
                        offenders.append(f"{path.name}:{lineno}: {stripped}")
        self.assertEqual(offenders, [], "content-capable code found:\n" + "\n".join(offenders))

    def test_the_tripwire_actually_fires(self):
        """A check that has never been seen to fail is a check nobody has tested."""
        hostile = 'url = BASE + "/files/" + fid + "?alt=media"'
        self.assertTrue(any(p.search(hostile) for p in FORBIDDEN))

    def test_declared_scopes_are_narrow(self):
        """Any googleapis auth scope appearing in the package must be allowlisted.

        `drive.readonly` would work perfectly well for an index, and that is
        exactly why it is not here: it can read content.
        """
        scope_re = re.compile(r"https://www\.googleapis\.com/auth/[\w.]+")
        found = set()
        for path in self._sources():
            found.update(scope_re.findall(path.read_text()))
        self.assertTrue(
            found <= ALLOWED_SCOPES,
            f"unexpected OAuth scopes: {sorted(found - ALLOWED_SCOPES)}",
        )

    def test_the_scope_constant_is_exactly_what_the_plan_approved(self):
        from drivectx.auth import SCOPES
        self.assertEqual(SCOPES, ("https://www.googleapis.com/auth/drive.metadata.readonly",))

    def test_no_grantee_address_field_is_ever_requested(self):
        """A permission's emailAddress is the one field that would turn this
        index into a social graph. It must not appear in any field mask."""
        offenders = []
        for path in self._sources():
            for match in re.finditer(r"permissions?\(([^)]*)\)", path.read_text()):
                if "email" in match.group(1).lower():
                    offenders.append(f"{path.name}: {match.group(0)}")
        self.assertEqual(offenders, [], f"permission address requested: {offenders}")

    def test_the_transport_is_the_only_module_that_touches_the_network(self):
        """Everything else must go through the ceiling. A second urlopen would
        be a second, unchecked door."""
        offenders = [p.name for p in self._sources()
                     if p.name != CEILING_MODULE
                     and re.search(r"urlopen|http\.client|socket\.", p.read_text())]
        self.assertEqual(offenders, [], f"network access outside the transport: {offenders}")


if __name__ == "__main__":
    unittest.main()
