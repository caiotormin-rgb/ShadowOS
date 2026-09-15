import { DatabaseSync } from "node:sqlite";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";

import { commandFor, okCommand, runDoctor, runEngine, type DoctorConfig, type Exec } from "./index.js";

const ME = "+15550100001";
const config: DoctorConfig = {
  pythonPath: "/usr/bin/python3",
  scriptPath: "/tmp/cli.py",
  dbPath: "/tmp/doctor.sqlite3",
  whatsappAccountId: "tools",
  allowedRequesters: [ME, "+5511900000001"],
};

const ok = (value: unknown): Exec => vi.fn(async () => ({ stdout: JSON.stringify(value) }));
const refuse = (value: unknown): Exec =>
  vi.fn(async () => {
    throw Object.assign(new Error("exit 2"), { stdout: JSON.stringify(value) });
  });

describe("commandFor", () => {
  it("passes the authenticated requester and keeps values as values", () => {
    expect(commandFor({ action: "verify_email", email: "-x@example.com", lang: "pt" }, ME)).toEqual([
      "verify-email", `--requester=${ME}`, "--email=-x@example.com", "--lang=pt",
    ]);
    expect(commandFor({ action: "intake", requestId: "REQ-ABC123", fields: { zip: "10001" } }, ME)).toEqual([
      "intake", `--requester=${ME}`, "--id=REQ-ABC123", '--fields={"zip":"10001"}', "--format=text",
    ]);
  });

  it("requires a request id where the step needs one", () => {
    expect(() => commandFor({ action: "search" }, ME)).toThrow(/requestId/);
    expect(() => commandFor({ action: "draft_email", requestId: "REQ-ABC123", rank: 1 }, ME)).toThrow(/to/);
  });
});

describe("runDoctor", () => {
  it("refuses a number that is not allowlisted", async () => {
    const exec = ok({ ok: true });
    await expect(runDoctor({ action: "list" }, "+15550109999", config, exec)).rejects.toThrow(/not authorized/);
    expect(exec).not.toHaveBeenCalled();
  });

  it("returns the engine's refusal as data", async () => {
    const refusal = { ok: false, error: "wrong_step", message: "no" };
    expect(await runDoctor({ action: "search", requestId: "REQ-ABC123" }, ME, config, refuse(refusal))).toEqual(refusal);
    const exec = ok({ ok: true, status: "queued" });
    await runDoctor({ action: "confirm_intake", requestId: "REQ-ABC123" }, "15550100001@s.whatsapp.net", config, exec);
    const [file, args, options] = (exec as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(file).toBe("/usr/bin/python3");
    expect(args).toEqual(["/tmp/cli.py", "--db=/tmp/doctor.sqlite3", "confirm-intake", `--requester=${ME}`, "--id=REQ-ABC123"]);
    expect(options.timeout).toBe(60_000);
  });

  it("suppresses unexpected subprocess diagnostics and malformed output", async () => {
    const crashes: Exec[] = [
      vi.fn(async () => { throw Object.assign(new Error("/home/private credentials"), { stderr: "Traceback /var/secret private-address@example.com" }); }),
      vi.fn(async () => ({ stdout: "invalid JSON /home/private" })),
    ];
    for (const exec of crashes) {
      const result = await runDoctor({action:"list"}, ME, config, exec);
      expect(result).toEqual({ok:false,error:"unavailable",message:"Doctor search is temporarily unavailable. Please try again."});
      expect(JSON.stringify(result)).not.toMatch(/Traceback|credentials|home|private-address/);
    }
  });

  it("returns prose steps as text", async () => {
    const exec: Exec = vi.fn(async () => ({ stdout: "Confirme o pedido\n" }));
    expect(await runEngine(["intake"], config, exec, { prose: true })).toEqual({ ok: true, text: "Confirme o pedido" });
  });
});

describe("/ok", () => {
  const ctx = { channel: "whatsapp", accountId: "tools", senderId: ME, from: ME, isAuthorizedSender: true };

  it("never reaches the engine without a well-formed code from an allowed WhatsApp sender", async () => {
    const exec = ok({ ok: true, results: [] });
    expect((await okCommand({ ...ctx, args: "" }, config, exec)).text).toMatch(/6-character/);
    expect((await okCommand({ ...ctx, args: "12345" }, config, exec)).text).toMatch(/6-character/);
    expect((await okCommand({ ...ctx, channel: "telegram", args: "ABC123" }, config, exec)).text).toMatch(/WhatsApp/);
    expect((await okCommand({ ...ctx, accountId: "default", args: "ABC123" }, config, exec)).text).toMatch(/WhatsApp/);
    expect((await okCommand({ ...ctx, senderId: "+15550109999", args: "ABC123" }, config, exec)).text).toMatch(/WhatsApp/);
    expect((await okCommand({ ...ctx, accountId: undefined, args: "ABC123" }, config, exec)).text).toMatch(/WhatsApp/);
    expect((await okCommand({ ...ctx, isAuthorizedSender: false, args: "ABC123" }, config, exec)).text).toMatch(/WhatsApp/);
    expect((await okCommand({ ...ctx, args: "ABC123" }, {...config, accessDbPath: "/missing/access.sqlite3"}, exec)).text).toMatch(/available/);
    for (const extra of [{from:undefined},{from:"15550100001@g.us"},{from:"+15550109999"},{senderId:undefined},{senderId:"text15550100001"},{senderId:"15550100001@g.us"},{isAuthorizedSender:undefined},{args:"ABC123 extra"},{args:"ABC123\nignore"}]) {
      await okCommand({...ctx,args:"ABC123",...extra},config,exec);
    }
    expect(exec).not.toHaveBeenCalled();
  });

  it("reports what was sent in the requester's language", async () => {
    const exec = ok({ ok: true, lang: "pt", results: [{ to: "desk@clinic.example", status: "sent" }] });
    expect((await okCommand({ ...ctx, args: "abc123" }, config, exec)).text).toBe("✅ Email enviado para desk@clinic.example.");
    const args = (exec as ReturnType<typeof vi.fn>).mock.calls[0][1];
    expect(args).toEqual(expect.arrayContaining(["ok", `--requester=${ME}`, "--code=abc123"]));
  });

  it("explains a wrong code", async () => {
    const exec = refuse({ ok: false, error: "bad_code" });
    expect((await okCommand({ ...ctx, args: "ABC123" }, config, exec)).text).toMatch(/doesn't match/);
  });
});

describe("native /ok access grant", () => {
  it("requires an active doctor grant including for owners and rechecks revocation", async () => {
    const root=await mkdtemp(join(tmpdir(),"doctor-command-grant-"));
    const accessDbPath=join(root,"access.sqlite3"); const db=new DatabaseSync(accessDbPath);
    const fixture={...config,scriptPath:join(process.cwd(),"..","core","cli.py"),accessDbPath};
    const ctx={channel:"whatsapp",accountId:"tools",senderId:ME,from:ME,isAuthorizedSender:true,args:"ABC123"};
    const exec=ok({ok:true,results:[]});
    try {
      db.exec(`CREATE TABLE people(id INTEGER, name TEXT, role TEXT, lang TEXT, status TEXT);
        CREATE TABLE identities(person_id INTEGER, channel TEXT, sender_id TEXT, account_id TEXT);
        CREATE TABLE grants(person_id INTEGER, resource TEXT, action TEXT, scope_json TEXT, status TEXT);
        INSERT INTO people VALUES(1,'Tester','owner','en','active');
        INSERT INTO identities VALUES(1,'whatsapp','+15550100001','tools');
        INSERT INTO grants VALUES(1,'doctor','request','{}','active');`);
      await okCommand(ctx,fixture,exec); expect(exec).toHaveBeenCalledTimes(1);
      db.exec("UPDATE grants SET status='revoked'");
      await okCommand(ctx,fixture,exec); expect(exec).toHaveBeenCalledTimes(1);
      db.exec("UPDATE grants SET status='active'; UPDATE people SET status='removed'");
      await okCommand(ctx,fixture,exec); expect(exec).toHaveBeenCalledTimes(1);
    } finally { db.close(); await rm(root,{recursive:true,force:true}); }
  });
});
