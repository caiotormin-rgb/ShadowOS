# Grocery List Tool

OpenClaw plugin for `core/agent_api.py`, which reuses the existing grocery engine.
The configured `scriptPath` remains `core/grocery.py`; the bridge calls its
sibling `agent_api.py`. Python and database paths are operator-controlled.

Seven optional tools: `grocery_show`, `grocery_add`, `grocery_mark`,
`grocery_remove`, `grocery_trip`, `grocery_activity`, `grocery_preferences`.
Explicitly allowlist those names. Do not add deprecated `grocery_list` alongside
them; it routes through the same compact API only for older clients.

All registered tools validate their schema at execution. None accepts actor,
DB path, confirmation authority or retry ID. Fixed `execFile` argv supplies the
trusted sender and JSON request, without a shell. Every call propagates `lang`.
Public responses contain a compact localized `reply`, status, assumptions and
optional clarification/confirmation fields. Subprocess diagnostics are never
returned to the model. The old `runGrocery` and argv helpers remain exported for
legacy CLI tests only; they are not a registered execution path.

Tools exist only for an allowlisted sender on the exact configured WhatsApp
account. Missing accounts, group JIDs and alternate identity namespaces fail
closed. Family DB membership is required by the API. Private storage uses a
hash of the actor and trusted `--private` provisioning; the API permanently
binds that database to one actor.

Removal tools only create a preview. `/remover CODE` is a native authenticated
command, independent of the model, with strict account/sender/direct-conversation
checks and a
single-use expiring backend confirmation. Private previews include
`/remover CODE private`. The complete command must match. Optional `accessDbPath`
enables a fresh `grocery.use` grant check using the shared AccessClient before
confirmation. The AccessClient module is dynamically loaded from the sibling
`tools/access/plugin/dist/client.js`; a missing module/store denies. Pilot
configuration should supply `accessDbPath`; omission preserves old allowlist
behavior for existing deployments.

Retry IDs derive from host session and tool-call ID. This protects repeated
execution of that same call. The SDK does not expose an inbound message ID in
tool context, so independently repeated WhatsApp messages are not deduplicated
by this bridge. Never claim end-to-end exactly-once delivery.

## Build and verify

Build `tools/access/plugin` first when using grant checks, then:

```bash
npm ci
npm run plugin:build
npm run plugin:validate
```

This is a mixed tool/command plugin using `definePluginEntry`. Its manifest is
maintained explicitly and checked against registrations in tests; the tool-only
`openclaw plugins build` generator is intentionally not used. `plugin:validate`
runs TypeScript and all plugin tests. It does not attest deployment readiness.
`node_modules` and `dist` are untracked. Build before deploying. Follow the
reviewed rollout/rollback instructions; do not blindly restart the gateway.

For isolated evaluations import `narrowTools`, `toolRequest(name, input)`, and
`runAgentApi(request, actor, config, {requestId})`. These reuse production schema
and backend mapping but bypass the host factory context, so they are not an
end-to-end WhatsApp runtime test. Use only synthetic databases and enrolled
synthetic identities. See `docs/shadow-0914/GROCERY-PLUGIN.md` for handoff details.
