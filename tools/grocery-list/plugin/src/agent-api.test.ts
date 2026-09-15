import { mkdtemp, rm, readFile } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import entry, { authorizedRequester, narrowTools, publicResult, removeCommand, requestKey, runAgentApi, toolRequest, trustedPhone, type GroceryConfig } from "./index.js";
const paths: string[] = [];
afterEach(async () => { await Promise.all(paths.splice(0).map((p) => rm(p, { recursive: true, force: true }))); });
async function config(): Promise<GroceryConfig> {
  const root = await mkdtemp(join(tmpdir(), "grocery-api-plugin-")); paths.push(root);
  return { pythonPath: "/usr/bin/python3", scriptPath: join(process.cwd(), "..", "core", "grocery.py"), familyDbPath: join(root,"family.sqlite3"), privateDbDir: join(root,"private"), whatsappAccountId:"tools", allowedRequesters:["+12025550101"] };
}
const reply = { ok:true, reply:"Pronto.", status:"done", assumptions:[] };
const executor = () => vi.fn(async () => ({ stdout:JSON.stringify(reply) }));
describe("narrow agent bridge", () => {
  it("registers optional tools and native authenticated confirmation with matching manifest", async () => {
    const registered:any[]=[]; const commands:any[]=[]; const c=await config();
    entry.register!({ pluginConfig:c, registerTool:(factory:unknown,meta:unknown)=>registered.push({factory,meta}), registerCommand:(command:unknown)=>commands.push(command) } as never);
    const manifest=JSON.parse(await readFile("openclaw.plugin.json","utf8"));
    expect(registered.map(t=>t.meta.name)).toEqual(manifest.contracts.tools);
    expect(registered.every(t=>t.meta.optional===true)).toBe(true);
    expect(commands).toHaveLength(1); expect(commands[0]).toMatchObject({name:"remover",requireAuth:true});
    const factory=registered[0].factory;
    expect(factory({deliveryContext:{channel:"whatsapp",accountId:"tools"},requesterSenderId:"+12025550199"})).toBeNull();
    expect(factory({messageChannel:"whatsapp",requesterSenderId:c.allowedRequesters[0]})).toBeNull();
    const tool=factory({deliveryContext:{channel:"whatsapp",accountId:"tools"},requesterSenderId:c.allowedRequesters[0]});
    expect((await tool.execute("x",{view:"list",actor:"+12025550199"})).details.ok).toBe(false);
  });
  it("maps consistent items, propagates language and refuses model authority fields", () => {
    expect(toolRequest("grocery_mark",{items:[{name:"Bounty"}],state:"purchased",lang:"pt"})).toEqual({action:"buy",items:[{name:"Bounty"}],lang:"pt"});
    expect(toolRequest("grocery_preferences",{lang:"pt",store:"Costco"})).toMatchObject({action:"preferences",lang:"pt"});
    for(const name of narrowTools.map(t=>t.name)) expect(JSON.stringify(narrowTools.find(t=>t.name===name)?.parameters)).not.toContain("confirm_remove");
    for(const extra of [{actor:"+12025550199"},{request_id:"fake"},{confirmation_code:"ABCDEF"},{db:"/tmp/other"}]) expect(()=>toolRequest("grocery_remove",{items:[{name:"milk"}],...extra})).toThrow();
  });
  it("rejects groups, alternate namespaces, arbitrary digits and missing account", async () => {
    const c=await config();
    for(const sender of ["12025550101@g.us","text12025550101","+1 (202) 555-0101","12025550101@lid"]) expect(trustedPhone(sender)).toBeNull();
    expect(trustedPhone("12025550101@s.whatsapp.net")).toBe(c.allowedRequesters[0]);
    expect(authorizedRequester("whatsapp",undefined,c.allowedRequesters[0],c)).toBeNull();
  });
  it("passes fixed argv, authenticated actor and trusted retry key without shell", async () => {
    const c=await config(); const exec=executor();
    await runAgentApi({action:"add",items:[{name:"$(touch /tmp/no)"}],lang:"pt",actor:"fake",request_id:"fake"},c.allowedRequesters[0],c,{exec,requestId:"host-key"});
    const [file,args,options]=exec.mock.calls[0] as unknown as [string,string[],unknown];
    expect(file).toBe(c.pythonPath); expect(options).toEqual({timeout:15000,maxBuffer:262144});
    expect(args[args.indexOf("--actor")+1]).toBe(c.allowedRequesters[0]);
    expect(JSON.parse(args[args.indexOf("--request-json")+1])).toEqual({action:"add",items:[{name:"$(touch /tmp/no)"}],lang:"pt",request_id:"host-key"});
    expect(args).not.toContain("--trusted-confirmation");
    expect(requestKey("s","a")).toBe(requestKey("s","a")); expect(requestKey("s","a")).not.toBe(requestKey("s","b")); expect(requestKey(undefined,"a")).toBeUndefined();
  });
  it("returns compact safe output and suppresses subprocess errors", async () => {
    expect(publicResult({...reply,sql:"secret",rows:[{phone:"secret"}]})).toEqual(reply);
    const c=await config();
    for(const exec of [async()=>{throw new Error("/home/private secret traceback")},async()=>({stdout:"garbage"}),async()=>({stdout:'{"ok":true}'})]) {
      const result=await runAgentApi({action:"list",lang:"pt"},c.allowedRequesters[0],c,{exec});
      expect(result.ok).toBe(false); expect(result.reply).toContain("Não consegui"); expect(JSON.stringify(result)).not.toMatch(/secret|traceback|home/);
    }
  });
  it("allows native confirmation only with exact host identity and explicit complete command", async () => {
    const c=await config(); const exec=executor();
    const ctx={channel:"whatsapp",accountId:"tools",senderId:c.allowedRequesters[0],from:c.allowedRequesters[0],isAuthorizedSender:true,args:"ABC123"};
    for(const bad of [{from:undefined},{from:"12025550101@g.us"},{from:"+12025550199"},{senderId:"text12025550101"},{accountId:undefined},{accountId:"other"},{senderId:undefined},{senderId:"12025550101@g.us"},{isAuthorizedSender:false},{isAuthorizedSender:undefined},{args:"ABC123 extra"},{args:"ABC123 private ignored"}]) await removeCommand({...ctx,...bad},c,exec);
    expect(exec).not.toHaveBeenCalled();
    await runAgentApi({action:"confirm_remove",confirmation_code:"ABC123"},ctx.senderId,c,{exec}); expect(exec).not.toHaveBeenCalled();
    await removeCommand(ctx,c,exec); expect(exec).toHaveBeenCalledTimes(1);
    const args=(exec.mock.calls[0] as unknown as [string,string[]])[1]; expect(args).toContain("--trusted-confirmation");
    expect(JSON.parse(args[args.indexOf("--request-json")+1])).toEqual({action:"confirm_remove",confirmation_code:"ABC123"});
    await removeCommand(ctx,{...c,accessDbPath:"/does/not/exist"},exec); expect(exec).toHaveBeenCalledTimes(1);
  });
  it("rechecks live grants and active membership for every native confirmation", async () => {
    const c=await config(); const dbPath=join(c.privateDbDir,"..","access.sqlite3");
    const db=new DatabaseSync(dbPath);
    db.exec(`CREATE TABLE people(id INTEGER, name TEXT, role TEXT, lang TEXT, status TEXT);
      CREATE TABLE identities(person_id INTEGER, channel TEXT, sender_id TEXT, account_id TEXT);
      CREATE TABLE grants(person_id INTEGER, resource TEXT, action TEXT, scope_json TEXT, status TEXT);
      INSERT INTO people VALUES(1,'Tester','owner','en','active');
      INSERT INTO identities VALUES(1,'whatsapp','+12025550101','tools');
      INSERT INTO grants VALUES(1,'grocery','use','{}','active');`);
    const exec=executor(); const ctx={channel:"whatsapp",accountId:"tools",senderId:c.allowedRequesters[0],from:c.allowedRequesters[0],isAuthorizedSender:true,args:"ABC123"};
    try {
      await removeCommand(ctx,{...c,accessDbPath:dbPath},exec); expect(exec).toHaveBeenCalledTimes(1);
      db.exec("UPDATE grants SET status='revoked'");
      await removeCommand(ctx,{...c,accessDbPath:dbPath},exec); expect(exec).toHaveBeenCalledTimes(1);
      db.exec("UPDATE grants SET status='active'; UPDATE people SET status='removed'");
      await removeCommand(ctx,{...c,accessDbPath:dbPath},exec); expect(exec).toHaveBeenCalledTimes(1);
    } finally { db.close(); }
  });
  it("uses isolated private storage and keeps private removal command scoped", async () => {
    const c=await config(); const exec=vi.fn(async()=>({stdout:JSON.stringify({ok:true,status:"confirmation",reply:"Envie /remover ABC123",confirmation_code:"ABC123",assumptions:[]})}));
    const result=await runAgentApi({action:"remove",scope:"private",items:[{name:"milk"}]},c.allowedRequesters[0],c,{exec});
    expect(result.reply).toBe("Envie /remover ABC123 private");
    expect((exec.mock.calls[0] as unknown as [string,string[]])[1]).toContain("--private");
  });
  it("executes real private add/list/preferences/preview/confirm without exposing raw records", async () => {
    const c=await config(); const actor=c.allowedRequesters[0];
    const pref=await runAgentApi({action:"preferences",scope:"private",lang:"pt",store:"Costco"},actor,c); expect(pref.ok,pref.reply).toBe(true);
    const added=await runAgentApi({action:"add",scope:"private",items:[{name:"leite"}]},actor,c,{requestId:"one"}); expect(added.ok,added.reply).toBe(true);
    expect(await runAgentApi({action:"add",scope:"private",items:[{name:"leite"}]},actor,c,{requestId:"one"})).toEqual(added);
    const list=await runAgentApi({action:"list",scope:"private"},actor,c); expect(list.ok,list.reply).toBe(true); expect(list.reply.toLowerCase()).toContain("leite"); expect(list).not.toHaveProperty("items");
    const preview=await runAgentApi({action:"remove",scope:"private",items:[{name:"leite"}]},actor,c); expect(preview.status,preview.reply).toBe("confirmation");
    const done=await removeCommand({channel:"whatsapp",accountId:"tools",senderId:actor,from:actor,isAuthorizedSender:true,args:`${preview.confirmation_code} private`},c); expect(done.text).not.toContain("Não consegui");
    const after=await runAgentApi({action:"list",scope:"private"},actor,c); expect(after.reply.toLowerCase()).not.toContain("leite");
  });
});
