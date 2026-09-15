import { chmodSync, mkdtempSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AccessClient } from "./client.js";
import { accessCommand, meCommand, type CommandCtx } from "./commands.js";
import entry from "./index.js";
import { decide, enforce, monitor } from "./policy.js";
import { initStore } from "./store.js";

// Fictional identities only.
const OWNER_WA = "+15550100001";
const OWNER_TG = "900000001";
const MEMBER_WA = "+5511900000001";
const MEMBER2_WA = "+15550100002";
const STRANGER_WA = "+15550100099";

let dir: string;
let dbPath: string;
let client: AccessClient;

function seed(path: string) {
  initStore(path);
  const db = new DatabaseSync(path);
  db.exec(`
    INSERT INTO people (id, name, role, lang) VALUES (1, 'Owner Test', 'owner', 'en'),
      (2, 'Membro Teste', 'member', 'pt'), (3, 'Member Two', 'member', 'en');
    INSERT INTO identities (person_id, channel, account_id, sender_id) VALUES
      (1, 'whatsapp', 'tools', '${OWNER_WA}'), (1, 'telegram', '', '${OWNER_TG}'),
      (2, 'whatsapp', 'tools', '${MEMBER_WA}'), (3, 'whatsapp', 'tools', '${MEMBER2_WA}');
    INSERT INTO grants (person_id, resource, action, scope_json) VALUES
      (2, 'grocery', 'use', '{"household":true}'), (2, 'doctor', 'request', '{"self":true}'),
      (3, 'doctor', 'request', '{"self":true}');
    INSERT INTO contact_emails (person_id, email, verified_at) VALUES
      (3, 'member2@example.test', '2026-09-01T00:00:00Z'), (2, 'unverified@example.test', NULL);
  `);
  db.close();
}

function auditRows(): Array<Record<string, unknown>> {
  const db = new DatabaseSync(dbPath, { readOnly: true });
  try {
    return db.prepare("SELECT event, decision, reason, sender_id, tool FROM audit ORDER BY id").all() as never;
  } finally {
    db.close();
  }
}

const wa = (senderId: string, extra: Partial<CommandCtx> = {}): CommandCtx => ({
  channel: "whatsapp",
  accountId: "tools",
  senderId,
  ...extra,
});

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "access-test-"));
  dbPath = join(dir, "access", "access.sqlite3");
  seed(dbPath);
  client = new AccessClient(dbPath);
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe("store", () => {
  it("is created with mode 0600", () => {
    expect(statSync(dbPath).mode & 0o777).toBe(0o600);
  });
});

describe("AccessClient", () => {
  it("resolves identities, normalizing WhatsApp ids", () => {
    expect(client.resolvePrincipal("whatsapp", "tools", "5511900000001@s.whatsapp.net")?.personId).toBe(2);
    expect(client.resolvePrincipal("telegram", "default", OWNER_TG)?.role).toBe("owner");
    expect(client.resolvePrincipal("whatsapp", "other-account", MEMBER_WA)).toBeNull();
    expect(client.resolvePrincipal("whatsapp", "tools", STRANGER_WA)).toBeNull();
  });

  it("denies by default and respects self scope", () => {
    expect(client.can(2, "grocery", "use")).toBe(true);
    expect(client.can(2, "grocery", "admin")).toBe(false);
    expect(client.can(3, "grocery", "use")).toBe(false);
    expect(client.can(2, "doctor", "request", { subjectPersonId: 2 })).toBe(true);
    expect(client.can(2, "doctor", "request", { subjectPersonId: 3 })).toBe(false);
    expect(client.can(999, "grocery", "use")).toBe(false);
  });

  it("returns only verified contact emails", () => {
    expect(client.verifiedEmail(3)).toBe("member2@example.test");
    expect(client.verifiedEmail(2)).toBeNull();
  });
});

describe("store unreadable -> deny", () => {
  it.each([
    ["missing", () => rmSync(dbPath)],
    ["corrupt", () => { rmSync(dbPath); require("node:fs").writeFileSync(dbPath, "not sqlite"); }],
    ["no permission", () => chmodSync(dbPath, 0o000)],
  ])("%s store", (_label, breakStore) => {
    breakStore();
    // root ignores file modes; the permission case is only meaningful otherwise.
    if (_label === "no permission" && process.getuid?.() === 0) return;
    expect(client.can(2, "grocery", "use")).toBe(false);
    expect(decide(client, "grocery_list", "shared-tools", { channel: "whatsapp", accountId: "tools", senderId: MEMBER_WA })).toMatchObject({
      allow: false,
      reason: "store_unreadable",
    });
    expect(accessCommand(client, wa(OWNER_WA, { senderIsOwner: true })).text).toMatch(/unavailable/);
    expect(meCommand(client, wa(MEMBER_WA)).text).toMatch(/unavailable|indispon/);
  });
});

describe("tool policy (monitor mode)", () => {
  const logger = () => ({ warn: vi.fn() });

  it("unknown sender produces an audited would-deny", () => {
    const log = logger();
    const d = monitor(client, log, "grocery_list", "shared-tools", { channel: "whatsapp", accountId: "tools", senderId: STRANGER_WA });
    expect(d).toMatchObject({ allow: false, reason: "unknown_sender" });
    expect(log.warn).toHaveBeenCalledWith(expect.stringContaining("would-deny"));
    expect(auditRows()).toEqual([
      { event: "tool.would_deny", decision: "would_deny", reason: "unknown_sender", sender_id: STRANGER_WA, tool: "grocery_list" },
    ]);
  });

  it("covers the other deny reasons and allows granted members and the owner", () => {
    const member = { channel: "whatsapp", accountId: "tools", senderId: MEMBER_WA };
    expect(decide(client, "grocery_list", "shared-tools", undefined).reason).toBe("no_requester");
    expect(decide(client, "grocery_list", "shared-tools", member)).toMatchObject({ allow: true, reason: "grant" });
    expect(decide(client, "exec", "shared-tools", member).reason).toBe("unmapped_tool");
    expect(decide(client, "grocery_list", "main", member).reason).toBe("main_requires_owner");
    expect(decide(client, "grocery_list", "shared-tools", { ...member, senderId: MEMBER2_WA }).reason).toBe("no_grant");
    // Store role owner alone is not enough: the host must also say senderIsOwner.
    const owner = { channel: "whatsapp", accountId: "tools", senderId: OWNER_WA };
    expect(decide(client, "exec", "main", owner).reason).toBe("main_requires_owner");
    expect(decide(client, "exec", "main", { ...owner, senderIsOwner: true })).toMatchObject({ allow: true });
  });

  it("is registered as a trusted policy that never blocks", async () => {
    const policies: Array<{ evaluate: (e: unknown, c: unknown) => unknown }> = [];
    const commands: Array<{ name: string; requiredScopes?: string[]; requireAuth?: boolean }> = [];
    entry.register({
      pluginConfig: { dbPath },
      logger: { warn: vi.fn(), info: vi.fn(), error: vi.fn(), debug: vi.fn() },
      registerTrustedToolPolicy: (p: never) => policies.push(p),
      registerCommand: (c: never) => commands.push(c),
    } as never);
    expect(commands.map((c) => c.name)).toEqual(["access", "me"]);
    // Without requiredScopes the host never passes senderIsOwner, and /access would refuse everyone.
    expect(commands[0]).toMatchObject({ requiredScopes: ["operator.admin"], requireAuth: true });
    const result = await policies[0].evaluate({ toolName: "exec", params: {} }, { toolName: "exec", agentId: "main" });
    expect(result).toBeUndefined();
    expect(auditRows().at(-1)).toMatchObject({ reason: "no_requester", tool: "exec" });
  });
});

describe("/access", () => {
  const subcommands = ["who", "quem", "invite +15550100003 grocery", "grant x y", "revoke x y", "remove x", "log", "confirm abc", ""];

  it("member cannot run any /access admin command, even if the host marks them owner", () => {
    for (const args of subcommands) {
      for (const senderIsOwner of [undefined, false, true]) {
        const reply = accessCommand(client, wa(MEMBER_WA, { args, senderIsOwner }));
        expect(reply.text, `/access ${args}`).toBe("Somente o administrador da casa pode usar /access.");
        expect(reply.text).not.toContain("Owner Test");
      }
    }
    expect(auditRows().every((r) => r.event === "command.denied" && r.reason === "not_owner")).toBe(true);
  });

  it("unknown senders are refused bilingually and audited", () => {
    const reply = accessCommand(client, wa(STRANGER_WA, { args: "who", senderIsOwner: true }));
    expect(reply.text).toContain("Only the household admin");
    expect(reply.text).toContain("Somente o administrador");
    expect(auditRows()).toMatchObject([{ event: "command.denied", reason: "unknown_sender" }]);
  });

  it("owner needs both store role and host owner status", () => {
    expect(accessCommand(client, wa(OWNER_WA, { args: "who" })).text).toMatch(/Only the household admin/);
    const who = accessCommand(client, wa(OWNER_WA, { args: "who", senderIsOwner: true })).text;
    expect(who).toContain("Household members:");
    expect(who).toContain(`Membro Teste (member): whatsapp:${MEMBER_WA}`);
    expect(accessCommand(client, { channel: "telegram", senderId: OWNER_TG, args: "who", senderIsOwner: true }).text).toContain("Owner Test (admin)");
  });
});

describe("/me", () => {
  it("shows only the sender's own data, in their language", () => {
    const me = meCommand(client, wa(MEMBER_WA)).text;
    expect(me).toBe("Membro Teste\nPapel: membro\nAcesso: doctor.request, grocery.use\nEmail verificado: nenhum");
    for (const other of ["Owner Test", "Member Two", OWNER_WA, MEMBER2_WA, "member2@example.test", "unverified@example.test"]) {
      expect(me).not.toContain(other);
    }
    const me2 = meCommand(client, wa(MEMBER2_WA)).text;
    expect(me2).toBe("Member Two\nRole: member\nAccess: doctor.request\nVerified email: member2@example.test");
  });

  it("unknown sender learns nothing", () => {
    expect(meCommand(client, wa(STRANGER_WA)).text).toBe(
      "I don't have you on the household list yet.\nVocê ainda não está na lista da casa.",
    );
  });
});


describe("scoped enforcement", () => {
  const member = { channel: "whatsapp", accountId: "tools", senderId: MEMBER_WA };
  it.each(["grocery_show", "grocery_add", "grocery_mark", "grocery_remove", "grocery_trip", "grocery_activity", "grocery_preferences"])("maps %s to the existing grocery grant", (name) => {
    expect(decide(client, name, "shared-tools", member)).toMatchObject({allow: true});
    expect(decide(client, name, "shared-tools", {...member, senderId: MEMBER2_WA})).toMatchObject({allow: false, reason: "no_grant"});
  });
  it("blocks missing identity and audits denials instead of would-deny", () => {
    expect(enforce(client, {warn: vi.fn()}, "grocery_add", "shared-tools", undefined)).toMatchObject({allow: false});
    expect(auditRows().at(-1)).toMatchObject({event: "tool.denied", decision: "deny", reason: "no_requester"});
  });
  it("fails closed with a missing store", () => {
    rmSync(dbPath);
    expect(enforce(client, {warn: vi.fn()}, "grocery_add", "shared-tools", member)).toMatchObject({allow: false});
  });
  it("enforces configured guests while leaving senderless owner CLI monitored", () => {
    const policies: Array<{evaluate: (event: any, context: any) => unknown}> = [];
    entry.register({pluginConfig: {dbPath, enforceAgents: ["shared-tools"]},
      logger: {warn: vi.fn()}, registerTrustedToolPolicy: (p: any) => policies.push(p), registerCommand: () => {}} as never);
    expect(policies[0].evaluate({toolName: "grocery_add"}, {agentId: "shared-tools"})).toMatchObject({allow: false});
    expect(policies[0].evaluate({toolName: "exec"}, {agentId: "main"})).toBeUndefined();
    expect(policies[0].evaluate({toolName: "grocery_add"}, {agentId: "shared-tools", requester: member})).toBeUndefined();
  });
});


describe("host deferred tool broker", () => {
  it("allows catalog discovery only for an authenticated household grant holder", () => {
    expect(decide(client, "tool_search", "shared-tools", wa(MEMBER_WA)).allow).toBe(true);
    expect(decide(client, "tool_search", "shared-tools", wa(STRANGER_WA)).allow).toBe(false);
    expect(decide(client, "tool_search", "shared-tools", undefined).allow).toBe(false);
    expect(decide(client, "tool_search", "main", wa(MEMBER_WA)).allow).toBe(false);
    const db = new DatabaseSync(dbPath);
    db.exec("UPDATE grants SET status='revoked' WHERE person_id=2");db.close();
    expect(decide(client, "tool_search", "shared-tools", wa(MEMBER_WA)).allow).toBe(false);
  });
  it.each(["tool_call", "tool_describe"])("%s checks canonical household ownership and current grants", (wrapper) => {
    const check = (id: string, sender = MEMBER_WA) => decide(client, wrapper, "shared-tools", wa(sender), {id});
    expect(check("openclaw:grocery-list-tool:grocery_show").allow).toBe(true);
    expect(check("openclaw:doctor-search-tool:doctor_search").allow).toBe(true);
    expect(check("openclaw:grocery-list-tool:grocery_show", MEMBER2_WA).allow).toBe(false);
    expect(check("openclaw:doctor-search-tool:doctor_search", MEMBER2_WA).allow).toBe(true);
    for (const id of ["openclaw:other:grocery_show", "openclaw:grocery-list-tool:exec", "openclaw:doctor-search-tool:grocery_show", "openclaw:grocery-list-tool:doctor_search", "openclaw:grocery-list-tool:grocery_admin", "grocery_show", "openclaw:grocery-list-tool:grocery_show:extra", "openclaw:grocery-list-tool:grocery_show\n"]) expect(check(id).allow).toBe(false);
    expect(decide(client, wrapper, "shared-tools", wa(MEMBER_WA), {id: 42, actor: OWNER_WA}).allow).toBe(false);
    expect(decide(client, wrapper, "shared-tools", wa(MEMBER_WA)).allow).toBe(false);
    const db = new DatabaseSync(dbPath);db.exec("UPDATE grants SET status='revoked' WHERE person_id=2 AND resource='grocery'");db.close();
    expect(check("openclaw:grocery-list-tool:grocery_show").allow).toBe(false);
  });
  it("enforcement forwards broker arguments and never returns an allow override", () => {
    const logger = {warn:vi.fn()};
    expect(enforce(client, logger, "tool_call", "shared-tools", wa(MEMBER_WA), {id:"openclaw:grocery-list-tool:grocery_show"})).toBeUndefined();
    expect(enforce(client, logger, "tool_call", "shared-tools", wa(MEMBER_WA), {id:"openclaw:other:exec"})?.allow).toBe(false);
  });
});
