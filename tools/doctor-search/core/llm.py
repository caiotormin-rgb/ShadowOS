"""One-shot model calls through OpenClaw (no tools, no session), answering in JSON."""

from __future__ import annotations

import json
import os
import re
import subprocess

import notify
from store import DoctorError

# Research quality must not follow a future change to the WhatsApp front end.
DEFAULT_RESEARCH_MODEL = "openai/gpt-5.6-sol"
AGENT = os.environ.get("DOCTOR_LLM_AGENT", "shared-tools").strip() or "shared-tools"
MODEL = os.environ.get("DOCTOR_LLM_MODEL", DEFAULT_RESEARCH_MODEL).strip() or DEFAULT_RESEARCH_MODEL
RUN = subprocess.run
# A single argv string is capped at 128 KiB on Linux.
MAX_PROMPT_CHARS = 100_000


def parse_json(text: str):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.M)
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        raise ValueError("no JSON")
    value, _ = json.JSONDecoder().raw_decode(text[min(starts):])
    return value


def ask_json(prompt: str, timeout: int = 240):
    cmd = [notify.openclaw_bin(), "infer", "model", "run", "--agent", AGENT, "--local",
           "--thinking", "low", "--json", f"--prompt={prompt[:MAX_PROMPT_CHARS]}"]
    cmd += ["--model", MODEL]
    try:
        done = RUN(cmd, capture_output=True, text=True, timeout=timeout)
        data = json.loads(done.stdout[done.stdout.find("{"):])
        return parse_json(data["outputs"][0]["text"])
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, IndexError, TypeError):
        raise DoctorError("llm_failed", "The model call failed or returned no usable JSON.")
