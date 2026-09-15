import type { DatabaseSync } from "node:sqlite";

import { normalizeSender, openStore, type Role } from "./store.js";

export type Principal = {
  personId: number;
  name: string;
  role: Role;
  lang: "en" | "pt";
};

export type Grant = { resource: string; action: string; scope: Record<string, unknown> };

export type AuditEntry = {
  event: string;
  decision?: string;
  reason?: string;
  personId?: number;
  channel?: string;
  senderId?: string;
  agentId?: string;
  tool?: string;
};

/** Raised when the store cannot be opened or read. Every caller treats it as deny. */
export class StoreUnavailable extends Error {}

/**
 * What other household tools import. Every call opens the store fresh, so a
 * store that goes missing or unreadable denies on the very next check.
 */
export class AccessClient {
  constructor(readonly dbPath: string) {}

  private read<T>(fn: (db: DatabaseSync) => T): T {
    let db: DatabaseSync;
    try {
      db = openStore(this.dbPath);
    } catch (error) {
      throw new StoreUnavailable(String(error));
    }
    try {
      return fn(db);
    } catch (error) {
      throw new StoreUnavailable(String(error));
    } finally {
      db.close();
    }
  }

  /** Active person behind a channel identity, or null. Throws StoreUnavailable. */
  resolvePrincipal(channel: string, accountId: string | undefined, senderId: string): Principal | null {
    const sender = normalizeSender(channel, senderId);
    if (!channel || !sender) return null;
    return this.read((db) => {
      // An identity row with an empty account_id matches any account on that channel.
      const row = db
        .prepare(
          `SELECT p.id, p.name, p.role, p.lang FROM identities i JOIN people p ON p.id = i.person_id
           WHERE i.channel = ? AND i.sender_id = ? AND i.account_id IN (?, '') AND p.status = 'active'
           ORDER BY i.account_id DESC LIMIT 1`,
        )
        .get(channel, sender, accountId ?? "") as
        | { id: number; name: string; role: Role; lang: "en" | "pt" }
        | undefined;
      return row ? { personId: row.id, name: row.name, role: row.role, lang: row.lang } : null;
    });
  }

  grants(personId: number): Grant[] {
    return this.read((db) =>
      (
        db
          .prepare(
            `SELECT g.resource, g.action, g.scope_json FROM grants g JOIN people p ON p.id = g.person_id
             WHERE g.person_id = ? AND g.status = 'active' AND p.status = 'active' ORDER BY g.resource, g.action`,
          )
          .all(personId) as Array<{ resource: string; action: string; scope_json: string }>
      ).map((g) => ({ resource: g.resource, action: g.action, scope: JSON.parse(g.scope_json) })),
    );
  }

  /**
   * Deny by default. True only for an active grant whose scope fits ctx
   * (a `self` scope refuses a different subject). Never throws: an
   * unreadable store is a deny.
   */
  can(personId: number, resource: string, action: string, ctx: { subjectPersonId?: number } = {}): boolean {
    try {
      return this.grants(personId).some(
        (g) =>
          g.resource === resource &&
          g.action === action &&
          !(g.scope.self === true && ctx.subjectPersonId !== undefined && ctx.subjectPersonId !== personId),
      );
    } catch {
      return false;
    }
  }

  /** A contact email verified by one-time code, or null. Contact data only, never a credential. */
  verifiedEmail(personId: number): string | null {
    try {
      return this.read((db) => {
        const row = db
          .prepare(
            `SELECT email FROM contact_emails WHERE person_id = ? AND verified_at IS NOT NULL
             ORDER BY verified_at DESC LIMIT 1`,
          )
          .get(personId) as { email: string } | undefined;
        return row?.email ?? null;
      });
    } catch {
      return null;
    }
  }

  /** Active people with their identities, for /access who. Throws StoreUnavailable. */
  roster(): Array<{ name: string; role: Role; identities: string[] }> {
    return this.read((db) =>
      (
        db
          .prepare(
            `SELECT p.name, p.role, group_concat(i.channel || ':' || i.sender_id, ', ') AS ids
             FROM people p LEFT JOIN identities i ON i.person_id = p.id
             WHERE p.status = 'active' GROUP BY p.id ORDER BY p.role DESC, p.name`,
          )
          .all() as Array<{ name: string; role: Role; ids: string | null }>
      ).map((r) => ({ name: r.name, role: r.role, identities: r.ids ? r.ids.split(", ") : [] })),
    );
  }

  /** Append-only audit. No message bodies or health details. Throws StoreUnavailable. */
  audit(entry: AuditEntry): void {
    let db: DatabaseSync;
    try {
      db = openStore(this.dbPath, false);
    } catch (error) {
      throw new StoreUnavailable(String(error));
    }
    try {
      db.prepare(
        `INSERT INTO audit (event, decision, reason, person_id, channel, sender_id, agent_id, tool)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
      ).run(
        entry.event,
        entry.decision ?? null,
        entry.reason ?? null,
        entry.personId ?? null,
        entry.channel ?? null,
        entry.senderId ?? null,
        entry.agentId ?? null,
        entry.tool ?? null,
      );
    } catch (error) {
      throw new StoreUnavailable(String(error));
    } finally {
      db.close();
    }
  }
}
