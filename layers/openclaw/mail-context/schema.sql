-- mail-context schema v3
-- Gmail is authoritative. Everything here is a rebuildable read model.
--
-- HARD RULE: no column in this file stores a message body or attachment
-- content. Retained fields are metadata, labels, and the Gmail snippet only.
-- tests/test_schema.py enforces this against the live schema.
--
-- v3 (2026-08-25) removed `mail_messages.has_attachments`. It was written from
-- the MIME walk in sync.parse_message, which never fires because the sync
-- fetches format=metadata -- so the column read 0 for all 132,588 rows and any
-- query trusting it got a confidently wrong answer with no way to tell.
-- Attachment presence is now DERIVED, three-valued, from evidence that
-- actually exists: see mail_message_attachments below. `store.migrate()` drops
-- the column from databases that still carry it.

CREATE TABLE IF NOT EXISTS mail_threads (
  thread_id        TEXT PRIMARY KEY,
  subject          TEXT,
  participants     TEXT,
  last_message_ts  INTEGER,
  message_count    INTEGER NOT NULL DEFAULT 0,
  synced_at        INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS mail_messages (
  message_id       TEXT PRIMARY KEY,
  thread_id        TEXT NOT NULL REFERENCES mail_threads(thread_id) ON DELETE CASCADE,
  internal_ts      INTEGER NOT NULL,
  synced_at        INTEGER NOT NULL,
  history_id       INTEGER,
  from_addr        TEXT,
  to_addrs         TEXT,
  cc_addrs         TEXT,
  reply_to         TEXT,
  subject          TEXT,
  snippet          TEXT,
  -- No has_attachments here by design; see the v3 note at the top of the file.
  deleted_at       INTEGER
);

CREATE INDEX IF NOT EXISTS mail_messages_thread   ON mail_messages(thread_id);
CREATE INDEX IF NOT EXISTS mail_messages_ts       ON mail_messages(internal_ts DESC);
CREATE INDEX IF NOT EXISTS mail_messages_live     ON mail_messages(internal_ts DESC) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS mail_labels (
  message_id  TEXT NOT NULL REFERENCES mail_messages(message_id) ON DELETE CASCADE,
  label       TEXT NOT NULL,
  PRIMARY KEY (message_id, label)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS mail_labels_label ON mail_labels(label);

-- Filename and MIME type only. Never bytes, never a local path.
CREATE TABLE IF NOT EXISTS mail_attachments (
  message_id  TEXT NOT NULL REFERENCES mail_messages(message_id) ON DELETE CASCADE,
  filename    TEXT,
  mime_type   TEXT,
  size_bytes  INTEGER,
  PRIMARY KEY (message_id, filename, mime_type)
) WITHOUT ROWID;

CREATE VIRTUAL TABLE IF NOT EXISTS mail_search USING fts5(
  subject, snippet, from_addr, to_addrs, cc_addrs,
  content='mail_messages', content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS mail_messages_ai AFTER INSERT ON mail_messages BEGIN
  INSERT INTO mail_search(rowid, subject, snippet, from_addr, to_addrs, cc_addrs)
  VALUES (new.rowid, new.subject, new.snippet, new.from_addr, new.to_addrs, new.cc_addrs);
END;

CREATE TRIGGER IF NOT EXISTS mail_messages_ad AFTER DELETE ON mail_messages BEGIN
  INSERT INTO mail_search(mail_search, rowid, subject, snippet, from_addr, to_addrs, cc_addrs)
  VALUES ('delete', old.rowid, old.subject, old.snippet, old.from_addr, old.to_addrs, old.cc_addrs);
END;

CREATE TRIGGER IF NOT EXISTS mail_messages_au AFTER UPDATE ON mail_messages BEGIN
  INSERT INTO mail_search(mail_search, rowid, subject, snippet, from_addr, to_addrs, cc_addrs)
  VALUES ('delete', old.rowid, old.subject, old.snippet, old.from_addr, old.to_addrs, old.cc_addrs);
  INSERT INTO mail_search(rowid, subject, snippet, from_addr, to_addrs, cc_addrs)
  VALUES (new.rowid, new.subject, new.snippet, new.from_addr, new.to_addrs, new.cc_addrs);
END;

CREATE TABLE IF NOT EXISTS action_candidates (
  candidate_id       INTEGER PRIMARY KEY,
  source_message_id  TEXT NOT NULL REFERENCES mail_messages(message_id) ON DELETE CASCADE,
  source_thread_id   TEXT NOT NULL,
  category           TEXT NOT NULL CHECK (category IN ('reply','appointment','bill','reminder','errand')),
  status             TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','accepted','dismissed','snoozed','needs_review')),
  confidence         REAL,
  extractor          TEXT NOT NULL DEFAULT 'deterministic',
  due_date           TEXT,
  amount             TEXT,
  summary            TEXT,
  snooze_until       INTEGER,
  created_at         INTEGER NOT NULL,
  updated_at         INTEGER NOT NULL,
  UNIQUE (source_message_id, category)
);

CREATE INDEX IF NOT EXISTS action_candidates_status ON action_candidates(status, category);

CREATE TABLE IF NOT EXISTS draft_links (
  draft_id                  TEXT PRIMARY KEY,
  candidate_id              INTEGER REFERENCES action_candidates(candidate_id) ON DELETE SET NULL,
  source_message_id         TEXT NOT NULL,
  thread_id                 TEXT NOT NULL,
  purpose                   TEXT NOT NULL,
  created_by_openclaw       INTEGER NOT NULL DEFAULT 1 CHECK (created_by_openclaw = 1),
  source_message_id_at_creation TEXT NOT NULL,
  state                     TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active','superseded','needs_review','withdrawn')),
  created_at                INTEGER NOT NULL,
  updated_at                INTEGER NOT NULL
);

-- At most one active OpenClaw draft per source message + purpose.
CREATE UNIQUE INDEX IF NOT EXISTS draft_links_active_uniq
  ON draft_links(source_message_id, purpose) WHERE state = 'active';

CREATE TABLE IF NOT EXISTS mail_sync_state (
  id                  INTEGER PRIMARY KEY CHECK (id = 1),
  last_history_id     INTEGER,
  last_success_at     INTEGER,
  last_full_sync_at   INTEGER,
  window_start_ts     INTEGER,
  status              TEXT NOT NULL DEFAULT 'never_run',
  error_class         TEXT,
  -- Whether a full initial load ever finished. Presence of a cursor is NOT a
  -- proxy for this: a bounded or failed load can leave a cursor behind, and
  -- inferring completion from it strands the unfetched remainder forever.
  initial_complete    INTEGER NOT NULL DEFAULT 0
);

INSERT OR IGNORE INTO mail_sync_state (id, status) VALUES (1, 'never_run');

CREATE TABLE IF NOT EXISTS mail_sync_runs (
  run_id        INTEGER PRIMARY KEY,
  kind          TEXT NOT NULL CHECK (kind IN ('initial','incremental','resync','prune')),
  started_at    INTEGER NOT NULL,
  finished_at   INTEGER,
  added         INTEGER NOT NULL DEFAULT 0,
  updated       INTEGER NOT NULL DEFAULT 0,
  deleted       INTEGER NOT NULL DEFAULT 0,
  pruned        INTEGER NOT NULL DEFAULT 0,
  status        TEXT NOT NULL DEFAULT 'running',
  error_class   TEXT
);

CREATE INDEX IF NOT EXISTS mail_sync_runs_started ON mail_sync_runs(started_at DESC);

-- Attachment PRESENCE, sourced from Gmail's search index rather than from
-- message payloads: format=metadata carries no parts, and format=full would
-- drag bodies into this layer. Hints are targeting data -- which messages are
-- worth fetching into the artifact store -- not attachment content.
CREATE TABLE IF NOT EXISTS mail_attachment_hints (
  message_id  TEXT NOT NULL REFERENCES mail_messages(message_id) ON DELETE CASCADE,
  hint        TEXT NOT NULL,
  source      TEXT NOT NULL DEFAULT 'gmail-query',
  synced_at   INTEGER NOT NULL,
  PRIMARY KEY (message_id, hint)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS mail_attachment_hints_hint ON mail_attachment_hints(hint);

-- One row per targeting run. Without this table an empty mail_attachment_hints
-- is ambiguous -- "targeting never ran" and "targeting ran and this mailbox has
-- no attachments" look identical -- and that ambiguity is exactly the bug this
-- schema version exists to kill. A run records what it covered so absence of a
-- hint can be read as evidence only where evidence was actually gathered.
--
-- `covers_presence` is 1 only when the run refreshed the `any` hint
-- (`has:attachment`). A run that refreshed only `pdf` proves nothing about
-- messages with no PDF, so it must not turn 'unknown' into 'no'.
-- `window_start_ts` records what was sent to Gmail; `covered_from_ts` is the
-- conservative floor the state derivation trusts. They differ deliberately:
-- Gmail's `after:` takes a DATE in the user's timezone, we derive that date
-- from a UTC timestamp, and either edge can slip a day. Claiming coverage right
-- up to the boundary would let a boundary-day message read 'no' when it was
-- simply never asked about, so the floor is padded.
CREATE TABLE IF NOT EXISTS mail_targeting_runs (
  run_id          INTEGER PRIMARY KEY,
  started_at      INTEGER NOT NULL,
  finished_at     INTEGER,
  window_start_ts INTEGER,               -- NULL = whole indexed history
  covered_from_ts INTEGER,               -- NULL = whole indexed history
  covers_presence INTEGER NOT NULL DEFAULT 0,
  hints           TEXT,                  -- JSON array of hint names refreshed
  rows_written    INTEGER NOT NULL DEFAULT 0,
  status          TEXT NOT NULL DEFAULT 'running'
                  CHECK (status IN ('running','ok','failed')),
  error_class     TEXT
);

CREATE INDEX IF NOT EXISTS mail_targeting_runs_status
  ON mail_targeting_runs(status, finished_at DESC);

-- How much of the index attachment targeting can speak for.
--
--   covered_from_ts        oldest internal_ts any successful presence run asked
--                          Gmail about (0 = the whole history)
--   covered_until_synced_at newest ingestion moment a run could have seen; a
--                          message synced after the last run was never asked
--                          about, however old its internal_ts is
CREATE VIEW IF NOT EXISTS mail_targeting_coverage AS
  SELECT
    (SELECT COUNT(*) FROM mail_targeting_runs
      WHERE status = 'ok' AND covers_presence = 1)                       AS runs,
    (SELECT COALESCE(MIN(COALESCE(covered_from_ts, 0)), 0)
       FROM mail_targeting_runs
      WHERE status = 'ok' AND covers_presence = 1)                       AS covered_from_ts,
    (SELECT COALESCE(MAX(finished_at), 0) FROM mail_targeting_runs
      WHERE status = 'ok' AND covers_presence = 1)                       AS covered_until_synced_at,
    (SELECT MAX(finished_at) FROM mail_targeting_runs
      WHERE status = 'ok' AND covers_presence = 1)                       AS last_run_at;

-- Attachment presence as a THREE-valued fact. This is the surface callers use;
-- there is no boolean to misread.
--
--   'yes'     a hint or a stored MIME part says this message carries a file
--   'no'      targeting covered this message and Gmail did not list it
--   'unknown' nobody has ever asked Gmail about this message
--
-- 'unknown' is the honest default and, until a targeting run exists, it is the
-- answer for every row.
CREATE VIEW IF NOT EXISTS mail_message_attachments AS
  SELECT m.message_id, m.thread_id, m.internal_ts, m.synced_at, m.deleted_at,
         CASE
           WHEN EXISTS (SELECT 1 FROM mail_attachment_hints h
                         WHERE h.message_id = m.message_id)
             OR EXISTS (SELECT 1 FROM mail_attachments a
                         WHERE a.message_id = m.message_id)          THEN 'yes'
           WHEN c.runs = 0
             OR m.internal_ts < c.covered_from_ts
             OR m.synced_at  > c.covered_until_synced_at              THEN 'unknown'
           ELSE 'no'
         END AS attachment_state
    FROM mail_messages m CROSS JOIN mail_targeting_coverage c;

-- Layer-1 termination rules, keyed by SENDER (master plan cascade table).
--
-- Measured on the 64,200-message corpus: sender is the template key that
-- actually works. 1,163 sender families cover the non-marketing corpus; 41
-- senders cover 50% of it, 154 cover 80%, and only 2% of messages come from a
-- sender seen once. Subject-derived keys fragment (82% singletons) and are not
-- used. So one decision per sender is the cheapest durable unit of judgement,
-- and this table is where those decisions live.
--
-- A rule TERMINATES or PROMOTES; it never merely annotates. 'never' drops
-- every future message from that sender before any API call, model call, or
-- byte is spent. 'always' fetches regardless of the phase-0 sender class.
--
-- `sender` is a normalized address, or '@domain' to cover a whole domain.
-- Address rules win over domain rules.
CREATE TABLE IF NOT EXISTS sender_rules (
  sender      TEXT PRIMARY KEY,
  verdict     TEXT NOT NULL CHECK (verdict IN ('never','always','review')),
  reason      TEXT,
  decided_by  TEXT NOT NULL DEFAULT 'operator',
  decided_at  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS sender_rules_verdict ON sender_rules(verdict);
