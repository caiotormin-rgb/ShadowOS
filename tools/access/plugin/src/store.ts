import { chmodSync, closeSync, mkdirSync, openSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { DatabaseSync } from "node:sqlite";

export type Role = "owner" | "member";

export const DEFAULT_DB_PATH = join(homedir(), ".openclaw", "access", "access.sqlite3");

// Journal mode stays DELETE (the default): WAL side files would be created
// with the process umask instead of 0600.
const SCHEMA = `
CREATE TABLE IF NOT EXISTS people (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('owner', 'member')),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('invited', 'active', 'removed')),
  lang TEXT NOT NULL DEFAULT 'en' CHECK (lang IN ('en', 'pt')),
  agent_id TEXT,
  workspace TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TABLE IF NOT EXISTS identities (
  id INTEGER PRIMARY KEY,
  person_id INTEGER NOT NULL REFERENCES people(id),
  channel TEXT NOT NULL,
  account_id TEXT NOT NULL DEFAULT '',
  sender_id TEXT NOT NULL,
  UNIQUE (channel, account_id, sender_id)
);
CREATE TABLE IF NOT EXISTS grants (
  id INTEGER PRIMARY KEY,
  person_id INTEGER NOT NULL REFERENCES people(id),
  resource TEXT NOT NULL,
  action TEXT NOT NULL,
  scope_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TABLE IF NOT EXISTS invites (
  id INTEGER PRIMARY KEY,
  person_id INTEGER NOT NULL REFERENCES people(id),
  created_by INTEGER NOT NULL REFERENCES people(id),
  nonce TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'confirmed', 'cancelled')),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TABLE IF NOT EXISTS contact_emails (
  person_id INTEGER NOT NULL REFERENCES people(id),
  email TEXT NOT NULL,
  verified_at TEXT,
  PRIMARY KEY (person_id, email)
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  event TEXT NOT NULL,
  decision TEXT,
  reason TEXT,
  person_id INTEGER,
  channel TEXT,
  sender_id TEXT,
  agent_id TEXT,
  tool TEXT
);
`;

/** Creates the store (dir 0700, file 0600) if missing and applies the schema. */
export function initStore(path: string): void {
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
  closeSync(openSync(path, "a", 0o600));
  chmodSync(path, 0o600);
  const db = new DatabaseSync(path);
  try {
    db.exec("PRAGMA foreign_keys = ON;");
    db.exec(SCHEMA);
  } finally {
    db.close();
  }
}

/** Opens an existing store. Throws if it is missing or unreadable; callers deny on throw. */
export function openStore(path: string, readOnly = true): DatabaseSync {
  return new DatabaseSync(path, { readOnly });
}

/** WhatsApp ids arrive as "+1555…", "1555…" or "1555…@s.whatsapp.net"; store them as +digits. */
export function normalizeSender(channel: string, senderId: string): string {
  if (channel !== "whatsapp") return senderId.trim();
  const digits = senderId.split("@")[0].replace(/\D/g, "");
  return digits ? `+${digits}` : "";
}
