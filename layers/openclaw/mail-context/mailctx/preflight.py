"""Preflight gates that must pass before real mail is stored.

The plan requires encryption at rest to be verified before the initial sync.
That requirement is enforced here as code rather than left as prose, because a
prose requirement in a runbook does not stop a timer from firing.
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

MIN_FREE_BYTES = 2 * 1024**3  # bounded metadata index; 2 GiB is generous headroom

CONFIG_DIR = Path.home() / ".config" / "mail-context"
WAIVER_PATH = CONFIG_DIR / "encryption-waiver.txt"


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    blocking: bool = True


@dataclass(frozen=True)
class Preflight:
    checks: tuple[Check, ...]

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks if c.blocking)

    @property
    def failures(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.blocking and not c.ok)

    def report(self) -> str:
        lines = []
        for c in self.checks:
            mark = "PASS" if c.ok else ("FAIL" if c.blocking else "WARN")
            lines.append(f"[{mark}] {c.name}: {c.detail}")
        return "\n".join(lines)


def _exists(path: Path) -> bool:
    """Path.exists() propagates PermissionError when a parent is unreadable.
    For preflight, 'cannot see it' must read as 'not there', never as a crash."""
    try:
        return path.exists()
    except OSError:
        return False


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def check_encryption_at_rest(path: Path) -> Check:
    """True only if the filesystem holding `path` sits on a dm-crypt device."""
    if not shutil.which("findmnt") or not shutil.which("lsblk"):
        return Check("encryption_at_rest", False, "cannot verify: findmnt/lsblk unavailable")
    # findmnt needs a path that exists; walk up to the nearest real ancestor so
    # the check works before the state directory has been created.
    probe = path
    while not _exists(probe) and probe != probe.parent:
        probe = probe.parent
    source = _run(["findmnt", "-no", "SOURCE", "--target", str(probe)])
    if not source:
        return Check("encryption_at_rest", False, f"cannot resolve mount for {probe}")
    types = _run(["lsblk", "-no", "TYPE", source]).split()
    if "crypt" in types:
        return Check("encryption_at_rest", True, f"{source} is a dm-crypt device")
    return Check(
        "encryption_at_rest", False,
        f"{source} is plaintext ({'/'.join(types) or 'unknown'}); "
        "0700/0600 modes do not survive disk theft or offline access",
    )


def check_swap_encrypted() -> Check:
    """Unencrypted swap can leak fragments of anything held in memory."""
    try:
        entries = [ln.split()[0] for ln in Path("/proc/swaps").read_text().splitlines()[1:]]
    except OSError:
        return Check("swap_encrypted", True, "no /proc/swaps", blocking=False)
    if not entries:
        return Check("swap_encrypted", True, "no swap configured", blocking=False)
    plain = [e for e in entries if "crypt" not in _run(["lsblk", "-no", "TYPE", e]).split()
             and not e.startswith("/dev/mapper/")]
    if plain:
        return Check("swap_encrypted", False, f"plaintext swap: {', '.join(plain)}", blocking=False)
    return Check("swap_encrypted", True, "swap is encrypted", blocking=False)


def check_free_space(path: Path, minimum: int = MIN_FREE_BYTES) -> Check:
    probe = path
    while not _exists(probe) and probe != probe.parent:
        probe = probe.parent
    try:
        free = shutil.disk_usage(probe).free
    except OSError as exc:
        return Check("free_space", False, f"cannot stat {probe}: {exc.__class__.__name__}")
    gib = free / 1024**3
    return Check("free_space", free >= minimum, f"{gib:.1f} GiB free (need {minimum / 1024**3:.0f})")


def check_fts5() -> Check:
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        conn.close()
        return Check("sqlite_fts5", True, f"available (sqlite {sqlite3.sqlite_version})")
    except sqlite3.Error:
        return Check("sqlite_fts5", False, "this Python's sqlite3 lacks FTS5")


def check_permissions(path: Path) -> Check:
    """State dir must be 0700 and the database 0600.

    Every stat here is guarded: when this runs from outside the production
    account the parent may exist but be unreadable, and an unreadable 0700 home
    is the expected healthy state rather than an error.
    """
    parent = path.parent
    try:
        pmode = parent.stat().st_mode & 0o777
    except FileNotFoundError:
        return Check("permissions", True, f"{parent} not created yet; will be made 0700", blocking=False)
    except PermissionError:
        return Check("permissions", True, f"{parent} not readable from this account; cannot verify",
                     blocking=False)
    except OSError as exc:
        return Check("permissions", False, f"cannot stat {parent}: {exc.__class__.__name__}")
    if pmode != 0o700:
        return Check("permissions", False, f"{parent} is {pmode:04o}, expected 0700")
    try:
        fmode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        return Check("permissions", True, f"{parent} 0700; db not created yet")
    except OSError as exc:
        return Check("permissions", False, f"cannot stat {path}: {exc.__class__.__name__}")
    if fmode != 0o600:
        return Check("permissions", False, f"{path} is {fmode:04o}, expected 0600")
    return Check("permissions", True, f"{parent} 0700, db 0600")


def run(db_path: Path, *, override_path: Path | None = WAIVER_PATH) -> Preflight:
    """Full preflight. An operator may waive encryption ONLY by writing an
    explicit override file; the waiver is then reported in every status call so
    it cannot be forgotten."""
    checks = [
        check_encryption_at_rest(db_path),
        check_swap_encrypted(),
        check_free_space(db_path),
        check_fts5(),
        check_permissions(db_path),
    ]
    if override_path and _exists(override_path):
        reason = override_path.read_text().strip().splitlines()
        note = reason[0][:120] if reason else "no reason recorded"
        checks = [
            Check(c.name, True, f"WAIVED by operator ({note}) -- was: {c.detail}", blocking=False)
            if c.name == "encryption_at_rest" and not c.ok else c
            for c in checks
        ]
    return Preflight(tuple(checks))


def require_for_initial_sync(db_path: Path, *, override_path: Path | None = WAIVER_PATH) -> Preflight:
    """Raise unless it is safe to store real mail."""
    pre = run(db_path, override_path=override_path)
    if not pre.ok:
        raise PreflightError(pre)
    return pre


class PreflightError(RuntimeError):
    def __init__(self, preflight: Preflight):
        self.preflight = preflight
        names = ", ".join(c.name for c in preflight.failures)
        super().__init__(f"preflight failed ({names}); refusing to store mail\n{preflight.report()}")
