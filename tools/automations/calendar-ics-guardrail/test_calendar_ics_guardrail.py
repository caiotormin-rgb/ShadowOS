import datetime as dt
import importlib.util
import sys
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("calendar_ics_guardrail.py")
spec = importlib.util.spec_from_file_location("calendar_ics_guardrail", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class CalendarParserTests(unittest.TestCase):
    def test_timed_event_folding_escaping_and_timezone(self):
        text = """BEGIN:VCALENDAR\r
VERSION:2.0\r
METHOD:PUBLISH\r
BEGIN:VEVENT\r
UID:synthetic-1@example.test\r
SEQUENCE:2\r
SUMMARY:Safety review\\, phase 1\r
DTSTART;TZID=America/New_York:20300115T090000\r
DTEND;TZID=America/New_York:20300115T100000\r
DESCRIPTION:Line one\\nLine two with https://example.test/a\r
LOCATION:Room\r
  42\r
STATUS:CONFIRMED\r
END:VEVENT\r
END:VCALENDAR\r
"""
        event = module.parse_calendar(text)[0]
        self.assertEqual(event.uid, "synthetic-1@example.test")
        self.assertEqual(event.sequence, 2)
        self.assertEqual(event.summary, "Safety review, phase 1")
        self.assertEqual(event.description, "Line one\nLine two with https://example.test/a")
        self.assertEqual(event.location, "Room 42")
        self.assertEqual(event.start.timezone, "America/New_York")
        self.assertEqual(event.start.canonical, dt.datetime(2030, 1, 15, 14, tzinfo=dt.timezone.utc))
        self.assertFalse(event.cancelled)

    def test_all_day_duration_and_cancel(self):
        text = """BEGIN:VCALENDAR
METHOD:CANCEL
BEGIN:VEVENT
UID:synthetic-2@example.test
SUMMARY:All day
DTSTART;VALUE=DATE:20300201
DURATION:P2D
END:VEVENT
END:VCALENDAR
"""
        event = module.parse_calendar(text)[0]
        self.assertTrue(event.start.all_day)
        self.assertEqual(event.start.api_value, "2030-02-01")
        self.assertEqual(event.end.api_value, "2030-02-03")
        self.assertTrue(event.cancelled)

    def test_nested_alarm_fields_do_not_override_event(self):
        text = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:synthetic-3@example.test
SUMMARY:Real title
DTSTART:20300301T120000Z
DTEND:20300301T130000Z
BEGIN:VALARM
DESCRIPTION:Ignore me
END:VALARM
END:VEVENT
END:VCALENDAR
"""
        event = module.parse_calendar(text)[0]
        self.assertEqual(event.summary, "Real title")
        self.assertEqual(event.description, "")

    def test_unsupported_recurrence_is_rejected_conservatively(self):
        text = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:synthetic-4@example.test
SUMMARY:Dates
DTSTART:20300301T120000Z
DTEND:20300301T130000Z
RDATE:20300308T120000Z
END:VEVENT
END:VCALENDAR
"""
        with self.assertRaises(module.GuardrailError):
            module.parse_calendar(text)

    def test_legacy_timezone_alias(self):
        prop = module.ContentLine("DTSTART", {"TZID": "US/Eastern"}, "20300115T090000")
        parsed = module.parse_temporal(prop)
        self.assertEqual(parsed.timezone, "America/New_York")
        self.assertEqual(parsed.canonical, dt.datetime(2030, 1, 15, 14, tzinfo=dt.timezone.utc))

    def test_command_arguments_keep_untrusted_text_literal(self):
        text = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:synthetic-5@example.test
SUMMARY:$(touch /tmp/should-not-exist)
DTSTART:20300301T120000Z
DTEND:20300301T130000Z
END:VEVENT
END:VCALENDAR
"""
        event = module.parse_calendar(text)[0]
        args = module.event_args(event, "message-id")
        self.assertIn("--summary=$(touch /tmp/should-not-exist)", args)
        self.assertIn("--private-prop=openclawIcsUid=synthetic-5@example.test", args)
        self.assertNotIn("--timezone", args)

    def test_manual_match_normalizes_case_and_spacing(self):
        self.assertEqual(module.normalize_summary("  Café   REVIEW "), "café review")


if __name__ == "__main__":
    unittest.main()
