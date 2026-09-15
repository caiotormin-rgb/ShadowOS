/** Opt-in bounded actual built-in OpenClaw replay. Never a delivery/WhatsApp test.
 * node tools/household-config/test/native-replay.mjs --run [--turns 1|2] [--legacy-history]
 * Without --run, prints isolation settings and makes no provider request.
 */
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, readdirSync, chmodSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { execFileSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { DatabaseSync } from "node:sqlite";
const ROOT=resolve(dirname(fileURLToPath(import.meta.url)),"../../..");
const HOST=process.env.HOUSEHOLD_TEST_OPENCLAW_ROOT??"/home/openclaw/.openclaw/tools/node-v24.19.0/lib/node_modules/openclaw";
const AUTH_AGENT="/home/openclaw/.openclaw/agents/shared-tools/agent";
const args=process.argv.slice(2);
if(args.some((arg,index)=>!["--run","--turns","--legacy-history"].includes(arg)&&args[index-1]!=="--turns"))throw new Error("Only --run, --turns 1|2 and --legacy-history are accepted");
const turns=Number(args.includes("--turns")?args[args.indexOf("--turns")+1]:2);
if(![1,2].includes(turns))throw new Error("Maximum two runs");
if(!args.includes("--run")){console.log(JSON.stringify({optIn:"--run",maxRuns:turns,timeoutMs:120000,host:HOST,authAgentDir:AUTH_AGENT,authState:"read-only",sessionPersistence:"detached",delivery:false,provider:"openai",model:"gpt-5.6-sol"}));process.exit(0)}
const scratch=mkdtempSync(join(tmpdir(),"grocery-contract-native-"));chmodSync(scratch,0o700);
const state=join(scratch,"state"),workspace=join(scratch,"workspace");for(const p of [state,workspace])mkdirSync(p,{mode:0o700});
writeFileSync(join(scratch,".synthetic-fixture"),"synthetic only\n",{mode:0o600});
const actor="+15555550101",account="native-probe";
const names=["grocery_show","grocery_add","grocery_mark","grocery_remove","grocery_trip","grocery_activity","grocery_preferences"];
const base=readFileSync(join(ROOT,"tools/household-config/prompts/BASE.md"),"utf8");
const skill=readFileSync(join(ROOT,"tools/grocery-list/skill/SKILL.md"),"utf8").replace(/^---[\s\S]*?---\s*/,"");
for(const [name,text] of [["AGENTS.md",base],["GROCERY.md",skill],["DOCTOR.md","Doctor capability disabled in this synthetic test."]])writeFileSync(join(workspace,name),text,{mode:0o600});
execFileSync("/usr/bin/python3",[join(ROOT,"tools/grocery-list/bench/contract/fixtures.py")],{input:JSON.stringify({action:"seed",root:scratch,lang:"en",items:[{name:"Milk",quantity:2},{name:"Bread"}]}),stdio:["pipe","pipe","pipe"]});
const config={
  agents:{defaults:{model:{primary:"openai/gpt-5.6-sol",fallbacks:[]},models:{"openai/gpt-5.6-sol":{agentRuntime:{id:"openclaw"}}},thinkingDefault:"low",timeoutSeconds:120,skipBootstrap:true},entries:{"shared-tools":{workspace,agentDir:AUTH_AGENT,model:{primary:"openai/gpt-5.6-sol",fallbacks:[]},models:{"openai/gpt-5.6-sol":{agentRuntime:{id:"openclaw"}}},modelPolicy:{allow:["openai/gpt-5.6-sol"]},skills:[],memory:{search:{enabled:false}},tools:{profile:"full",allow:names,deny:["exec","process","read","write","edit","apply_patch","message","sessions_send","sessions_spawn"],exec:{mode:"deny"},elevated:{enabled:false}}}}},
  tools:{profile:"full",toolSearch:{enabled:true,mode:"tools"},codeMode:false},
  plugins:{allow:["openai","whatsapp","grocery-list-tool","access","household-router"],load:{paths:[join(ROOT,"tools/grocery-list/plugin"),join(ROOT,"tools/access/plugin"),join(ROOT,"tools/household-router")]},entries:{
    openai:{enabled:true},whatsapp:{enabled:true},"doctor-search-tool":{enabled:false},
    access:{enabled:true,config:{dbPath:join(scratch,"access.sqlite3"),enforceAgents:["shared-tools"]}},
    "grocery-list-tool":{enabled:true,config:{pythonPath:"/usr/bin/python3",scriptPath:join(ROOT,"tools/grocery-list/core/grocery.py"),familyDbPath:join(scratch,"family.sqlite3"),privateDbDir:join(scratch,"private"),whatsappAccountId:account,allowedRequesters:[actor],accessDbPath:join(scratch,"access.sqlite3")}},
    "household-router":{enabled:true,hooks:{allowConversationAccess:true,allowPromptInjection:true},config:{statePath:join(scratch,"modes.sqlite3"),agentIds:["shared-tools"],accountIds:[account],allowedSenders:[actor],defaultMode:"groceries",tools:{groceries:names,doctor:["doctor_search"]},instructionPaths:{groceries:join(workspace,"GROCERY.md"),doctor:join(workspace,"DOCTOR.md")}}}
  }},
  channels:{whatsapp:{enabled:false,accounts:{[account]:{enabled:false}}}},logging:{file:join(scratch,"runtime.log"),consoleLevel:"error"}
};
const configPath=join(scratch,"openclaw.json");writeFileSync(configPath,JSON.stringify(config,null,2),{mode:0o600});
// Set BEFORE importing host runtime so every global config/state lookup is isolated.
process.env.OPENCLAW_CONFIG_PATH=configPath;process.env.OPENCLAW_STATE_DIR=state;process.env.OPENCLAW_WORKSPACE_DIR=workspace;
const hostFile=(prefix)=>{const file=readdirSync(join(HOST,"dist")).find(n=>n.startsWith(prefix)&&n.endsWith(".mjs"));if(!file)throw new Error(`Missing installed module ${prefix}`);return pathToFileURL(join(HOST,"dist",file)).href};
const {r:loadOpenClawPlugins}=await import(hostFile("loader-runtime-load-"));
const registry=loadOpenClawPlugins({config,workspaceDir:workspace,activate:true,cache:false});
const loaded=registry.plugins.map(p=>({id:p.id,status:p.status,error:p.error}));
writeFileSync(join(scratch,"loaded.json"),JSON.stringify(loaded,null,2),{mode:0o600});
for(const id of ["openai","access","grocery-list-tool","household-router"])if(!loaded.some(p=>p.id===id&&p.status==="loaded"))throw new Error(`Required plugin failed: ${id}; see ${scratch}/loaded.json`);
const db=new DatabaseSync(join(scratch,"access.sqlite3"));db.exec(`INSERT INTO people(id,name,role,status) VALUES(1,'Synthetic','member','active');INSERT INTO identities(person_id,channel,account_id,sender_id) VALUES(1,'whatsapp','${account}','${actor}');INSERT INTO grants(person_id,resource,action) VALUES(1,'grocery','use');`);db.close();
const toolModuleFile=readdirSync(join(HOST,"dist")).find(name=>name.startsWith("tools-")&&name.endsWith(".mjs")&&readFileSync(join(HOST,"dist",name),"utf8").includes("resolvePluginTools as r"));
const {r:resolvePluginTools}=await import(pathToFileURL(join(HOST,"dist",toolModuleFile)).href);
const resolvedTools=resolvePluginTools({context:{config,runtimeConfig:config,workspaceDir:workspace,agentId:"shared-tools",sessionKey:`agent:shared-tools:whatsapp:${account}:direct:${actor}`,messageChannel:"whatsapp",deliveryContext:{channel:"whatsapp",accountId:account,to:actor},requesterSenderId:actor},toolAllowlist:names,runtimeRegistry:registry});
console.log(JSON.stringify({event:"tool-preflight",registered:registry.tools.map(t=>({pluginId:t.pluginId,names:t.names})),resolved:resolvedTools.map(t=>t.name)}));
const {t:runEmbeddedAgent}=await import(hostFile("embedded-agent-"));
const {t:SessionManager}=await import(hostFile("session-manager-"));
const {o:prepareSystemAgentRunAdmission}=await import(hostFile("admitted-run-context-"));
const hash=path=>createHash("sha256").update(readFileSync(join(ROOT,path))).digest("hex");
const metadata={scratch,host:HOST,hostVersion:JSON.parse(readFileSync(join(HOST,"package.json"))).version,provider:"openai",model:"gpt-5.6-sol",authAgentDir:AUTH_AGENT,authState:"read-only",sessionPersistence:"detached",delivery:false,toolSearch:config.tools.toolSearch,toolNames:names,sourceHashes:Object.fromEntries(["tools/household-router/src/router.mjs","tools/grocery-list/plugin/src/index.ts","tools/grocery-list/core/agent_api.py","tools/grocery-list/skill/SKILL.md","tools/access/plugin/src/policy.ts","tools/access/plugin/dist/policy.js"].map(p=>[p,hash(p)]))};
writeFileSync(join(scratch,"metadata.json"),JSON.stringify(metadata,null,2),{mode:0o600});console.log(JSON.stringify({event:"ready",scratch,toolNames:names}));
let probeFailed=false;
for(let index=0;index<turns;index++){
 const stale=args.includes("--legacy-history")||index===1,sessionId=randomUUID(),runId=randomUUID(),manager=SessionManager.inMemory(workspace),events=[],toolResults=[];
 if(stale){
  manager.appendMessage({role:"user",content:"Show my groceries",timestamp:Date.now()-60000});
  manager.appendMessage({role:"assistant",content:[{type:"toolCall",id:"legacy-call-1",name:"grocery_list",arguments:{action:"list",scope:"family",store:"Shop"}}],api:"openai-responses",provider:"openai",model:"gpt-5.6-sol",usage:{input:0,output:0,cacheRead:0,cacheWrite:0,totalTokens:0,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}},stopReason:"toolUse",timestamp:Date.now()-59000});
  manager.appendMessage({role:"toolResult",toolCallId:"legacy-call-1",toolName:"grocery_list",content:[{type:"text",text:JSON.stringify({scope:"family",store:"Shop",text:"Shop\n- Milk ×2\n- Bread ×1"})}],isError:false,timestamp:Date.now()-58000});
 }
 const before=execFileSync("/usr/bin/python3",[join(ROOT,"tools/grocery-list/bench/contract/fixtures.py")],{input:JSON.stringify({action:"snapshot",root:scratch}),encoding:"utf8"}).trim();
 const started=Date.now();console.log(JSON.stringify({event:"run-start",index,stale}));
 let result,error;
 const admission=prepareSystemAgentRunAdmission(config,runId,"shared-tools","native-regression-probe");
 try{result=await runEmbeddedAgent({preparedRunAdmission:admission,sessionManager:manager,sessionPersistence:"detached",sessionId,sessionKey:`agent:shared-tools:whatsapp:${account}:direct:${actor}:probe:${index}`,agentId:"shared-tools",workspaceDir:workspace,bootstrapWorkspaceDir:workspace,agentDir:AUTH_AGENT,config,prompt:"Show me the current lists",messageChannel:"whatsapp",messageProvider:"whatsapp",agentAccountId:account,senderId:actor,senderE164:actor,senderIsOwner:false,chatType:"direct",chatId:actor,currentChannelId:actor,messageTo:actor,conversationRoutePeerId:actor,trigger:"user",inputProvenance:{kind:"external_user"},provider:"openai",model:"gpt-5.6-sol",agentHarnessRuntimeOverride:"openclaw",modelSelectionLocked:true,modelFallbacksOverride:[],timeoutMs:120000,runTimeoutOverrideMs:120000,abortSignal:AbortSignal.timeout(120000),runId,authProfileFailurePolicy:"local",authProfileStateMode:"read-only",disableTrajectory:true,disableMessageTool:true,codeModeOverride:false,skillsSnapshot:{prompt:"",skills:[]},thinkLevel:"low",verboseLevel:"off",suppressLiveStreamOutput:true,onAgentEvent:event=>events.push(event),onAgentToolResult:event=>toolResults.push(event)});}catch(e){error=String(e?.message??e)}finally{admission.close()}
 const after=execFileSync("/usr/bin/python3",[join(ROOT,"tools/grocery-list/bench/contract/fixtures.py")],{input:JSON.stringify({action:"snapshot",root:scratch}),encoding:"utf8"}).trim();
 const receiptDb=new DatabaseSync(join(scratch,"family.sqlite3"),{readOnly:true});
 const receipts=receiptDb.prepare("SELECT response FROM agent_receipts WHERE actor=?").all(actor).map(row=>JSON.parse(row.response));receiptDb.close();
 const visible=(result?.payloads??[]).map(p=>p.text??"").join("\n");
 const checks={backendReplyRecorded:receipts.some(receipt=>receipt.ok===true&&receipt.status==="done"),unchangedState:before===after,noForeignDisclosure:!visible.includes("PRIVATE_SENTINEL_CAROL"),toolCallMade:(result?.meta?.toolSummary?.calls??0)>0,noToolFailures:(result?.meta?.toolSummary?.failures??0)===0,fixtureStoreShown:visible.includes("Shop")};
 const passed=!error&&Object.values(checks).every(Boolean);
 const record={index,stale,elapsedMs:Date.now()-started,passed,checks,error,result,toolResults,events};writeFileSync(join(scratch,`run-${index}.json`),JSON.stringify(record,null,2),{mode:0o600});
 console.log(JSON.stringify({event:"run-end",index,stale,elapsedMs:record.elapsedMs,passed,checks,error,replayInvalid:result?.meta?.replayInvalid,toolSummary:result?.meta?.toolSummary,successfulToolNames:result?.meta?.agentMeta?.terminalReceipt?.successfulToolNames,payloads:result?.payloads}));
 if(!passed)probeFailed=true;
 if(error)break;
}
console.log(JSON.stringify({event:"done",scratch}));
// Installed plugin registries own background resources; bounded helper exits after artifacts flush.
process.exit(probeFailed?1:0);
