-- calendar-context schema v1
-- Google Calendar is authoritative. Everything here is a rebuildable read model.
--
-- HARD RULE 1: no column in this file stores an event description, notes,
-- agenda body, attachment, conference entry point, or join code. Retained
-- fields are scheduling metadata, titles, and locations only.
-- tests/test_schema.py enforces this against the live schema.
--
-- HARD RULE 2: nothing here is a write path. There is no drafts/outbox/pending
-- table because V1 never creates, moves, or RSVPs to anything. The absence is
-- the feature; see calctx/gcal.py and tests/test_no_write_path.py.
--
-- HARD RULE 3 (the one that silently corrupts an agenda if you get it wrong):
-- a timed event is an instant plus an IANA zone; an all-day event is a calendar
-- date and is NOT an instant. A birthday is not 00:00:00Z. The CHECK
-- constraints below make coercion structurally impossible rather than merely
-- discouraged.

CREATE TABLE IF NOT EXISTS cal_calendars (
  calendar_id     TEXT PRIMARY KEY,
  summary         TEXT,
  time_zone       TEXT,                                  -- IANA name, verbatim from calendarList
  selected        INTEGER NOT NULL DEFAULT 0,            -- the user's own Calendar-UI choice is our scope control
  is_primary      INTEGER NOT NULL DEFAULT 0,
  access_role     TEXT,                                  -- recorded for auditing only; never consulted to
                                                         -- decide whether to write, because V1 never writes
  synced_at       INTEGER NOT NULL,
  deleted_at      INTEGER
);

-- Series layer: singles, recurring masters, and single-occurrence overrides.
-- One row per (calendar, event). Google issues ids per calendar, not globally.
CREATE TABLE IF NOT EXISTS cal_events (
  calendar_id           TEXT NOT NULL REFERENCES cal_calendars(calendar_id) ON DELETE CASCADE,
  event_id              TEXT NOT NULL,
  ical_uid              TEXT,
  etag                  TEXT,
  sequence              INTEGER,
  kind                  TEXT NOT NULL CHECK (kind IN ('single','series','override')),
  recurring_event_id    TEXT,                            -- set on overrides and cancellations
  original_start_utc    INTEGER,                         -- which occurrence this override replaces
  original_start_date   TEXT,
  status                TEXT NOT NULL DEFAULT 'confirmed'
                        CHECK (status IN ('confirmed','tentative','cancelled')),
  summary               TEXT,
  location              TEXT,
  organizer_email       TEXT,
  creator_email         TEXT,
  self_response_status  TEXT,                            -- the authenticated user's own RSVP, read-only
  transparency          TEXT,                            -- 'opaque' | 'transparent'
  visibility            TEXT,
  start_kind            TEXT NOT NULL CHECK (start_kind IN ('timed','all_day')),
  start_utc             INTEGER,
  start_tz              TEXT,
  start_date            TEXT,
  end_utc               INTEGER,
  end_tz                TEXT,
  end_date              TEXT,
  recurrence            TEXT,                            -- the RRULE/RDATE/EXRULE/EXDATE array, JSON,
                                                         -- stored verbatim: never normalized or re-serialized
  updated_ts            INTEGER,                         -- Google's `updated`
  materialized_etag     TEXT,                            -- etag at last instance expansion; drives the
                                                         -- initial-load resume skip and nothing else
  materialized_at       INTEGER,
  synced_at             INTEGER NOT NULL,
  deleted_at            INTEGER,
  PRIMARY KEY (calendar_id, event_id),

  -- Exactly one shape. Both or neither is rejected by SQLite, not by review.
  CHECK ((start_kind = 'timed'   AND start_utc IS NOT NULL AND start_tz IS NOT NULL
                                 AND start_date IS NULL)
      OR (start_kind = 'all_day' AND start_date IS NOT NULL AND start_utc IS NULL
                                 AND start_tz IS NULL)),
  CHECK ((start_kind = 'timed'   AND end_utc IS NOT NULL AND end_date IS NULL)
      OR (start_kind = 'all_day' AND end_date IS NOT NULL AND end_utc IS NULL))
);

CREATE INDEX IF NOT EXISTS cal_events_series  ON cal_events(calendar_id, recurring_event_id);
CREATE INDEX IF NOT EXISTS cal_events_kind    ON cal_events(kind);
CREATE INDEX IF NOT EXISTS cal_events_uid     ON cal_events(ical_uid);

-- Attendees are stored because conflict detection has to know which invitations
-- the user declined. They are never logged; see calctx/logs.py.
CREATE TABLE IF NOT EXISTS cal_attendees (
  calendar_id      TEXT NOT NULL,
  event_id         TEXT NOT NULL,
  email            TEXT NOT NULL,
  response_status  TEXT,
  is_self          INTEGER NOT NULL DEFAULT 0,
  optional         INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (calendar_id, event_id, email),
  FOREIGN KEY (calendar_id, event_id) REFERENCES cal_events(calendar_id, event_id) ON DELETE CASCADE
) WITHOUT ROWID;

-- Materialized layer: occurrences inside the window, expanded by Google.
-- Overrides are denormalized here (summary, location, times, RSVP) so an agenda
-- read never has to consult the series row to learn that one occurrence moved.
CREATE TABLE IF NOT EXISTS cal_instances (
  calendar_id           TEXT NOT NULL,
  instance_id           TEXT NOT NULL,
  series_event_id       TEXT NOT NULL,                   -- owning master; equals instance_id for singles
  start_kind            TEXT NOT NULL CHECK (start_kind IN ('timed','all_day')),
  start_utc             INTEGER,
  start_tz              TEXT,
  start_date            TEXT,
  end_utc               INTEGER,
  end_tz                TEXT,
  end_date              TEXT,

  -- ORDERING AID ONLY. For an all-day row this is local midnight in the owning
  -- calendar's timezone, which is a fiction invented so agendas can interleave
  -- dates with instants. It MUST NOT be returned to a caller as an instant, and
  -- every returned row carries start_kind so a caller cannot mistake one for
  -- the other. calctx/query.py strips it.
  order_ts              INTEGER NOT NULL,

  status                TEXT NOT NULL DEFAULT 'confirmed'
                        CHECK (status IN ('confirmed','tentative','cancelled')),
  is_override           INTEGER NOT NULL DEFAULT 0,
  summary               TEXT,
  location              TEXT,
  transparency          TEXT,
  self_response_status  TEXT,
  synced_at             INTEGER NOT NULL,
  PRIMARY KEY (calendar_id, instance_id),
  FOREIGN KEY (calendar_id, series_event_id)
    REFERENCES cal_events(calendar_id, event_id) ON DELETE CASCADE,

  CHECK ((start_kind = 'timed'   AND start_utc IS NOT NULL AND start_tz IS NOT NULL
                                 AND start_date IS NULL)
      OR (start_kind = 'all_day' AND start_date IS NOT NULL AND start_utc IS NULL
                                 AND start_tz IS NULL)),
  CHECK ((start_kind = 'timed'   AND end_utc IS NOT NULL AND end_date IS NULL)
      OR (start_kind = 'all_day' AND end_date IS NOT NULL AND end_utc IS NULL))
);

CREATE INDEX IF NOT EXISTS cal_instances_order  ON cal_instances(order_ts);
CREATE INDEX IF NOT EXISTS cal_instances_series ON cal_instances(calendar_id, series_event_id);
CREATE INDEX IF NOT EXISTS cal_instances_live   ON cal_instances(order_ts) WHERE status != 'cancelled';

CREATE VIRTUAL TABLE IF NOT EXISTS cal_search USING fts5(
  summary, location,
  content='cal_instances', content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS cal_instances_ai AFTER INSERT ON cal_instances BEGIN
  INSERT INTO cal_search(rowid, summary, location) VALUES (new.rowid, new.summary, new.location);
END;

CREATE TRIGGER IF NOT EXISTS cal_instances_ad AFTER DELETE ON cal_instances BEGIN
  INSERT INTO cal_search(cal_search, rowid, summary, location)
  VALUES ('delete', old.rowid, old.summary, old.location);
END;

CREATE TRIGGER IF NOT EXISTS cal_instances_au AFTER UPDATE ON cal_instances BEGIN
  INSERT INTO cal_search(cal_search, rowid, summary, location)
  VALUES ('delete', old.rowid, old.summary, old.location);
  INSERT INTO cal_search(rowid, summary, location) VALUES (new.rowid, new.summary, new.location);
END;

-- One sync cursor per calendar, because Google issues one syncToken per
-- calendar and not one per account. anchor_start_ts/anchor_end_ts are the
-- window bounds of the full sync that MINTED that token: events.list forbids
-- timeMin/timeMax together with syncToken, so the token silently carries the
-- bounds of its originating request. Comparing the desired window against the
-- anchor is the only way to notice that a rolling window has drifted away from
-- a fixed token. See calctx/sync.py.
CREATE TABLE IF NOT EXISTS cal_calendar_sync (
  calendar_id      TEXT PRIMARY KEY REFERENCES cal_calendars(calendar_id) ON DELETE CASCADE,
  sync_token       TEXT,        -- Google's opaque list cursor. NOT a credential:
                                -- it authorizes nothing on its own.
  anchor_start_ts  INTEGER,
  anchor_end_ts    INTEGER,
  last_success_at  INTEGER,
  status           TEXT NOT NULL DEFAULT 'never_run',
  error_class      TEXT
);

CREATE TABLE IF NOT EXISTS cal_sync_state (
  id                 INTEGER PRIMARY KEY CHECK (id = 1),
  last_success_at    INTEGER,
  last_full_sync_at  INTEGER,
  window_start_ts    INTEGER,
  window_end_ts      INTEGER,   -- a calendar window is bounded on BOTH sides,
                                -- unlike mail-context's open-ended forward edge
  status             TEXT NOT NULL DEFAULT 'never_run',
  error_class        TEXT
);

INSERT OR IGNORE INTO cal_sync_state (id, status) VALUES (1, 'never_run');

CREATE TABLE IF NOT EXISTS cal_sync_runs (
  run_id        INTEGER PRIMARY KEY,
  kind          TEXT NOT NULL CHECK (kind IN ('initial','incremental','resync','prune')),
  calendar_id   TEXT,
  started_at    INTEGER NOT NULL,
  finished_at   INTEGER,
  added         INTEGER NOT NULL DEFAULT 0,
  updated       INTEGER NOT NULL DEFAULT 0,
  deleted       INTEGER NOT NULL DEFAULT 0,
  pruned        INTEGER NOT NULL DEFAULT 0,
  instances     INTEGER NOT NULL DEFAULT 0,
  status        TEXT NOT NULL DEFAULT 'running',
  error_class   TEXT
);

CREATE INDEX IF NOT EXISTS cal_sync_runs_started ON cal_sync_runs(started_at DESC);
