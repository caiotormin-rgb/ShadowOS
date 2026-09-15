# Access integration

The access plugin still defaults to monitor mode. New `enforceAgents` enables
actual denial only for selected agent IDs. The rollout selects shared-tools;
owner main/CLI remains monitored because legitimate local turns lack a channel
requester. Missing sender, unknown person, inactive grant, unmapped tool and
unreadable store deny guest calls. Successful checks return no override, so
other host/plugin restrictions remain authoritative.

Seven narrow Grocery tools map to the existing grocery.use grant. No new grant
rows or memberships are needed. Current aggregate coverage was checked read-only:
three members and one owner each have grocery.use and doctor.request.

Native approval commands bypass tool policies. Grocery /remover and Doctor /ok
therefore optionally recheck the access store through the shared AccessClient.
Set accessDbPath in both plugin configs during deployment. Missing module/store
or missing/inactive person/grant denies approval. Both require an exact WhatsApp
account and host-authorized sender. This does not add an invite/revoke UI.

After explicit hook approval and activation, the router adds independent domain
restrictions: Doctor tools are blocked while
Grocery is active and vice versa. Escalation cannot expand tools or grants.
Commands select modes, not identities or roles. /ok and /remover intentionally
can approve their correctly scoped pending task independent of active mode;
their own token and grant checks remain mandatory.

Tests: 25 access tests; Doctor command tests cover absent/wrong account, host
authorization, and unavailable access backend. Grocery tests exercise its own
native confirmation bridge. Full test results are in VALIDATION.md.

Dependency: build tools/access/plugin before the Grocery and Doctor plugin
checks. Native command modules locate the sibling compiled AccessClient from
operator-configured core scriptPath, never a model-supplied path.

## Deferred tool broker correction

The actual built-in harness exposes tool_search/tool_describe/tool_call. Initial
native replay showed the policy denied tool_search as unmapped, preventing
new Grocery tool discovery. Current enforcement accepts tool_search only for an
authenticated household grant holder. Describe/call decode exact IDs owned by
grocery-list-tool or doctor-search-tool and check the corresponding current
grant. Invalid ownership, unknown capabilities and malformed IDs are denied.
The host invokes the wrapped canonical tool through the policy again; router
scope/revision checks also apply to wrappers and their targets. No general
broker bypass was added. Main/other monitor-only surfaces retain their behavior.

Twenty-nine Access tests and 24 composed router/host-context tests pass. Actual
native Sol replay verified search then fresh Grocery store/list reads without
tool errors or data changes. See ROUTING-REGRESSION.md and NATIVE-REPLAY.md.
