"""Keep the household's real phone numbers out of every tracked file.

Two guards:

* RealNumberLeakTests reads the local, untracked sources of real numbers (the
  OpenClaw WhatsApp allowlist and config/members.json) and fails if any tracked
  file contains one, in any formatting. It skips when neither source exists, so
  it runs on a fresh clone without them.
* FictionalNumberTests is a heuristic that needs no secrets: every E.164-looking
  number in a tracked file must match one of the fictional patterns below.

Failure messages never print a whole number, only a masked form like +551***21.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
OPENCLAW_CONFIG = Path(
    os.environ.get("OPENCLAW_CONFIG", Path.home() / ".openclaw" / "openclaw.json")
)
MEMBERS = HERE.parent / "config" / "members.json"

# Separators people type inside a number. Removing them lets "+55 11 9..." and
# "+55119..." match the same digits.
SEPARATORS = re.compile(r"[\s\-().+/]")
# The last 8 digits identify a number well enough, and catch it written
# without its country code or area code.
TAIL = 8

# Fictional patterns a tracked file may use, applied to the digits only.
FICTIONAL = {
    # NANP: 555-0100..0199 is reserved for fiction in every area code.
    "nanp-555-01xx": re.compile(r"1\d{3}55501\d\d"),
    # NANP: 555 is not an assigned area code (also covers short invalid inputs).
    "nanp-area-555": re.compile(r"1555\d{0,7}"),
    # Brazil has no reserved range. A mobile number (area code + 9 digits
    # starting with 9) whose next four digits are 0000 is visibly synthetic and
    # still passes E.164 and the +55 timezone default.
    "br-90000-xxxx": re.compile(r"55\d\d90000\d{4}"),
    # UK: Ofcom reserves 10003 900000..900999 for drama.
    "uk-7700-900xxx": re.compile(r"447700900\d{3}"),
    # Obvious placeholders: the last 8 digits are one or two runs of a single
    # digit (99999999, 88887777) or the ascending run 12345678.
    "placeholder-runs": re.compile(r"\d*?(?:(\d)\1{7}|(\d)\2{3}(\d)\3{3})"),
    "placeholder-ascending": re.compile(r"\d*12345678"),
}
E164_LIKE = re.compile(r"(?<![\w+])\+\d[\d \-().]{5,20}\d")


def mask(digits: str) -> str:
    return f"+{digits[:3]}***{digits[-2:]}"


def tracked_files() -> list[Path]:
    try:
        root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=HERE,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        listed = subprocess.run(
            ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    paths = [Path(root) / p.decode() for p in listed.split(b"\0") if p]
    return [p for p in paths if p.is_file()]


def read_text(path: Path) -> str:
    # latin-1 never fails, and digits are the same bytes in any encoding.
    return path.read_bytes().decode("latin-1")


def _allow_from(node) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "allowFrom" and isinstance(value, list):
                found.extend(str(v) for v in value)
            else:
                found.extend(_allow_from(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_allow_from(value))
    return found


def real_numbers() -> set[str]:
    """Digits of every real number in the local sources that exist."""
    raw: list[str] = []
    if OPENCLAW_CONFIG.is_file():
        try:
            config = json.loads(OPENCLAW_CONFIG.read_text())
            raw.extend(_allow_from(config.get("channels", {})))
        except (OSError, ValueError, AttributeError):
            pass
    if MEMBERS.is_file():
        try:
            roster = json.loads(MEMBERS.read_text())
            raw.extend(str(m.get("phone", "")) for m in roster.get("members", []))
        except (OSError, ValueError, AttributeError):
            pass
    digits = {re.sub(r"\D", "", value) for value in raw}
    return {d for d in digits if len(d) >= TAIL}


def is_fictional(digits: str) -> bool:
    return any(p.fullmatch(digits) for p in FICTIONAL.values())


class RealNumberLeakTests(unittest.TestCase):
    def test_no_tracked_file_contains_a_real_number(self):
        numbers = real_numbers()
        if not numbers:
            self.skipTest("no local allowlist or members.json to check against")
        files = tracked_files()
        if not files:
            self.skipTest("not in a git checkout")
        tails = {n[-TAIL:]: n for n in numbers}
        leaks = []
        for path in files:
            flat = SEPARATORS.sub("", read_text(path))
            for tail, number in tails.items():
                count = flat.count(tail)
                if count:
                    leaks.append(f"{path}: {mask(number)} x{count}")
        self.assertEqual(leaks, [], "real phone numbers in tracked files")


class FictionalNumberTests(unittest.TestCase):
    def test_fictional_patterns_accept_the_documented_examples(self):
        for number in ("+12025550101", "+19175550182", "+15551234567",
                       "+5511900000021", "+447700900123", "+5511999999999"):
            self.assertTrue(is_fictional(re.sub(r"\D", "", number)), mask(number[1:]))

    def test_fictional_patterns_reject_ordinary_numbers(self):
        # Digits only, so this file does not trip its own heuristic.
        for digits in ("1202" "5551234", "5511" "987654321", "4479" "11123456"):
            self.assertFalse(is_fictional(digits), mask(digits))

    def test_tracked_numbers_are_all_fictional(self):
        files = tracked_files()
        if not files:
            self.skipTest("not in a git checkout")
        flagged = []
        for path in files:
            for match in E164_LIKE.finditer(read_text(path)):
                digits = re.sub(r"\D", "", match.group(0))
                if 8 <= len(digits) <= 15 and not is_fictional(digits):
                    flagged.append(f"{path}: {mask(digits)}")
        self.assertEqual(
            flagged, [],
            "numbers outside the fictional patterns in test_privacy.FICTIONAL; "
            "use US 202-555-01xx or Brazil 11 90000-00xx",
        )


if __name__ == "__main__":
    unittest.main()
