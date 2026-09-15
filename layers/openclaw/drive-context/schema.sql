-- drive-context schema v1
-- Google Drive is authoritative. Everything here is a rebuildable read model.
--
-- HARD RULE: no column in this file stores file content in any form -- not
-- bytes, not extracted text, not an export, not a thumbnail, and not a content
-- hash. Checksums are metadata by type but content identifiers by function, so
-- they are absent too. tests/test_schema.py enforces this against the live
-- schema rather than against this comment.
--
-- SECOND HARD RULE: no column stores a grantee email address. Sharing is a
-- state plus counts (see drive_files.sharing_state). Owner email is retained
-- and grantee email is not; that asymmetry is argued in README.md.

CREATE TABLE IF NOT EXISTS drive_files (
  file_id              TEXT PRIMARY KEY,
  name                 TEXT,
  mime_type            TEXT,
  -- Derived from mime_type at parse time: 'folder', 'shortcut' or 'file'.
  -- Stored rather than recomputed so a query can filter on it without knowing
  -- Google's magic MIME strings.
  kind                 TEXT NOT NULL DEFAULT 'file'
                         CHECK (kind IN ('folder', 'shortcut', 'file')),
  created_ts           INTEGER,
  modified_ts          INTEGER,
  -- Google-native documents report no size at all; NULL means "Drive did not
  -- say", never "zero".
  size_bytes           INTEGER,
  trashed              INTEGER NOT NULL DEFAULT 0,
  explicitly_trashed   INTEGER NOT NULL DEFAULT 0,
  starred              INTEGER NOT NULL DEFAULT 0,
  owned_by_me          INTEGER,
  -- A UI deep link that still requires the viewer's own Google login. Any
  -- capability-bearing URL (webContentLink, a published link, a resource key)
  -- is out of scope and has no column here.
  web_view_link        TEXT,
  shortcut_target_id   TEXT,
  shortcut_target_mime TEXT,
  -- 'unknown' is a real and expected state: under drive.metadata.readonly the
  -- permissions collection is invisible for files shared *to* this account, and
  -- the index must say so rather than imply 'private'.
  sharing_state        TEXT NOT NULL DEFAULT 'unknown'
                         CHECK (sharing_state IN ('private', 'shared_with_named',
                                                  'domain', 'anyone_with_link', 'unknown')),
  named_user_count     INTEGER NOT NULL DEFAULT 0,
  group_count          INTEGER NOT NULL DEFAULT 0,
  link_discoverable    INTEGER NOT NULL DEFAULT 0,
  max_role             TEXT
                         CHECK (max_role IS NULL OR
                                max_role IN ('reader', 'commenter', 'writer', 'owner')),
  synced_at            INTEGER NOT NULL,
  -- Which drive_sync_runs.run_id last saw this row. Reconciliation after a full
  -- listing needs "not touched by that run", and a wall clock cannot express it:
  -- two runs in the same second share a timestamp, and NTP can step it backwards.
  -- run_id is monotonic by construction, so the comparison is exact.
  seen_run             INTEGER,
  -- Tombstone. A Drive removal is reversible within the reconciliation grace
  -- period; trashing is a flag above, not a deletion.
  deleted_at           INTEGER
);

CREATE INDEX IF NOT EXISTS drive_files_modified ON drive_files(modified_ts DESC);
CREATE INDEX IF NOT EXISTS drive_files_live     ON drive_files(modified_ts DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS drive_files_kind     ON drive_files(kind);
CREATE INDEX IF NOT EXISTS drive_files_sharing  ON drive_files(sharing_state);

-- Edges, not paths. Drive parents form a DAG in principle and an untrusted
-- graph in practice: multiple parents are legal, a parent may name a folder
-- this account cannot see, and cycles cannot be ruled out. Paths are therefore
-- computed at query time by drivectx.query, bounded and cycle-safe.
--
-- parent_id deliberately has NO foreign key: the edge routinely outlives the
-- node, and a constraint here would reject exactly the data we need to record
-- as 'unresolved-parent'.
CREATE TABLE IF NOT EXISTS drive_parents (
  file_id    TEXT NOT NULL REFERENCES drive_files(file_id) ON DELETE CASCADE,
  parent_id  TEXT NOT NULL,
  PRIMARY KEY (file_id, parent_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS drive_parents_parent ON drive_parents(parent_id);

-- Owner display name and email. One identity per file rather than an unbounded
-- set of third parties, which is why this exists and drive_permissions does not.
CREATE TABLE IF NOT EXISTS drive_owners (
  file_id       TEXT NOT NULL REFERENCES drive_files(file_id) ON DELETE CASCADE,
  display_name  TEXT,
  email_address TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (file_id, email_address)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS drive_owners_email ON drive_owners(email_address);

-- File name only. Indexing anything else would mean holding something else.
CREATE VIRTUAL TABLE IF NOT EXISTS drive_search USING fts5(
  name,
  content='drive_files', content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS drive_files_ai AFTER INSERT ON drive_files BEGIN
  INSERT INTO drive_search(rowid, name) VALUES (new.rowid, new.name);
END;

CREATE TRIGGER IF NOT EXISTS drive_files_ad AFTER DELETE ON drive_files BEGIN
  INSERT INTO drive_search(drive_search, rowid, name) VALUES ('delete', old.rowid, old.name);
END;

CREATE TRIGGER IF NOT EXISTS drive_files_au AFTER UPDATE ON drive_files BEGIN
  INSERT INTO drive_search(drive_search, rowid, name) VALUES ('delete', old.rowid, old.name);
  INSERT INTO drive_search(rowid, name) VALUES (new.rowid, new.name);
END;

CREATE TABLE IF NOT EXISTS drive_sync_state (
  id                 INTEGER PRIMARY KEY CHECK (id = 1),
  -- The Changes API cursor. Captured before the initial listing and written
  -- only after the data transaction commits; see drivectx.sync.
  page_token         TEXT,
  -- My Drive's root folder id, learned once. Path resolution needs it to tell
  -- "reached the top" apart from "parent not in the index".
  root_folder_id     TEXT,
  last_success_at    INTEGER,
  last_full_sync_at  INTEGER,
  status             TEXT NOT NULL DEFAULT 'never_run',
  error_class        TEXT,
  -- Whether a full listing ever finished. Presence of a page token is NOT a
  -- proxy for this: the cursor is captured before the listing starts, so a
  -- bounded validation run or a failed load leaves one behind, and inferring
  -- completion from it would strand the unlisted remainder forever behind
  -- incremental runs that only ever see what changed since.
  initial_complete   INTEGER NOT NULL DEFAULT 0
);

INSERT OR IGNORE INTO drive_sync_state (id, status) VALUES (1, 'never_run');

CREATE TABLE IF NOT EXISTS drive_sync_runs (
  run_id       INTEGER PRIMARY KEY,
  kind         TEXT NOT NULL CHECK (kind IN ('initial', 'incremental', 'resync', 'prune', 'recheck')),
  started_at   INTEGER NOT NULL,
  finished_at  INTEGER,
  added        INTEGER NOT NULL DEFAULT 0,
  updated      INTEGER NOT NULL DEFAULT 0,
  deleted      INTEGER NOT NULL DEFAULT 0,
  pruned       INTEGER NOT NULL DEFAULT 0,
  -- Items dropped for carrying a driveId while include_shared_drives is false.
  excluded     INTEGER NOT NULL DEFAULT 0,
  status       TEXT NOT NULL DEFAULT 'running',
  error_class  TEXT
);

CREATE INDEX IF NOT EXISTS drive_sync_runs_started ON drive_sync_runs(started_at DESC);
