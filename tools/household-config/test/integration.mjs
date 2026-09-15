// Provider-free integration: actual installed SDK context builder + registered
// production plugin callbacks + real synthetic SQLite stores. Not a WhatsApp run.
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { DatabaseSync } from "node:sqlite";
import grocery from "../../grocery-list/plugin/dist/index.js";
import access from "../../access/plugin/dist/index.js";
import { registerRouter } from "../../household-router/src/router.mjs";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const ACTOR = "+15550000001";
const ACTUAL_HOST = process.env.HOUSEHOLD_TEST_OPENCLAW_ROOT ?? "/home/openclaw/.openclaw/tools/node-v24.19.0/lib/node_modules/openclaw";
const pkg = JSON.parse(readFileSync(join(ACTUAL_HOST,"package.json"),"utf8"));
const dist = join(ACTUAL_HOST,"dist");
const contextModule = readdirSync(dist).find(name => /^lifecycle-hook-helpers-.*\.mjs$/.test(name));
assert.ok(contextModule, "Actual host context helper missing: update this version-specific integration test");
const contextSource = readFileSync(join(dist, contextModule), "utf8");
const alias = /buildAgentHookContext as (\w+)/.exec(contextSource)?.[1];
assert.ok(alias, "Actual host context export changed: inspect before updating");
const buildContext = (await import(pathToFileURL(join(dist,contextModule)).href))[alias];

function harness(t) {
  const root = mkdtempSync(join(tmpdir(),"household-integration-"));
  const policies=[], tools=[], commands=[], hooks=[], services=[];
  const api={logger:{warn(){}},registerTrustedToolPolicy(p){policies.push(p)},registerTool(factory,meta){tools.push({factory,meta})},registerCommand(c){commands.push(c)},on(name,fn){hooks.push({name,fn})},registerService(s){services.push(s)}};
  const accessDb=join(root,"access.sqlite3");
  access.register({...api,pluginConfig:{dbPath:accessDb,enforceAgents:["shared-tools"]}});
  const db=new DatabaseSync(accessDb);
  db.exec(`INSERT INTO people(id,name,role,status) VALUES(1,'Synthetic','member','active');
    INSERT INTO identities(person_id,channel,account_id,sender_id) VALUES(1,'whatsapp','test','${ACTOR}');
    INSERT INTO grants(person_id,resource,action) VALUES(1,'grocery','use');
    INSERT INTO grants(person_id,resource,action) VALUES(1,'doctor','request');`);
  const config={pythonPath:"/usr/bin/python3",scriptPath:join(ROOT,"tools/grocery-list/core/grocery.py"),familyDbPath:join(root,"family.sqlite3"),privateDbDir:join(root,"private"),whatsappAccountId:"test",allowedRequesters:[ACTOR],accessDbPath:accessDb};
  grocery.register({...api,pluginConfig:config});
  const names=tools.map(t=>t.meta.name).filter(n=>n!=="grocery_list");
  registerRouter({...api,pluginConfig:{statePath:join(root,"mode.sqlite3"),agentIds:["shared-tools"],accountIds:["test"],allowedSenders:[ACTOR],tools:{groceries:names,doctor:["doctor_search"]}}});
  const inbound={agentId:"shared-tools",channel:"whatsapp",accountId:"test",senderId:ACTOR,from:ACTOR,chatId:ACTOR,runId:"run1",trigger:"user",inputProvenance:{kind:"external_user"},isAuthorizedSender:true};
  const hookCtx=buildContext(inbound);
  const runCtx={agentId:"shared-tools",runId:"run1",requester:{channel:"whatsapp",accountId:"test",senderId:ACTOR}};
  const invokeHook=(name,ctx=hookCtx)=>hooks.filter(h=>h.name===name).map(h=>h.fn({},ctx));
  const blocked=(name,ctx=runCtx,params={})=>policies.some(p=>{const d=p.evaluate({toolName:name,params},ctx);assert.ok(!(d instanceof Promise),"Use async policy aggregation if implementation changes");return d?.block===true||d?.allow===false});
  const tool=name=>tools.find(t=>t.meta.name===name).factory({agentId:"shared-tools",sessionId:"s1",deliveryContext:{channel:"whatsapp",accountId:"test"},requesterSenderId:ACTOR});
  t.after(()=>{db.close();for(const service of services)service.stop();rmSync(root,{recursive:true,force:true})});
  return {db,commands,inbound,hookCtx,runCtx,invokeHook,blocked,tool};
}

test(`actual OpenClaw ${pkg.version} context and registered router/access/grocery compose`,async t=>{
  const h=harness(t);
  for(const field of ["runId","agentId","accountId","channel","senderId","chatId","inputProvenance"]) assert.deepEqual(h.hookCtx[field],h.inbound[field],`Actual host must preserve ${field}`);
  h.invokeHook("before_model_resolve"); h.invokeHook("before_prompt_build");
  assert.equal(h.blocked("grocery_add"),false);assert.equal(h.blocked("doctor_search"),true);assert.equal(h.blocked("exec"),true);
  const added=await h.tool("grocery_add").execute("add1",{scope:"private",items:[{name:"milk"}],store:"Test Store",lang:"en"});
  assert.equal(added.details.ok,true,added.details.reply);
  const preview=await h.tool("grocery_remove").execute("remove1",{scope:"private",items:[{name:"milk"}],store:"Test Store"});
  assert.equal(preview.details.status,"confirmation",preview.details.reply);
  const command=h.commands.find(c=>c.name==="remover");
  const before=await command.handler({...h.inbound,from:"123@g.us",args:`${preview.details.confirmation_code} private`});
  assert.match(before.text,/unavailable|não está disponível/);
  h.db.exec("UPDATE grants SET status='revoked' WHERE resource='grocery'");
  assert.equal(h.blocked("grocery_add"),true);
  const revoked=await command.handler({...h.inbound,args:`${preview.details.confirmation_code} private`});assert.match(revoked.text,/unavailable|não está disponível/);
  h.db.exec("UPDATE grants SET status='active' WHERE resource='grocery'");
  assert.equal(h.blocked("grocery_add"),false);
  const done=await command.handler({...h.inbound,args:`${preview.details.confirmation_code} private`});assert.doesNotMatch(done.text,/unavailable|não está disponível/);
  const listed=await h.tool("grocery_show").execute("list1",{view:"list",scope:"private",store:"Test Store"});assert.doesNotMatch(listed.details.reply,/milk/i);
});

test("native domain switch revokes old run while fresh host context adopts only new domain",t=>{
  const h=harness(t);h.invokeHook("before_prompt_build");
  const switched=h.commands.find(c=>c.name==="doctor").handler(h.inbound);assert.match(switched.text,/Doctor search active/);
  assert.equal(h.blocked("grocery_add"),true);assert.equal(h.blocked("doctor_search"),true);
  const ctx=buildContext({...h.inbound,runId:"run2"});h.invokeHook("before_prompt_build",ctx);
  assert.equal(h.blocked("doctor_search",{...h.runCtx,runId:"run2"}),false);
  assert.equal(h.blocked("grocery_add",{...h.runCtx,runId:"run2"}),true);
});


test("deferred discovery works while broker targets and canonical execution retain grants", async t => {
  const h=harness(t);h.invokeHook("before_prompt_build");
  assert.equal(h.blocked("tool_search"),false);
  const list={id:"openclaw:grocery-list-tool:grocery_show",args:{view:"list"}};
  assert.equal(h.blocked("tool_describe",h.runCtx,list),false);
  assert.equal(h.blocked("tool_call",h.runCtx,list),false);
  assert.equal(h.blocked("grocery_show"),false);
  for(const id of ["openclaw:other:grocery_show","openclaw:grocery-list-tool:exec","openclaw:doctor-search-tool:doctor_search","grocery_show"])
    assert.equal(h.blocked("tool_call",h.runCtx,{id}),true);
  assert.equal(h.blocked("tool_call",h.runCtx,{id:"openclaw:grocery-list-tool:grocery_list"}),true);
  assert.equal(h.blocked("tool_search",{...h.runCtx,runId:"unprepared"}),true);
  h.db.exec("UPDATE grants SET status='revoked' WHERE resource='grocery'");
  assert.equal(h.blocked("tool_call",h.runCtx,list),true);
  assert.equal(h.blocked("tool_describe",h.runCtx,list),true);
  assert.equal(h.blocked("grocery_show"),true);
  h.db.exec("UPDATE grants SET status='revoked'");
  assert.equal(h.blocked("tool_search"),true);
});
