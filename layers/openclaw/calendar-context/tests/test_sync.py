"""Synchronization: ordering, recovery, isolation, and the recurrence contract."""
from __future__ import annotations

import unittest

from calctx.gcal import CalendarReadOnly, IncompatibleParameters, TransportError
from calctx.store import Store, migrate
from calctx.sync import Syncer, parse_event, recurrence_of
from calctx.timespec import rfc3339, window_bounds
from tests.fixtures import (BEYOND, CAL_ID, LATER, NOW, OTHER_CAL_ID, SOON, day_of,
                            memory_conn, raw_all_day, raw_calendar,
                            raw_cancelled_occurrence, raw_deletion, raw_instance,
                            raw_timed, self_attendee)

FIXED = NOW              # one clock for the whole run, so the window is deterministic
WINDOW = window_bounds(FIXED)


class FakeApi:
    """Stands in for Google Calendar. Records calls so ordering can be asserted."""

    paginate = CalendarReadOnly.paginate

    def __init__(self, calendars=None, events=None, instances=None):
        self.calendars = calendars if calendars is not None else [raw_calendar()]
        self.events: dict[str, list[dict]] = events or {}
        self.full_pages: dict[str, list[dict]] = {}
        self.deltas: dict[str, list[list[dict]]] = {}
        self.instances_map: dict[tuple[str, str], list[dict]] = instances or {}
        self.calls: list[str] = []
        self.list_args: list[tuple] = []
        self.fail_list: dict[str, list] = {}
        self.fail_instances: dict[tuple[str, str], Exception] = {}
        self.emit_sync_token = True
        self.token_seq = 0

    def calendar_list(self, *, page_token=None, max_results=250):
        self.calls.append("calendarList")
        return {"items": list(self.calendars)}

    def list_events(self, calendar_id, *, time_min=None, time_max=None, sync_token=None,
                    page_token=None, max_results=250):
        if sync_token and (time_min or time_max):
            raise IncompatibleParameters("the real client refuses this too")
        self.calls.append(f"list:{calendar_id}:{'sync' if sync_token else 'full'}")
        self.list_args.append((calendar_id, time_min, time_max, sync_token, page_token))
        queue = self.fail_list.get(calendar_id)
        if queue:
            exc = queue.pop(0)
            if exc:
                raise exc
        if sync_token:
            pages = self.deltas.get(calendar_id) or []
            items = pages.pop(0) if pages else []
        elif self.full_pages.get(calendar_id):
            return self.full_pages[calendar_id].pop(0)
        else:
            items = list(self.events.get(calendar_id, []))
        out: dict = {"items": items}
        if self.emit_sync_token:
            self.token_seq += 1
            out["nextSyncToken"] = f"tok-{self.token_seq}"
        return out

    def instances(self, calendar_id, event_id, *, time_min, time_max, page_token=None,
                  max_results=250):
        self.calls.append(f"instances:{calendar_id}:{event_id}")
        exc = self.fail_instances.get((calendar_id, event_id))
        if exc:
            raise exc
        return {"items": list(self.instances_map.get((calendar_id, event_id), []))}


class SyncTestBase(unittest.TestCase):
    def setUp(self):
        self.conn = memory_conn()
        migrate(self.conn)
        self.store = Store(self.conn)

    def syncer(self, api, **kw):
        kw.setdefault("concurrency", 1)
        return Syncer(self.store, api, now=FIXED, **kw)

    def instance_ids(self):
        return [r[0] for r in self.conn.execute(
            "SELECT instance_id FROM cal_instances ORDER BY order_ts, instance_id")]

    def cursor(self, calendar_id=CAL_ID):
        return self.store.calendar_cursor(calendar_id)


# -- parsing --------------------------------------------------------------

class ParseTest(unittest.TestCase):
    def test_a_recurring_master_is_a_series_and_keeps_its_rule(self):
        ev = parse_event(raw_timed("s1", recurrence=["RRULE:FREQ=WEEKLY;BYDAY=MO"]),
                         calendar_id=CAL_ID, calendar_tz="UTC")
        self.assertEqual(ev.kind, "series")
        self.assertEqual(ev.recurrence, ("RRULE:FREQ=WEEKLY;BYDAY=MO",))

    def test_an_event_with_a_parent_is_an_override(self):
        ev = parse_event(raw_timed("s1_ovr", recurring_event_id="s1", original_start=SOON),
                         calendar_id=CAL_ID, calendar_tz="UTC")
        self.assertEqual(ev.kind, "override")
        self.assertEqual(ev.recurring_event_id, "s1")
        self.assertIsNotNone(ev.original_start)

    def test_a_plain_deletion_parses_as_none(self):
        self.assertIsNone(parse_event(raw_deletion("e1"), calendar_id=CAL_ID, calendar_tz="UTC"))

    def test_a_cancelled_occurrence_keeps_its_original_start(self):
        """Not a deletion: it must remain distinguishable from 'never existed'."""
        ev = parse_event(raw_cancelled_occurrence("s1_x", master="s1", original=SOON),
                         calendar_id=CAL_ID, calendar_tz="UTC")
        self.assertIsNotNone(ev)
        self.assertEqual(ev.status, "cancelled")
        self.assertEqual(ev.kind, "override")
        self.assertEqual(ev.start.utc, SOON)

    def test_all_day_end_date_is_stored_inclusive(self):
        ev = parse_event(raw_all_day("b1", day="2026-08-23", days=1),
                         calendar_id=CAL_ID, calendar_tz="UTC")
        self.assertEqual((ev.start.date, ev.end.date), ("2026-08-23", "2026-08-23"))
        self.assertIsNone(ev.start.utc)

    def test_self_response_status_is_extracted(self):
        ev = parse_event(raw_timed("e1", attendees=[self_attendee("declined")]),
                         calendar_id=CAL_ID, calendar_tz="UTC")
        self.assertEqual(ev.self_response_status, "declined")


# -- initial load ---------------------------------------------------------

class InitialSyncTest(SyncTestBase):
    def test_only_selected_calendars_are_indexed(self):
        api = FakeApi(calendars=[raw_calendar(CAL_ID),
                                 raw_calendar(OTHER_CAL_ID, selected=False, primary=False)],
                      events={CAL_ID: [raw_timed("e1")], OTHER_CAL_ID: [raw_timed("z1")]})
        self.syncer(api).sync()
        self.assertIn(f"list:{CAL_ID}:full", api.calls)
        self.assertNotIn(f"list:{OTHER_CAL_ID}:full", api.calls)

    def test_calendar_list_comes_before_any_event_listing(self):
        api = FakeApi(events={CAL_ID: [raw_timed("e1")]})
        self.syncer(api).sync()
        self.assertEqual(api.calls[0], "calendarList",
                         "a calendar's timezone must be known before its events are parsed")

    def test_the_anchor_is_exactly_the_window_the_listing_requested(self):
        """The token silently carries the bounds of the request that minted it.
        Recomputing the anchor afterwards would record bounds never requested."""
        api = FakeApi(events={CAL_ID: [raw_timed("e1")]})
        self.syncer(api).sync()
        _cid, time_min, time_max, sync_token, _pt = api.list_args[0]
        self.assertIsNone(sync_token, "an initial listing is bounded, not tokenized")
        self.assertEqual((time_min, time_max), (rfc3339(WINDOW[0]), rfc3339(WINDOW[1])))
        cur = self.cursor()
        self.assertEqual((cur["anchor_start_ts"], cur["anchor_end_ts"]), WINDOW)

    def test_the_token_is_stored_only_after_the_data_commits(self):
        """A crash between the two must leave the previous token in place, so
        the next run replays -- which is safe because every write is idempotent."""
        api = FakeApi()
        api.full_pages[CAL_ID] = [{"items": [raw_timed("e1")], "nextPageToken": "p2"}]
        api.fail_list[CAL_ID] = [None, TransportError("network")]
        res = self.syncer(api).sync()
        self.assertEqual(res.status, "error")
        self.assertIsNotNone(self.store.event_row(CAL_ID, "e1"), "page one did commit")
        cur = self.cursor()
        self.assertTrue(cur is None or cur["sync_token"] is None,
                        "no token may be stored for a listing that never finished")

    def test_a_single_event_is_materialized_without_an_api_call(self):
        api = FakeApi(events={CAL_ID: [raw_timed("e1")]})
        self.syncer(api).sync()
        self.assertEqual(self.instance_ids(), ["e1"])
        self.assertEqual([c for c in api.calls if c.startswith("instances:")], [],
                         "expanding a series of one is pure latency")

    def test_a_recurring_series_is_expanded_by_google(self):
        api = FakeApi(events={CAL_ID: [raw_timed("s1", recurrence=["RRULE:FREQ=DAILY"])]},
                      instances={(CAL_ID, "s1"): [raw_instance("s1_0", start=SOON),
                                                  raw_instance("s1_1", start=SOON + 86400)]})
        self.syncer(api).sync()
        self.assertEqual(self.instance_ids(), ["s1_0", "s1_1"])
        self.assertEqual(recurrence_of(self.store.event_row(CAL_ID, "s1")),
                         ["RRULE:FREQ=DAILY"])

    def test_an_all_day_event_never_becomes_an_instant(self):
        day = day_of(SOON)
        api = FakeApi(events={CAL_ID: [raw_all_day("b1", day=day)]})
        self.syncer(api).sync()
        row = self.conn.execute("SELECT * FROM cal_instances WHERE instance_id='b1'").fetchone()
        self.assertEqual(row["start_kind"], "all_day")
        self.assertIsNone(row["start_utc"])
        self.assertEqual(row["start_date"], day)

    def test_an_override_is_denormalized_onto_its_occurrence(self):
        """An agenda read must never consult the series row to learn one
        occurrence moved."""
        api = FakeApi(
            events={CAL_ID: [raw_timed("s1", summary="Standup", recurrence=["RRULE:FREQ=DAILY"]),
                             raw_timed("s1_ovr", summary="Standup (moved)",
                                       start=SOON + 5400, recurring_event_id="s1",
                                       original_start=SOON + 86400)]},
            instances={(CAL_ID, "s1"): [
                raw_instance("s1_0", start=SOON, summary="Standup"),
                raw_instance("s1_ovr", start=SOON + 5400, summary="Standup (moved)")]})
        self.syncer(api).sync()
        row = self.conn.execute(
            "SELECT * FROM cal_instances WHERE instance_id='s1_ovr'").fetchone()
        self.assertEqual(row["summary"], "Standup (moved)")
        self.assertEqual(row["is_override"], 1)
        self.assertEqual(row["start_utc"], SOON + 5400)

    def test_an_override_is_not_expanded_on_its_own(self):
        api = FakeApi(
            events={CAL_ID: [raw_timed("s1", recurrence=["RRULE:FREQ=DAILY"]),
                             raw_timed("s1_ovr", start=SOON + 5400, recurring_event_id="s1",
                                       original_start=SOON + 86400)]},
            instances={(CAL_ID, "s1"): [raw_instance("s1_0", start=SOON)]})
        self.syncer(api).sync()
        self.assertNotIn(f"instances:{CAL_ID}:s1_ovr", api.calls,
                         "an override has no occurrences of its own")

    def test_a_malformed_event_does_not_abort_the_calendar(self):
        bad = raw_timed("bad")
        bad["start"] = {"dateTime": "not-a-time"}
        api = FakeApi(events={CAL_ID: [bad, raw_timed("good")]})
        res = self.syncer(api).sync()
        self.assertEqual(res.status, "ok")
        self.assertIsNotNone(self.store.event_row(CAL_ID, "good"))
        self.assertIsNone(self.store.event_row(CAL_ID, "bad"))

    def test_events_outside_the_window_are_pruned_on_the_same_run(self):
        api = FakeApi(events={CAL_ID: [raw_timed("near", start=SOON),
                                       raw_timed("far", start=BEYOND)]})
        res = self.syncer(api).sync()
        self.assertEqual(res.pruned, 1)
        self.assertEqual(self.instance_ids(), ["near"])


# -- incremental ----------------------------------------------------------

class IncrementalSyncTest(SyncTestBase):
    def bootstrap(self, api):
        self.syncer(api).sync()
        api.calls.clear()
        api.list_args.clear()
        return self.syncer(api)

    def test_incremental_sends_the_token_and_no_time_window(self):
        api = FakeApi(events={CAL_ID: [raw_timed("e1")]})
        self.bootstrap(api).sync()
        _cid, time_min, time_max, sync_token, _pt = api.list_args[0]
        self.assertEqual(sync_token, "tok-1")
        self.assertIsNone(time_min)
        self.assertIsNone(time_max)

    def test_incremental_keeps_the_original_anchor(self):
        """The new token still descends from the bounds of the sync that first
        minted one; moving the anchor here would hide real drift."""
        api = FakeApi(events={CAL_ID: [raw_timed("e1")]})
        syncer = self.bootstrap(api)
        before = dict(self.cursor())
        syncer.sync()
        after = self.cursor()
        self.assertNotEqual(after["sync_token"], before["sync_token"])
        self.assertEqual((after["anchor_start_ts"], after["anchor_end_ts"]),
                         (before["anchor_start_ts"], before["anchor_end_ts"]))

    def test_a_changed_series_is_refetched_even_though_it_is_already_stored(self):
        """The resume optimisation must not apply to an incremental run: the
        stored expansion is exactly the stale one. Here the master's etag never
        changes -- only a cancellation for one occurrence arrives -- which is
        precisely the case an etag-based skip would get wrong.
        """
        api = FakeApi(events={CAL_ID: [raw_timed("s1", recurrence=["RRULE:FREQ=DAILY"])]},
                      instances={(CAL_ID, "s1"): [raw_instance("s1_0", start=SOON),
                                                  raw_instance("s1_1", start=SOON + 86400)]})
        syncer = self.bootstrap(api)
        api.deltas[CAL_ID] = [[raw_cancelled_occurrence("s1_1", master="s1",
                                                        original=SOON + 86400)]]
        api.instances_map[(CAL_ID, "s1")] = [
            raw_instance("s1_0", start=SOON),
            raw_instance("s1_1", start=SOON + 86400, status="cancelled")]
        syncer.sync()
        self.assertIn(f"instances:{CAL_ID}:s1", api.calls,
                      "a changed series must be re-expanded")
        row = self.conn.execute(
            "SELECT status FROM cal_instances WHERE instance_id='s1_1'").fetchone()
        self.assertEqual(row["status"], "cancelled")

    def test_a_cancelled_occurrence_is_kept_not_deleted(self):
        api = FakeApi(events={CAL_ID: [raw_timed("s1", recurrence=["RRULE:FREQ=DAILY"])]},
                      instances={(CAL_ID, "s1"): [raw_instance("s1_0", start=SOON)]})
        syncer = self.bootstrap(api)
        api.deltas[CAL_ID] = [[raw_cancelled_occurrence("s1_0", master="s1", original=SOON)]]
        api.instances_map[(CAL_ID, "s1")] = [
            raw_instance("s1_0", start=SOON, status="cancelled")]
        syncer.sync()
        self.assertEqual(self.instance_ids(), ["s1_0"])
        self.assertEqual(self.conn.execute(
            "SELECT status FROM cal_instances WHERE instance_id='s1_0'").fetchone()[0],
            "cancelled")

    def test_a_deleted_event_is_tombstoned_and_loses_its_occurrences(self):
        api = FakeApi(events={CAL_ID: [raw_timed("e1")]})
        syncer = self.bootstrap(api)
        api.deltas[CAL_ID] = [[raw_deletion("e1")]]
        res = syncer.sync()
        self.assertEqual(res.deleted, 1)
        self.assertEqual(self.instance_ids(), [])
        self.assertIsNotNone(self.store.event_row(CAL_ID, "e1")["deleted_at"])

    def test_a_second_run_that_changed_nothing_is_a_no_op(self):
        api = FakeApi(events={CAL_ID: [raw_timed("e1")]})
        syncer = self.bootstrap(api)
        res = syncer.sync()
        self.assertEqual((res.added, res.updated, res.deleted), (0, 0, 0))
        self.assertEqual(self.instance_ids(), ["e1"])


# -- recovery and drift ---------------------------------------------------

class RecoveryTest(SyncTestBase):
    def test_410_gone_falls_back_to_a_bounded_resync(self):
        """An expired syncToken is documented operation, not a dead index."""
        api = FakeApi(events={CAL_ID: [raw_timed("e1")]})
        self.syncer(api).sync()
        api.calls.clear()
        api.list_args.clear()
        api.fail_list[CAL_ID] = [TransportError("gone", 410)]
        res = self.syncer(api).sync()
        self.assertTrue(res.resynced)
        self.assertEqual(res.status, "ok")
        self.assertEqual([a[3] for a in api.list_args], ["tok-1", None],
                         "the dead token is tried once, then the retry is a bounded "
                         "full listing rather than another token call")
        self.assertIsNotNone(self.cursor()["sync_token"], "a fresh token replaced the dead one")

    def test_410_recovery_preserves_the_indexed_data(self):
        api = FakeApi(events={CAL_ID: [raw_timed("e1")]})
        self.syncer(api).sync()
        api.fail_list[CAL_ID] = [TransportError("gone", 410)]
        self.syncer(api).sync()
        self.assertEqual(self.instance_ids(), ["e1"])

    def test_window_drift_beyond_a_month_forces_a_reanchor(self):
        """A rolling window over a fixed anchor goes quietly wrong: the token
        keeps delivering changes for last month's window."""
        api = FakeApi(events={CAL_ID: [raw_timed("e1")]})
        self.syncer(api).sync()
        stale = WINDOW[0] - 60 * 86400
        self.conn.execute(
            "UPDATE cal_calendar_sync SET anchor_start_ts = ?, anchor_end_ts = ?",
            (stale, WINDOW[1] - 60 * 86400))
        api.calls.clear()
        api.list_args.clear()
        res = self.syncer(api).sync()
        self.assertTrue(res.resynced)
        self.assertIsNone(api.list_args[0][3], "a re-anchor is a bounded full listing")
        cur = self.cursor()
        self.assertEqual((cur["anchor_start_ts"], cur["anchor_end_ts"]), WINDOW)

    def test_drift_within_a_month_stays_incremental(self):
        api = FakeApi(events={CAL_ID: [raw_timed("e1")]})
        self.syncer(api).sync()
        self.conn.execute(
            "UPDATE cal_calendar_sync SET anchor_start_ts = ?, anchor_end_ts = ?",
            (WINDOW[0] - 5 * 86400, WINDOW[1] - 5 * 86400))
        api.list_args.clear()
        res = self.syncer(api).sync()
        self.assertFalse(res.resynced)
        self.assertIsNotNone(api.list_args[0][3], "a small drift is not worth a full resync")

    def test_an_interrupted_initial_load_resumes_without_re_expanding(self):
        """The resume skip exists for a long first load and applies only there."""
        api = FakeApi(events={CAL_ID: [raw_timed("s1", recurrence=["RRULE:FREQ=DAILY"])]},
                      instances={(CAL_ID, "s1"): [raw_instance("s1_0", start=SOON)]})
        api.emit_sync_token = False        # crashed before Google issued a token
        self.syncer(api).sync()
        self.assertIn(f"instances:{CAL_ID}:s1", api.calls)
        api.calls.clear()
        res = self.syncer(api).sync()      # still 'initial': no token was stored
        self.assertEqual(res.skipped, 1)
        self.assertNotIn(f"instances:{CAL_ID}:s1", api.calls,
                         "an unchanged series must not be re-expanded on resume")

    def test_per_calendar_failure_is_isolated(self):
        api = FakeApi(calendars=[raw_calendar(CAL_ID),
                                 raw_calendar(OTHER_CAL_ID, primary=False, summary="Team")],
                      events={CAL_ID: [raw_timed("e1")], OTHER_CAL_ID: [raw_timed("z1")]})
        api.fail_list[OTHER_CAL_ID] = [TransportError("forbidden_or_quota", 403)]
        res = self.syncer(api).sync()
        self.assertEqual(res.status, "partial")
        self.assertEqual(self.instance_ids(), ["e1"])
        self.assertEqual(self.cursor(OTHER_CAL_ID)["error_class"], "forbidden_or_quota")
        self.assertEqual(self.cursor(CAL_ID)["status"], "ok")

    def test_every_calendar_failing_is_an_error_not_an_empty_calendar(self):
        api = FakeApi(events={CAL_ID: [raw_timed("e1")]})
        api.fail_list[CAL_ID] = [TransportError("auth", 401)]
        res = self.syncer(api).sync()
        self.assertEqual((res.status, res.error_class), ("error", "auth"))
        self.assertEqual(
            self.conn.execute("SELECT status FROM cal_sync_state").fetchone()[0], "error")

    def test_a_failed_run_preserves_the_last_good_index(self):
        api = FakeApi(events={CAL_ID: [raw_timed("e1")]})
        self.syncer(api).sync()
        api.fail_list[CAL_ID] = [TransportError("network")]
        self.syncer(api).sync()
        self.assertEqual(self.instance_ids(), ["e1"])
        self.assertIsNotNone(self.cursor()["sync_token"])

    def test_calendar_list_failure_records_a_class_and_stops_cleanly(self):
        api = FakeApi()

        def boom(*a, **kw):
            raise TransportError("auth", 401)

        api.calendar_list = boom
        res = self.syncer(api).sync()
        self.assertEqual((res.status, res.error_class), ("error", "auth"))

    def test_an_unsubscribed_calendar_leaves_scope(self):
        api = FakeApi(calendars=[raw_calendar(CAL_ID),
                                 raw_calendar(OTHER_CAL_ID, primary=False)],
                      events={CAL_ID: [raw_timed("e1")], OTHER_CAL_ID: [raw_timed("z1")]})
        self.syncer(api).sync()
        api.calendars = [raw_calendar(CAL_ID)]
        api.calls.clear()
        self.syncer(api).sync()
        self.assertNotIn(f"list:{OTHER_CAL_ID}:sync", api.calls)

    def test_concurrency_writes_only_on_the_calling_thread(self):
        """The SQLite connection is not thread-safe; workers may only fetch."""
        events = [raw_timed(f"s{i}", recurrence=["RRULE:FREQ=DAILY"]) for i in range(6)]
        api = FakeApi(events={CAL_ID: events},
                      instances={(CAL_ID, f"s{i}"): [raw_instance(f"s{i}_0", start=SOON + i * 60)]
                                 for i in range(6)})
        res = self.syncer(api, concurrency=4).sync()
        self.assertEqual(res.status, "ok")
        self.assertEqual(len(self.instance_ids()), 6)


if __name__ == "__main__":
    unittest.main()
