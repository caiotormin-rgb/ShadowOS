# Household mode router

The household router keeps a durable mode for each authenticated WhatsApp sender
and account. Grocery is the initial mode. The latest entrypoint accepts a single
word by itself: `lista` / `groceries` for shopping, or `médico` / `medico` / `doctor`
for provider search. Existing `/lista`, `/groceries`, `/medico` and `/doctor`
aliases remain supported. Words inside sentences do not switch modes.

The native dispatcher acknowledges an exact standalone switch without calling a
model. A trusted tool policy restricts operations to the active domain and keeps
the guest agent's existing permissions. Switching does not approve pending
`/remover` or `/ok` confirmations.

Earlier domain routing has been deployed. The new plain-word entrypoint has a
separate verification/deployment record in
[plain mode switches](../../docs/shadow-0914/PLAIN-MODE-SWITCHES.md); check
[deployment state](../../docs/shadow-0914/DEPLOYMENT.md) for the actual live version.

See [implementation, configuration, limitations, and rollout](../../docs/shadow-0914/ROUTING.md). Node 24 required; no test dependencies:

```sh
node --test test/*.test.mjs
```

`config.example.json` is deliberately disabled and contains synthetic pilot
placeholders; it is not a snapshot of the deployed configuration. Existing
conversation/prompt hook approval and activation are recorded in the deployment
handoff. Model overrides are omitted to preserve the configured baseline. Verify
native dispatch identity, acknowledgements and existing tool-policy checks in
the deployed runtime before expanding a rollout.
