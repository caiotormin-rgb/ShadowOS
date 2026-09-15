# Calendar ICS guardrail

Model-free replacement for automation `df3687d0-256b-4ce0-a907-5cc4835da9e8`.
Every 15 minutes it scans recent Gmail `.ics` attachments and ensures active
future events exist on the primary calendar.

Safety invariants:

- Google access only through `/home/openclaw/.local/bin/gog-openclaw`.
- Gmail is read-only; calendar events are never deleted.
- Attendees are never copied and notifications use `--send-updates=none`.
- ICS text is untrusted data and is passed as argv values, never shell code.
- Cancellations, unsupported recurrence forms, ambiguity, and malformed data
  are reported rather than guessed.
- Existing manual matches are not modified.

Verification:

```bash
python3 automations/calendar-ics-guardrail/test_calendar_ics_guardrail.py -v
python3 automations/calendar-ics-guardrail/calendar_ics_guardrail.py --dry-run
```
