// Provider-free integration against the installed OpenClaw inbound mapper,
// claiming-hook runner and built-in prompt context builders. No delivery or
// model is created. This proves native hook contracts, not WhatsApp delivery.
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { registerRouter } from '../../household-router/src/router.mjs';

const HOST = process.env.HOUSEHOLD_TEST_OPENCLAW_ROOT ?? '/home/openclaw/.openclaw/tools/node-v24.19.0/lib/node_modules/openclaw';
async function hostExport(prefix, name) {
  const dist = join(HOST, 'dist');
  const file = readdirSync(dist).find(f => f.startsWith(prefix + '-') && f.endsWith('.mjs'));
  assert.ok(file, `Installed host module ${prefix} changed; inspect before updating`);
  const alias = new RegExp(`${name} as (\\w+)`).exec(readFileSync(join(dist, file), 'utf8'))?.[1];
  assert.ok(alias, `Installed host export ${name} changed`);
  return (await import(pathToFileURL(join(dist, file))))[alias];
}
const [deriveInbound, inboundPair, createHookRunner, channelFields, identityFields] = await Promise.all([
  hostExport('message-hook-mappers', 'deriveInboundMessageHookContext'),
  hostExport('message-hook-mappers', 'toPluginInboundClaimPair'),
  hostExport('hook-runner-global', 'createHookRunner'),
  hostExport('hook-agent-context', 'buildAgentHookContextChannelFields'),
  hostExport('hook-agent-context', 'buildAgentHookContextIdentityFields'),
]);
const ACTOR = '+15550000001', BOT = '+15550000099';
const SESSION = `agent:shared-tools:whatsapp:test:direct:${ACTOR}`;
function transport(word, overrides = {}) {
  // WhatsApp prepareWhatsAppInboundContext: reply.to is the recipient/bot;
  // reply.originatingTo is the admitted conversation (sender for a DM).
  return { From: ACTOR, To: BOT, OriginatingTo: ACTOR, SenderId: ACTOR,
    OriginatingChannel: 'whatsapp', Provider: 'whatsapp', Surface: 'whatsapp',
    AccountId: 'test', SessionKey: SESSION, AgentId: 'shared-tools',
    BodyForCommands: word, RawBody: word,
    Body: `[WhatsApp envelope and old history: doctor]\n${word}`,
    BodyForAgent: `[Agent envelope]\n${word}`, MessageSid: 'synthetic-inbound', ...overrides };
}
function dispatchPair(raw) {
  const h = deriveInbound(raw), claim = inboundPair(h).context;
  // Exact public projection in dispatch-from-config runBeforeDispatch. Rich
  // mapper media/provenance fields are deliberately not part of this hook.
  const event = { messageId: h.messageId, content: h.content,
    body: h.bodyForAgent ?? h.body, channel: h.channelId,
    sessionKey: h.sessionKey, senderId: h.senderId, isGroup: h.isGroup,
    timestamp: h.timestamp, replyToId: h.replyToId, replyToIdFull: h.replyToIdFull,
    replyToBody: h.replyToBody, replyToSender: h.replyToSender, replyToIsQuote: h.replyToIsQuote };
  const context = { messageId: h.messageId, channelId: h.channelId,
    accountId: h.accountId, conversationId: claim.conversationId,
    sessionKey: h.sessionKey, senderId: h.senderId,
    replyToId: h.replyToId, replyToIdFull: h.replyToIdFull,
    replyToBody: h.replyToBody, replyToSender: h.replyToSender, replyToIsQuote: h.replyToIsQuote };
  return { event, context };
}
function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'household-native-dispatch-'));
  const registry = { typedHooks: [] }, services = [], policies = [];
  registerRouter({ pluginConfig: { statePath: join(root, 'modes.sqlite3'),
    agentIds: ['shared-tools'], accountIds: ['test'], allowedSenders: [ACTOR],
    tools: { groceries: ['grocery_show'], doctor: ['doctor_search'] } },
    registerCommand() {}, registerService: s => services.push(s),
    registerTrustedToolPolicy: p => policies.push(p),
    on: (hookName, handler, options) => registry.typedHooks.push({
      pluginId: 'household-router', hookName, handler, ...options }),
  });
  const runner = createHookRunner(registry, { catchErrors: false });
  t.after(() => { services.forEach(s => s.stop()); rmSync(root, { recursive: true, force: true }); });
  let ordinal = 0;
  return {
    runner,
    async dispatch(raw) { const { event, context } = dispatchPair(raw); return runner.runBeforeDispatch(event, context); },
    async turn(prompt) {
      const attempt = { runId: `native-turn-${++ordinal}`, trigger: 'user',
        messageChannel: 'whatsapp', messageProvider: 'whatsapp', agentAccountId: 'test',
        senderId: ACTOR, chatId: ACTOR, messageTo: ACTOR, sessionKey: SESSION };
      const ctx = { runId: attempt.runId, agentId: 'shared-tools', sessionKey: SESSION,
        trigger: 'user', inputProvenance: { kind: 'external_user' },
        ...channelFields(attempt), ...identityFields(attempt) };
      const result = await runner.runBeforePromptBuild({ prompt, messages: [] }, ctx);
      return { prompt: result.appendSystemContext,
        policy: name => policies[0].evaluate({ toolName: name, params: {} }, {
          agentId: 'shared-tools', runId: attempt.runId,
          requester: { channel: 'whatsapp', accountId: 'test', senderId: ACTOR } }) };
    },
  };
}

test('actual mapper claim context uses bot To, so sender proof must use the trusted direct session', () => {
  const { event, context } = dispatchPair(transport('lista'));
  assert.equal(deriveInbound(transport('lista')).conversationId, ACTOR);
  assert.equal(context.conversationId, BOT);
  assert.notEqual(context.conversationId, ACTOR);
  assert.equal(event.content, 'lista');
  assert.equal(event.isGroup, false);
});

for (const [word, mode, reply] of [
  ['lista', 'groceries', /Lista ativa/], ['GROCERIES', 'groceries', /Groceries active/],
  [' médico ', 'doctor', /médicos ativa/], ['me\u0301dico', 'doctor', /médicos ativa/],
  ['medico', 'doctor', /médicos ativa/], ['doctor', 'doctor', /Doctor search active/],
]) test(`real claiming hook persists standalone ${JSON.stringify(word)} for next ordinary turn`, async t => {
  const f = fixture(t);
  assert.equal(f.runner.hasHooks('before_dispatch'), true);
  const result = await f.dispatch(transport(word));
  assert.equal(result.handled, true); assert.match(result.text, reply);
  for (const message of ['Show me the current lists', 'Pode mostrar agora?']) {
    const turn = await f.turn(message);
    assert.match(turn.prompt, new RegExp(`mode: ${mode}`));
    assert.equal(turn.policy(mode === 'groceries' ? 'grocery_show' : 'doctor_search'), undefined);
    assert.equal(turn.policy(mode === 'groceries' ? 'doctor_search' : 'grocery_show').block, true);
  }
});

test('quoted history, longer sentences and body/transcript words never supply a mode', async t => {
  const f = fixture(t);
  await f.dispatch(transport('doctor'));
  for (const word of ['Please use lista', '"lista"', '> lista', 'lista\ndoctor', 'Show lists']) {
    assert.equal(await f.dispatch(transport(word, {
      ReplyToBody: 'lista', ReplyToIsQuote: true, Transcript: 'lista', BodyForAgent: 'lista',
    })), undefined);
  }
  assert.match((await f.turn('Pode continuar?')).prompt, /mode: doctor/);
});

test('missing/foreign transport identity and routing never claim or change mode', async t => {
  const f = fixture(t);
  await f.dispatch(transport('doctor'));
  for (const override of [
    { SenderId: undefined }, { SenderId: '+15550000002' },
    { AccountId: undefined }, { AccountId: 'other' },
    { OriginatingChannel: 'telegram' },
    { SessionKey: 'agent:shared-tools:whatsapp:test:direct:+15550000002' },
    { SessionKey: `agent:shared-tools:whatsapp:other:direct:${ACTOR}` },
    { SessionKey: `agent:shared-tools:whatsapp:group:${ACTOR}` },
    { SessionKey: `${SESSION}:thread:spoof` },
    { OriginatingTo: '123@g.us', GroupSubject: 'Household' },
    { SessionKey: undefined }, { SessionKey: 'agent:main:main' },
    { SessionKey: 'agent:developer:main' },
  ]) assert.equal(await f.dispatch(transport('lista', override)), undefined);
  assert.match((await f.turn('Continue')).prompt, /mode: doctor/);
});

test('a newly persisted native switch revokes prior run tool authority', async t => {
  const f = fixture(t);
  const old = await f.turn('Show list');
  assert.equal(old.policy('grocery_show'), undefined);
  await f.dispatch(transport('doctor'));
  assert.match(old.policy('grocery_show').blockReason, /mode changed/i);
  const current = await f.turn('Find a doctor');
  assert.equal(current.policy('doctor_search'), undefined);
});
