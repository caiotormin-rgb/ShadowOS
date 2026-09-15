#!/usr/bin/env python3
"""Deterministically copy active Gmail .ics events to Google Calendar.

Email and ICS fields are always untrusted data. This program never opens URLs,
modifies Gmail, adds attendees, or deletes calendar events. Google access goes
exclusively through the authenticated gog-openclaw wrapper.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

GOG = "/home/openclaw/.local/bin/gog-openclaw"
ACCOUNT = os.environ.get("GUARDRAIL_ACCOUNT", "owner@example.com")
CALENDAR = "primary"
DEFAULT_TIMEZONE = "America/New_York"
DEFAULT_QUERY = "newer_than:3d filename:ics"
MAX_FIELD = {"summary": 512, "description": 8192, "location": 1024, "uid": 1024}
TIMEZONE_ALIASES = {
    "US/Eastern": "America/New_York",
    "US/Central": "America/Chicago",
    "US/Mountain": "America/Denver",
    "US/Pacific": "America/Los_Angeles",
    "Eastern Standard Time": "America/New_York",
    "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver",
    "Pacific Standard Time": "America/Los_Angeles",
}


class GuardrailError(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class ContentLine:
    name: str
    params: dict[str, str]
    value: str


@dataclasses.dataclass(frozen=True)
class Temporal:
    api_value: str
    canonical: dt.date | dt.datetime
    all_day: bool
    timezone: str | None = None


@dataclasses.dataclass(frozen=True)
class EventSpec:
    uid: str
    sequence: int
    summary: str
    start: Temporal
    end: Temporal
    description: str
    location: str
    rrules: tuple[str, ...]
    cancelled: bool


@dataclasses.dataclass
class Result:
    created: int = 0
    updated: int = 0
    already_present: int = 0
    ignored_past: int = 0
    cancellations: list[str] = dataclasses.field(default_factory=list)
    skipped: list[str] = dataclasses.field(default_factory=list)
    errors: list[str] = dataclasses.field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.created or self.updated)


def run_gog(args: list[str], *, timeout: int = 90) -> dict[str, Any]:
    command = [GOG, *args, "--account", ACCOUNT, "--json", "--no-input"]
    proc = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()[:1200]
        raise GuardrailError(f"gog failed ({proc.returncode}): {detail}")
    try:
        value = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise GuardrailError(f"gog returned invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise GuardrailError("gog returned a non-object JSON value")
    return value


def unfold_ics(text: str) -> list[str]:
    physical = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    logical: list[str] = []
    for line in physical:
        if line.startswith((" ", "\t")) and logical:
            logical[-1] += line[1:]
        else:
            logical.append(line)
    return logical


def split_unquoted(value: str, delimiter: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quoted = False
    escaped = False
    for char in value:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            current.append(char)
            escaped = True
        elif char == '"':
            quoted = not quoted
            current.append(char)
        elif char == delimiter and not quoted:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


def parse_content_line(line: str) -> ContentLine:
    quoted = False
    escaped = False
    colon = -1
    for index, char in enumerate(line):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif char == ":" and not quoted:
            colon = index
            break
    if colon < 1:
        raise GuardrailError("malformed ICS content line")
    left, raw_value = line[:colon], line[colon + 1 :]
    tokens = split_unquoted(left, ";")
    name = tokens[0].upper()
    params: dict[str, str] = {}
    for token in tokens[1:]:
        if "=" in token:
            key, value = token.split("=", 1)
            params[key.upper()] = value.strip('"')
    return ContentLine(name=name, params=params, value=raw_value)


def unescape_text(value: str) -> str:
    value = value.replace("\\N", "\\n")
    return re.sub(
        r"\\([nN,;\\])",
        lambda match: {
            "n": "\n", "N": "\n", ",": ",", ";": ";", "\\": "\\"
        }[match.group(1)],
        value,
    ).replace("\x00", "")


def bounded(value: str, field: str) -> str:
    return value.strip()[: MAX_FIELD[field]]


def parse_duration(value: str) -> dt.timedelta:
    match = re.fullmatch(
        r"(?P<sign>-)?P(?:(?P<weeks>\d+)W)?(?:(?P<days>\d+)D)?"
        r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?",
        value,
    )
    if not match:
        raise GuardrailError(f"unsupported DURATION {value!r}")
    amount = dt.timedelta(
        weeks=int(match.group("weeks") or 0), days=int(match.group("days") or 0),
        hours=int(match.group("hours") or 0), minutes=int(match.group("minutes") or 0),
        seconds=int(match.group("seconds") or 0),
    )
    return -amount if match.group("sign") else amount


def parse_temporal(prop: ContentLine, *, default_timezone: str = DEFAULT_TIMEZONE) -> Temporal:
    raw = prop.value.strip()
    is_date = prop.params.get("VALUE", "").upper() == "DATE" or bool(re.fullmatch(r"\d{8}", raw))
    if is_date:
        value = dt.datetime.strptime(raw, "%Y%m%d").date()
        return Temporal(value.isoformat(), value, True)
    utc = raw.endswith("Z")
    body = raw[:-1] if utc else raw
    fmt = "%Y%m%dT%H%M%S" if len(body) == 15 else "%Y%m%dT%H%M"
    try:
        naive = dt.datetime.strptime(body, fmt)
    except ValueError as exc:
        raise GuardrailError(f"unsupported date-time {raw!r}") from exc
    timezone = "UTC" if utc else prop.params.get("TZID", default_timezone)
    timezone = TIMEZONE_ALIASES.get(timezone, timezone)
    try:
        aware = naive.replace(tzinfo=ZoneInfo(timezone))
    except ZoneInfoNotFoundError as exc:
        raise GuardrailError(f"unknown TZID {timezone!r}") from exc
    return Temporal(aware.isoformat(), aware.astimezone(dt.timezone.utc), False,
                    None if timezone == "UTC" else timezone)


def add_duration(start: Temporal, duration: str) -> Temporal:
    delta = parse_duration(duration)
    if delta <= dt.timedelta(0):
        raise GuardrailError("event DURATION must be positive")
    value = start.canonical + delta
    if start.all_day:
        if not isinstance(value, dt.date) or isinstance(value, dt.datetime):
            raise GuardrailError("invalid all-day duration")
        return Temporal(value.isoformat(), value, True)
    if not isinstance(value, dt.datetime):
        raise GuardrailError("invalid timed duration")
    local = value.astimezone(ZoneInfo(start.timezone or "UTC"))
    return Temporal(local.isoformat(), value, False, start.timezone)


def first(props: dict[str, list[ContentLine]], name: str) -> ContentLine | None:
    values = props.get(name, [])
    return values[0] if values else None


def parse_calendar(text: str) -> list[EventSpec]:
    stack: list[str] = []
    calendar_method = ""
    raw_events: list[dict[str, list[ContentLine]]] = []
    current: dict[str, list[ContentLine]] | None = None
    for raw_line in unfold_ics(text):
        if not raw_line:
            continue
        line = parse_content_line(raw_line)
        if line.name == "BEGIN":
            component = line.value.upper()
            stack.append(component)
            if component == "VEVENT":
                current = {}
            continue
        if line.name == "END":
            component = line.value.upper()
            if component == "VEVENT" and current is not None:
                raw_events.append(current)
                current = None
            if not stack or stack[-1] != component:
                raise GuardrailError(f"unbalanced ICS component {component}")
            stack.pop()
            continue
        if stack == ["VCALENDAR"] and line.name == "METHOD":
            calendar_method = line.value.upper()
        elif stack and stack[-1] == "VEVENT" and current is not None:
            current.setdefault(line.name, []).append(line)
    if stack:
        raise GuardrailError("unterminated ICS component")

    events: list[EventSpec] = []
    for props in raw_events:
        uid_prop, summary_prop = first(props, "UID"), first(props, "SUMMARY")
        start_prop, end_prop = first(props, "DTSTART"), first(props, "DTEND")
        duration_prop = first(props, "DURATION")
        if not uid_prop or not summary_prop or not start_prop:
            raise GuardrailError("VEVENT requires UID, SUMMARY, and DTSTART")
        if any(name in props for name in ("RDATE", "EXDATE")):
            raise GuardrailError("RDATE/EXDATE recurrence is unsupported")
        start = parse_temporal(start_prop)
        end = parse_temporal(end_prop) if end_prop else add_duration(start, duration_prop.value) if duration_prop else None
        if end is None:
            raise GuardrailError("VEVENT requires DTEND or DURATION")
        if start.all_day != end.all_day or end.canonical <= start.canonical:
            raise GuardrailError("invalid event time range")
        sequence_prop = first(props, "SEQUENCE")
        try:
            sequence = int(sequence_prop.value) if sequence_prop else 0
        except ValueError as exc:
            raise GuardrailError("SEQUENCE is not an integer") from exc
        status_prop = first(props, "STATUS")
        description_prop, location_prop = first(props, "DESCRIPTION"), first(props, "LOCATION")
        events.append(EventSpec(
            uid=bounded(unescape_text(uid_prop.value), "uid"), sequence=sequence,
            summary=bounded(unescape_text(summary_prop.value), "summary"), start=start, end=end,
            description=bounded(unescape_text(description_prop.value) if description_prop else "", "description"),
            location=bounded(unescape_text(location_prop.value) if location_prop else "", "location"),
            rrules=tuple(f"RRULE:{item.value}" for item in props.get("RRULE", [])),
            cancelled=calendar_method == "CANCEL" or
                      (status_prop is not None and status_prop.value.upper() == "CANCELLED"),
        ))
    return events


def normalize_summary(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def parse_google_temporal(value: dict[str, Any]) -> dt.date | dt.datetime:
    if value.get("date"):
        return dt.date.fromisoformat(value["date"])
    raw = value.get("dateTime")
    if not raw:
        raise GuardrailError("calendar event lacks start/end value")
    parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(value.get("timeZone", DEFAULT_TIMEZONE)))
    return parsed.astimezone(dt.timezone.utc)


def same_times(item: dict[str, Any], event: EventSpec) -> bool:
    try:
        return parse_google_temporal(item["start"]) == event.start.canonical and \
               parse_google_temporal(item["end"]) == event.end.canonical
    except (KeyError, TypeError, ValueError, GuardrailError):
        return False


def iso_window(event: EventSpec, *, margin_days: int) -> tuple[str, str]:
    zone = ZoneInfo(event.start.timezone or DEFAULT_TIMEZONE)
    if event.start.all_day:
        if isinstance(event.start.canonical, dt.datetime) or isinstance(event.end.canonical, dt.datetime):
            raise GuardrailError("invalid all-day event")
        start = dt.datetime.combine(event.start.canonical, dt.time.min, zone)
        end = dt.datetime.combine(event.end.canonical, dt.time.min, zone)
    else:
        if not isinstance(event.start.canonical, dt.datetime) or not isinstance(event.end.canonical, dt.datetime):
            raise GuardrailError("invalid timed event")
        start, end = event.start.canonical, event.end.canonical
    return ((start - dt.timedelta(days=margin_days)).isoformat(),
            (end + dt.timedelta(days=margin_days)).isoformat())


def calendar_events(args: list[str]) -> list[dict[str, Any]]:
    data = run_gog(["calendar", "events", CALENDAR, *args, "--max", "250", "--all-pages"])
    return [item for item in data.get("events", []) if isinstance(item, dict)]


def find_uid_matches(event: EventSpec) -> list[dict[str, Any]]:
    start, end = iso_window(event, margin_days=370)
    return calendar_events(["--from", start, "--to", end,
                            "--private-prop-filter", f"openclawIcsUid={event.uid}"])


def find_manual_match(event: EventSpec) -> dict[str, Any] | None:
    start, end = iso_window(event, margin_days=1)
    summary = normalize_summary(event.summary)
    matches = [item for item in calendar_events(["--from", start, "--to", end])
               if item.get("status") != "cancelled"
               and normalize_summary(str(item.get("summary", ""))) == summary
               and same_times(item, event)]
    # Any exact manual match prevents a create. Existing duplicates are left
    # untouched rather than turning every future scan into a hard failure.
    return matches[0] if matches else None


def event_args(event: EventSpec, message_id: str,
               existing: dict[str, Any] | None = None) -> list[str]:
    # Use --flag=value for every untrusted string. The CLI otherwise parses a
    # value beginning with '-' as another option even when argv is shell-free.
    args = [f"--summary={event.summary}", f"--from={event.start.api_value}",
            f"--to={event.end.api_value}", f"--description={event.description}",
            f"--location={event.location}", "--visibility=private",
            "--send-updates=none"]
    if event.start.all_day:
        args.append("--all-day")
    else:
        # Both create and update accept the split timezone flags; update does
        # not accept the create command's combined --timezone flag.
        if event.start.timezone:
            args.append(f"--start-timezone={event.start.timezone}")
        if event.end.timezone:
            args.append(f"--end-timezone={event.end.timezone}")
    for rule in event.rrules:
        args.append(f"--rrule={rule}")
    private: dict[str, str] = {}
    if existing:
        private.update(existing.get("extendedProperties", {}).get("private", {}))
    private.update({"openclawIcsUid": event.uid, "openclawIcsSequence": str(event.sequence),
                    "openclawSourceMessage": message_id})
    for key, value in sorted(private.items()):
        if isinstance(key, str) and isinstance(value, str):
            args.append(f"--private-prop={key}={value}")
    return args


def create_event(event: EventSpec, message_id: str, *, dry_run: bool) -> None:
    args = ["calendar", "create", CALENDAR, *event_args(event, message_id)]
    if dry_run:
        args.append("--dry-run")
    run_gog(args)


def update_event(event: EventSpec, message_id: str, existing: dict[str, Any],
                 *, dry_run: bool) -> None:
    event_id = existing.get("id")
    if not event_id:
        raise GuardrailError("UID match lacks calendar event id")
    args = ["calendar", "update", CALENDAR, str(event_id),
            *event_args(event, message_id, existing)]
    if dry_run:
        args.append("--dry-run")
    run_gog(args)


def event_is_old(event: EventSpec, *, grace_hours: float) -> bool:
    if event.rrules:
        return False
    if event.end.all_day:
        if isinstance(event.end.canonical, dt.datetime):
            raise GuardrailError("invalid all-day end")
        end = dt.datetime.combine(event.end.canonical, dt.time.min,
                                  ZoneInfo(DEFAULT_TIMEZONE)).astimezone(dt.timezone.utc)
    else:
        if not isinstance(event.end.canonical, dt.datetime):
            raise GuardrailError("invalid timed end")
        end = event.end.canonical
    return end < dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=grace_hours)


def process_event(event: EventSpec, message_id: str, *, dry_run: bool,
                  past_grace_hours: float, result: Result) -> None:
    if event.cancelled:
        result.cancellations.append(event.uid)
        return
    if event_is_old(event, grace_hours=past_grace_hours):
        result.ignored_past += 1
        return
    uid_matches = find_uid_matches(event)
    if len(uid_matches) > 1:
        # Do not deepen an existing duplicate situation. If at least one copy
        # already has the incoming times, the invariant is satisfied.
        if any(same_times(item, event) for item in uid_matches):
            result.already_present += 1
            return
        raise GuardrailError(f"multiple private-property matches for UID {event.uid}")
    if uid_matches:
        existing = uid_matches[0]
        props = existing.get("extendedProperties", {}).get("private", {})
        try:
            existing_sequence = int(props.get("openclawIcsSequence", "-1"))
        except ValueError:
            existing_sequence = -1
        if event.sequence > existing_sequence or not same_times(existing, event):
            update_event(event, message_id, existing, dry_run=dry_run)
            result.updated += 1
        else:
            result.already_present += 1
        return
    if find_manual_match(event):
        result.already_present += 1
        return
    create_event(event, message_id, dry_run=dry_run)
    result.created += 1


def guardrail(*, query: str, max_threads: int, dry_run: bool,
              past_grace_hours: float) -> Result:
    result = Result()
    search = run_gog(["gmail", "search", query, "--max", str(max_threads)])
    threads = search.get("threads", [])
    if not isinstance(threads, list):
        raise GuardrailError("Gmail search response lacks a threads list")
    with tempfile.TemporaryDirectory(prefix="openclaw-ics-") as tmp:
        tmpdir = Path(tmp)
        for thread in threads:
            if not isinstance(thread, dict) or not thread.get("id"):
                continue
            thread_id = str(thread["id"])
            try:
                details = run_gog(["gmail", "thread", "get", thread_id, "--sanitize-content"])
                messages = details.get("thread", {}).get("messages", [])
                for message in messages:
                    if not isinstance(message, dict) or not message.get("id"):
                        continue
                    message_id = str(message["id"])
                    for index, attachment in enumerate(message.get("attachments", [])):
                        if not isinstance(attachment, dict):
                            continue
                        filename = str(attachment.get("filename", ""))
                        mime = str(attachment.get("mimeType", "")).lower()
                        if mime != "text/calendar" and not filename.lower().endswith(".ics"):
                            continue
                        attachment_id = attachment.get("attachmentId")
                        if not attachment_id:
                            result.skipped.append(f"{message_id}:{filename}: missing attachment id")
                            continue
                        output = tmpdir / f"{message_id}-{index}.ics"
                        run_gog(["gmail", "attachment", message_id, str(attachment_id),
                                 "--out", str(output)])
                        if not output.exists() or output.stat().st_size > 2_000_000:
                            raise GuardrailError("attachment missing or larger than 2 MB")
                        try:
                            events = parse_calendar(output.read_text(errors="strict"))
                        except (UnicodeDecodeError, GuardrailError) as exc:
                            result.skipped.append(f"{message_id}:{filename}: {exc}")
                            continue
                        for event in events:
                            try:
                                process_event(event, message_id, dry_run=dry_run,
                                              past_grace_hours=past_grace_hours, result=result)
                            except GuardrailError as exc:
                                result.errors.append(f"{message_id}:{event.uid}: {exc}")
            except (GuardrailError, subprocess.TimeoutExpired) as exc:
                result.errors.append(f"thread {thread_id}: {exc}")
    return result


def print_result(result: Result, *, dry_run: bool) -> None:
    if not result.changed and not result.cancellations and not result.errors and not result.skipped:
        print("NO_CHANGES")
        return
    print(json.dumps({"dryRun": dry_run, "created": result.created,
                      "updated": result.updated, "alreadyPresent": result.already_present,
                      "ignoredPast": result.ignored_past,
                      "cancellationsObserved": result.cancellations,
                      "skipped": result.skipped, "errors": result.errors},
                     ensure_ascii=False, sort_keys=True))


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--max-threads", type=int, default=100)
    parser.add_argument("--past-grace-hours", type=float, default=24.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.max_threads < 1 or args.max_threads > 500:
        parser.error("--max-threads must be between 1 and 500")
    try:
        result = guardrail(query=args.query, max_threads=args.max_threads,
                           dry_run=args.dry_run, past_grace_hours=args.past_grace_hours)
    except (GuardrailError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"errors": [str(exc)]}), file=sys.stderr)
        return 1
    print_result(result, dry_run=args.dry_run)
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
