"""WhatsApp messages to a requester, sent through the OpenClaw CLI."""

from __future__ import annotations

import os
import re
import shutil
import subprocess

from store import DoctorError

ACCOUNT = os.environ.get("DOCTOR_WHATSAPP_ACCOUNT", "tools")


def openclaw_bin() -> str:
    return os.environ.get("OPENCLAW_BIN") or shutil.which("openclaw") or "openclaw"


def whatsapp(target: str, text: str, runner=subprocess.run) -> None:
    if not re.fullmatch(r"\+\d{8,15}", target or ""):
        raise DoctorError("bad_target", "Notices go only to a WhatsApp number.")
    try:
        done = runner([openclaw_bin(), "message", "send", "--channel", "whatsapp", "--account", ACCOUNT,
                       "--target", target, "--message", text],
                      capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.TimeoutExpired):
        raise DoctorError("notify_failed", "Could not send the WhatsApp message.")
    if done.returncode != 0:
        raise DoctorError("notify_failed", "Could not send the WhatsApp message.")
