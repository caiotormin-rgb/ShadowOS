-- life-index catalog schema v1
--
-- Lives INSIDE the gocryptfs mount, separately from mail-context: that index
-- is a disposable cache of metadata, this holds expensive extractions and
-- real document text (CPFs, RGs, a minor's DOB). Different lifecycle,
-- different privacy posture.
--
-- Filing is metadata, never folders. One artifact is simultaneously
-- 'property', '2025', 'Acme Imoveis' and tier 1, stored once.

CREATE TABLE IF NOT EXISTS artifacts (
  sha256        TEXT PRIMARY KEY,
  bytes         INTEGER NOT NULL,
  mime          TEXT,
  ext           TEXT,
  source        TEXT NOT NULL CHECK (source IN ('manual','gmail','drive')),
  source_ref    TEXT,               -- gmail message_id, drive file id, or original path
  thread_id     TEXT,               -- nullable: 98.8% of mail threads are singletons
  original_name TEXT,
  title         TEXT,
  doc_type      TEXT,               -- contract | statement | iep | receipt | identity | ...
  tier          INTEGER NOT NULL DEFAULT 3 CHECK (tier IN (1,2,3)),
  doc_date      TEXT,               -- ISO date the document is *about*
  correspondent INTEGER REFERENCES correspondents(id) ON DELETE SET NULL,
  supersedes    TEXT REFERENCES artifacts(sha256) ON DELETE SET NULL,
  superseded_by TEXT REFERENCES artifacts(sha256) ON DELETE SET NULL,
  -- Unsigned drafts sit beside executed versions in the same folder. The Drive
  -- sweep found several (Contrato - Alex e Sam, final.doc). Conflating a draft
  -- with the document that was actually signed is worse than not having it.
  executed      INTEGER NOT NULL DEFAULT 0 CHECK (executed IN (0,1)),
  needs_review  INTEGER NOT NULL DEFAULT 0,
  review_reason TEXT,
  created_at    INTEGER NOT NULL,
  updated_at    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS artifacts_tier    ON artifacts(tier, doc_date DESC);
CREATE INDEX IF NOT EXISTS artifacts_type    ON artifacts(doc_type);
CREATE INDEX IF NOT EXISTS artifacts_source  ON artifacts(source, source_ref);
CREATE INDEX IF NOT EXISTS artifacts_review  ON artifacts(needs_review) WHERE needs_review = 1;

-- A counterparty. The SAME concept as ledger.entities, and the two must not
-- drift: "Acme Imoveis" is one party whether the evidence is a contract PDF
-- here or a payment email there.
--
-- They live in separate databases (different lifecycles, different privacy
-- posture), so identity is carried by `entity_key` rather than a foreign key.
-- The key is produced by mail_enrichment.entities.resolve() and looks like
-- 'merchant:acme-imoveis.com.br' or 'person:someone@example.com'. Anything
-- writing a correspondent MUST set it, or cross-store joins silently return
-- nothing and each half looks correct on its own.
CREATE TABLE IF NOT EXISTS correspondents (
  id            INTEGER PRIMARY KEY,
  entity_key    TEXT UNIQUE,
  name          TEXT NOT NULL,
  kind          TEXT NOT NULL DEFAULT 'org' CHECK (kind IN ('person','org')),
  primary_addr  TEXT,
  UNIQUE (name, kind)
);

CREATE INDEX IF NOT EXISTS correspondents_key ON correspondents(entity_key);

CREATE TABLE IF NOT EXISTS artifact_tags (
  sha256  TEXT NOT NULL REFERENCES artifacts(sha256) ON DELETE CASCADE,
  tag     TEXT NOT NULL,
  PRIMARY KEY (sha256, tag)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS artifact_tags_tag ON artifact_tags(tag);

-- The table that makes "what did we agree to pay" answerable rather than
-- merely findable: extracted values, each with its provenance.
CREATE TABLE IF NOT EXISTS artifact_fields (
  sha256      TEXT NOT NULL REFERENCES artifacts(sha256) ON DELETE CASCADE,
  key         TEXT NOT NULL,
  value       TEXT NOT NULL,
  confidence  REAL,
  run_id      INTEGER REFERENCES extraction_runs(run_id) ON DELETE SET NULL,
  PRIMARY KEY (sha256, key, value)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS artifact_fields_key ON artifact_fields(key);

CREATE TABLE IF NOT EXISTS artifact_text (
  sha256      TEXT PRIMARY KEY REFERENCES artifacts(sha256) ON DELETE CASCADE,
  text        TEXT NOT NULL,
  chars       INTEGER NOT NULL,
  pages       INTEGER,
  extractor   TEXT NOT NULL,        -- pdftotext | docling | plain | ...
  extracted_at INTEGER NOT NULL
);

-- A regular (not contentless) FTS5 table: contentless tables cannot return
-- snippet()/highlight(), and a search result without an excerpt is much less
-- useful. The cost is one extra copy of the extracted text, which is small
-- next to the documents themselves.
-- sha256 is UNINDEXED: carried for the join, never searched. Without it the
-- only way back to an artifact was through artifact_text, which meant a
-- document with no stored text had no FTS row at all and was unfindable --
-- exactly the tier-1 pointers (tax forms, ID cards) that the catalog exists to
-- locate. Every artifact gets a row now; text is optional.
CREATE VIRTUAL TABLE IF NOT EXISTS artifact_search USING fts5(
  sha256 UNINDEXED, title, original_name, text,
  tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS extraction_runs (
  run_id      INTEGER PRIMARY KEY,
  lane        INTEGER NOT NULL,     -- 0 deterministic, 1 local, 3 cloud
  model       TEXT,
  version     TEXT,
  started_at  INTEGER NOT NULL,
  finished_at INTEGER,
  items       INTEGER NOT NULL DEFAULT 0
);

-- Provenance for the blob store itself: which file on disk holds the bytes.
CREATE TABLE IF NOT EXISTS artifact_blobs (
  sha256    TEXT PRIMARY KEY REFERENCES artifacts(sha256) ON DELETE CASCADE,
  rel_path  TEXT NOT NULL,
  stored_at INTEGER NOT NULL
);
