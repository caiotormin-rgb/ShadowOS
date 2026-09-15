-- Entity ledger schema. SQLite. Per docs/plans/entity-ledger-prd.md.
--
-- The organising key is the COUNTERPARTY, because that is how Caio searches:
-- "merchants, individual names, sometimes date". Date is a filter on top, not
-- the spine. Every transaction keeps its message_id so the email is one click
-- away, and its run_id so a better extractor supersedes rather than overwrites.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- entities

CREATE TABLE IF NOT EXISTS entities (
  entity_id     INTEGER PRIMARY KEY,
  entity_key    TEXT    NOT NULL UNIQUE,          -- resolver key, e.g. merchant:nubank.com.br
  name          TEXT    NOT NULL,
  kind          TEXT    NOT NULL CHECK (kind IN ('merchant','person','self','platform-sender','unknown')),
  domain        TEXT,
  stream        TEXT,                              -- orders | marketing | ... when a vendor is split
  first_seen    TEXT,                              -- ISO date
  last_seen     TEXT,
  message_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS entities_name ON entities(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS entities_kind ON entities(kind);

-- Every address, display name and merchant spelling that resolves to an
-- entity. This is what makes "acme", "Acme Lawn" and
-- "quickbooks@notification.intuit.com" one answer.
CREATE TABLE IF NOT EXISTS entity_aliases (
  alias_id   INTEGER PRIMARY KEY,
  entity_id  INTEGER NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
  alias      TEXT    NOT NULL,
  alias_type TEXT    NOT NULL CHECK (alias_type IN ('address','display_name','counterparty','domain')),
  UNIQUE (entity_id, alias, alias_type)
);
CREATE INDEX IF NOT EXISTS entity_aliases_alias ON entity_aliases(alias COLLATE NOCASE);

-- A human inside an organisation: "when did Dana last write" resolves to a
-- contact at Hannon De Palma, not to a separate merchant.
CREATE TABLE IF NOT EXISTS contacts (
  contact_id  INTEGER PRIMARY KEY,
  entity_id   INTEGER NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
  person_name TEXT    NOT NULL,
  address     TEXT,
  first_seen  TEXT,
  last_seen   TEXT,
  message_count INTEGER NOT NULL DEFAULT 0,
  UNIQUE (entity_id, person_name, address)
);
CREATE INDEX IF NOT EXISTS contacts_person ON contacts(person_name COLLATE NOCASE);

-- ---------------------------------------------------------------- runs

CREATE TABLE IF NOT EXISTS extraction_runs (
  run_id       INTEGER PRIMARY KEY,
  started_at   TEXT NOT NULL,
  finished_at  TEXT,
  source       TEXT NOT NULL,          -- e.g. 'mc-snap.sqlite snippets'
  lane         TEXT NOT NULL,          -- deterministic | model | mixed
  extractor    TEXT NOT NULL,          -- module/version
  model        TEXT,                   -- null for the deterministic lanes
  messages_in  INTEGER NOT NULL DEFAULT 0,
  rows_out     INTEGER NOT NULL DEFAULT 0,
  notes        TEXT
);

-- Learned per-sender templates. Persisted so they are reusable and
-- inspectable rather than re-derived every run: the unit of cost is one
-- learning per sender, and this table is the asset that buys.
CREATE TABLE IF NOT EXISTS extraction_templates (
  template_id     INTEGER PRIMARY KEY,
  sender          TEXT NOT NULL UNIQUE,
  entity_id       INTEGER REFERENCES entities(entity_id) ON DELETE SET NULL,
  rules_json      TEXT NOT NULL,        -- ordered rule list; see ledger/templates.py
  rule_count      INTEGER NOT NULL DEFAULT 0,
  default_action  TEXT NOT NULL DEFAULT 'terminate'
                  CHECK (default_action IN ('terminate','escalate')),
  learned_by      TEXT NOT NULL,        -- human | model | imported
  learned_at      TEXT NOT NULL,
  run_id          INTEGER REFERENCES extraction_runs(run_id) ON DELETE SET NULL,
  confidence      REAL,                 -- mean confidence of rows this template yields
  messages_seen   INTEGER NOT NULL DEFAULT 0,
  rows_yielded    INTEGER NOT NULL DEFAULT 0,
  sample_message_ids TEXT,              -- JSON array; the evidence the rules were written from
  notes           TEXT
);

-- ---------------------------------------------------------------- transactions

CREATE TABLE IF NOT EXISTS transactions (
  txn_id        INTEGER PRIMARY KEY,
  entity_id     INTEGER NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
  counterparty  TEXT    NOT NULL,       -- the real other side; differs from the
                                        -- entity when a rail sent the mail
                                        -- (PayPal -> Google, QuickBooks -> Acme Lawn)
  date          TEXT    NOT NULL,       -- ISO; the event date, not the email date, when known
  kind          TEXT    NOT NULL CHECK (kind IN
                  -- 'bill' added 2026-08-25: the prototype filed a QuickBooks
                  -- invoice as 'payment' because the enum had nowhere to put a
                  -- request-for-money that has not been paid yet. A bill and a
                  -- payment are different events and both are retrievable.
                  ('purchase','subscription','booking','appointment',
                   'payment','bill','shipment')),
  amount        REAL,
  currency      TEXT CHECK (currency IN ('USD','BRL') OR currency IS NULL),
  ref_number    TEXT,
  service_dates TEXT,                   -- when the thing happens, vs when it was bought
  description   TEXT,
  message_id    TEXT    NOT NULL,
  confidence    REAL    NOT NULL,
  lane          TEXT    NOT NULL,       -- L1 (template) | L2 (generic) | L3 (model)
  rule          TEXT,                   -- sender:rule that produced it
  event_id      INTEGER,                -- rows sharing one real-world event
  link_basis    TEXT CHECK (link_basis IN ('ref','title','counterparty+date','none')),
  is_primary    INTEGER NOT NULL DEFAULT 0,   -- the row chosen to represent the event
  run_id        INTEGER NOT NULL REFERENCES extraction_runs(run_id) ON DELETE CASCADE,
  UNIQUE (message_id, kind, run_id)
);
CREATE INDEX IF NOT EXISTS txn_entity_date ON transactions(entity_id, date DESC);
CREATE INDEX IF NOT EXISTS txn_kind_date   ON transactions(kind, date DESC);
CREATE INDEX IF NOT EXISTS txn_counterpart ON transactions(counterparty COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS txn_ref         ON transactions(ref_number);
-- event_id is assigned by linking over the WHOLE merged row set, so one event's
-- evidence can carry rows from several extraction runs once extraction is
-- incremental (2026-08-25). Scoping this index or the evidence view by run_id
-- would split an order from the shipping notice that arrived a week later.
CREATE INDEX IF NOT EXISTS txn_event       ON transactions(event_id);

-- One row per real-world event: the order confirmation, its shipping notice
-- and its delivery notice collapse to the single row that carries the money.
CREATE VIEW IF NOT EXISTS ledger AS
  SELECT t.txn_id, e.name AS entity, e.kind AS entity_kind, t.counterparty, t.date,
         t.kind, t.amount, t.currency, t.ref_number, t.service_dates,
         t.description, t.confidence, t.lane, t.message_id, t.run_id
  FROM transactions t JOIN entities e USING (entity_id)
  WHERE t.is_primary = 1;

-- Every message that witnessed an event, for "show me the emails behind this".
CREATE VIEW IF NOT EXISTS ledger_evidence AS
  SELECT t.run_id, t.event_id, e.name AS entity, t.counterparty, t.date, t.kind,
         t.amount, t.ref_number, t.link_basis, t.is_primary, t.message_id, t.rule
  FROM transactions t JOIN entities e USING (entity_id)
  ORDER BY t.event_id, t.is_primary DESC;
