# Security

The gateway accepts chat messages and reads untrusted external content.
A sender must only reach the tools and records authorized for that person;
content from mail, documents or websites must not grant new authority.

This document describes the September 2026 export's controls and deployment
assumptions. It is not a fresh audit of the live gateway.

| Surface | Read/write boundary |
|---|---|
| Personal Shadow on Telegram | Broader configured owner tools, including Firecrawl, grocery functionality and owner-reported support-email workflows; permissions depend on the selected tool |
| Invited-user Shadow on WhatsApp | Grocery and Doctor tools only; broader OpenClaw capabilities deliberately withheld |
| Mail, ledger and document MCP services | Read-only queries for the owner |
| Household grocery tools | Conversational adds and purchase updates; typed confirmation for removal |
| Doctor outreach (early testing) | Draft preview and requester `/ok CODE` before sending |
| Calendar ICS guardrail | Scheduled event creation and updates, without per-event confirmation; no attendees copied or update notifications requested |

The owner reports that personal Shadow completed a product-support inquiry
and followed up when a reply arrived. That establishes a personal-agent
email workflow, not a sending capability in the context services below.
The specific connector and its approval/attachment policy were not supplied
for this review; the Doctor approval contract should not be inferred for it.

## 1. Context services have no account-write path

Sending is absent from the Gmail context layer by construction. The
unconnected Calendar read-model layer and retired Drive metadata layer have
similarly restricted transports. These guarantees do not cover the separate
Doctor mailbox, calendar automation or the personal agent’s other tools.

- **OAuth grant.** Gmail is `gmail.readonly` only. Calendar would be
  `calendar.calendarlist.readonly` plus `calendar.events.readonly`, which is
  narrower than the write-free `calendar.readonly` because that one also
  reads settings and ACLs the layer never touches. Drive was
  `drive.metadata.readonly` rather than the workable `drive.readonly`, on the
  principle that a grant where the dangerous capability is absent beats one
  where it is merely uncalled.
- **Transport ceiling.** Each layer has one module that can open a socket. It
  exposes a single `_get` with a hardcoded method, an anchored regex allowlist
  of API paths, and a denylist of mutating segments
  (`send|trash|modify|drafts|watch|...`). Drive's content door is a query
  parameter rather than a path (`?alt=media`), so that ceiling also
  allowlists parameter names and checks the percent-decoded request line.
- **Schema.** No outbox, no draft table, no body column. There is nothing to
  write *from*.
- **Tool surface.** The MCP servers open SQLite with `mode=ro` and expose only
  read methods.

Each of those layers has a test. `test_no_send_path.py` walks the package for
any non-GET request or any scope outside the approved tuple.
`test_no_write_path.py` in calendar-context drives a full sync through a
recording transport and asserts every request received was a GET.
`test_no_content_path.py` in drive-context greps the package for the content
endpoints, and includes `test_the_tripwire_actually_fires`, a meta-test proving
the grep can fail. The tests are the boundary. Do not relax them to make a
feature easier.

A token outlives the code that requested it, so `scopes_are_readonly` re-checks
the *stored* grant at use time, and a wider grant than requested is refused at
authorization.

## 2. The agent runs in a box

- **Isolated Linux account.** The gateway runs as `openclaw` (uid 1001): no
  login shell, no sudo, no supplemental groups, home mode 0700. The operator's
  home is `drwxr-x---` and shares no group with it, so the gateway cannot
  traverse it. A plaintext personal mail archive sits in the operator's home
  precisely because of that kernel boundary.
- **Loopback only.** The gateway listens on `127.0.0.1:18789` and `[::1]`,
  verified unreachable from the LAN address. Token auth applies even on
  loopback. Remote access is an SSH tunnel, which does not bypass auth. Never
  `gateway.bind lan`, never a public funnel.
- **Asserted, not remembered.** `station/scripts/check-gateway-isolation.sh`
  is read-only, needs no sudo, and fails if the gateway process is not uid
  1001, not on loopback, or could be started from the operator's home by any
  loadable unit in the repo. It runs before every commit.
- **Systemd hardening on context-layer timer units.** `NoNewPrivileges`, `PrivateTmp`,
  `ProtectSystem=strict` with explicit `ReadWritePaths`, `RestrictNamespaces`,
  `MemoryDenyWriteExecute`, `SystemCallFilter=@system-service`. The ledger
  rebuild also gets `PrivateNetwork=true` because it never needs the network.
  Directives that do not work in an unprivileged user manager are documented
  as such rather than left in to look good.
- **No passwordless sudo, by design.** Agents fail closed. When a step needs
  root, the agent writes the exact commands into a handoff file and a human
  runs them at a terminal.

## 3. What the agent is allowed to say

Reading is not the same as repeating. Documents in the catalog carry a tier:

| Tier | Examples | What the agent gets |
|---|---|---|
| 1 | tax forms, identity documents | metadata, extracted fields, a path. **No text.** |
| 2 | contracts, school records | same |
| 3 | correspondence, ordinary notes | excerpts and text |

"What is my tax ID" must not be answerable by anyone who can message the bot.
Search returns enough to *find* the document and not enough to leak it. The
policy is an environment variable (`LIFE_INDEX_AGENT_TEXT`), default `tier3`,
and documents that carry government ID numbers are cataloged as
pointers only: tier, type, date and link, with no extracted text at all.

Cheap privacy filters run first. The Gmail listing query excludes promotional
mail before any id is fetched. Marketing-class mail is excluded from any batch
that would go to a cloud model unless `--include-marketing` is passed
deliberately, and the batch file *is* the egress list: exactly what a cloud
reviewer will see, reviewable before anything is sent.

## 4. Untrusted text is data

A folder can be named `ignore previous instructions`. A file can be named
`" OR 1=1` or `NEAR(x y)`. Every search term is quoted so FTS5 operators are
matched as literal words, and no string from mail, Drive or a document ever
becomes a system instruction. Logs carry ids, counts, durations and error
classes only, never subjects, titles or attendees; one module per layer is
allowed to import `logging` and it whitelists field names. HTTP errors are
mapped to a class and a status, never a response body.

## 5. Secrets

- Secrets live in each tool's own config under `$HOME`, mode 0600, never in
  this repo. `.gitignore` names the live credential files individually as
  defence in depth.
- Token and client-secret objects override `__repr__` and `__str__` to hide
  their values, and there is a test for it. Saving a token leaves no readable
  temp file, tested.
- **Publication checks have limited coverage.** `scan-secrets.sh` checks
  tracked files for credential shapes. `publish-check.sh` checks selected
  paths for tax-ID formats and personal email addresses; its current path
  list omits `tools/`. The exported `publish.sh` invokes only the credential
  scanner. Both checks and an explicit review of exported content are needed;
  neither establishes that a tree contains no personal data. See
  [publication procedure](PUBLISHING.md).
- **Proton Pass, item-scoped.** Coding agents on the machine get their own
  Proton Pass identities with viewer access to individual items, never a whole
  vault. A secret is injected only into the child process that needs it, from
  a mode-0600 env file containing `pass://` references rather than values,
  with a mandatory human-readable audit reason recorded by Proton's agent
  monitor. The wrapper rejects raw-token shapes. Identities are per machine;
  nothing is copied between machines.

## 6. Encryption at rest: waived, not passed

The deployment machine's disk is plain ext4 with a plaintext swapfile. The
preflight gate that checks for encryption at rest **fails on its merits** and
passes only through a recorded operator waiver whose text is surfaced in every
`status()` response, so it cannot be quietly forgotten. The waiver scopes
itself to Gmail metadata, subjects and snippets. It does not cover the attendee
addresses calendar-context would store, which is one reason that layer is not
connected. Remediation paths exist and are tested: `bin/li-encrypt` migrates
the document store to gocryptfs, and `setup-encrypted-vault.sh` builds a
TPM-sealed LUKS vault. The vault script states its own limit: it protects a
stolen or discarded disk, not a stolen machine that can be powered on.

## 7. Sharing the agent with other people

Three invited people use the grocery tools through a second WhatsApp
account. Doctor finder is in early testing, not fully launched. The owner
has deliberately limited this agent to grocery and doctor-finder tools for
security. Personal Shadow on Telegram retains broader configured tools,
including Firecrawl, alongside the same grocery functionality. General web
scraping is not a capability granted to invited users; the Doctor workflow's
bounded provider research does not expose the owner's general tools.

The access controls below apply to the implemented household workflows:

1. **Gateway allowlists.** Two WhatsApp accounts: mine, and a household
   account with a four-number allowlist and groups disabled. A Telegram bot
   with its own allowlist. The command owner is set for both channels, so
   owner-only commands and dangerous-action approvals have a governor.
2. **Grants.** A household access plugin holds people, identities and
   grants (`grocery.use`, `doctor.request`) and registers a trusted tool
   policy. It ran in monitor mode first and now enforces for the shared
   agent. Missing sender, unknown person, inactive grant, unmapped tool or an
   unreadable store all deny. Discovery through the gateway's tool broker is
   allowed only to an authenticated grant holder and only for exact tool ids
   owned by the household plugins.
3. **Domain ceiling.** A mode router keeps one active domain per sender.
   Doctor tools are blocked while Grocery is active and vice versa.
   Switching modes never approves a pending confirmation, and escalation
   cannot widen tools or grants. Model strength is configuration; a stronger
   model never means access to owner files.
4. **Removal and outreach need typed commands.** The model-facing tools
   preview grocery removal and prepare email drafts. Native `/remover CODE`
   and `/ok CODE` handlers perform the approval step for the requester and
   re-check grants when the access store is configured. Grocery confirmation
   codes expire; doctor draft approval codes currently have no expiry check.
   A reproduced concurrency issue can duplicate an approved doctor send, so
   the export does not establish exactly-once delivery.
5. **Requester isolation.** A doctor request is visible only to its
   requester. Intake has no dedicated member-ID or date-of-birth field, but
   patient, specialty and free-text context can contain health information.
   It must be treated as sensitive data. Private
   grocery lists are bound to one actor by hash. Different senders are
   isolated by the host's session scope and by backend ownership, though a
   person's own Grocery and Doctor history share one session.

The mail index, the ledger and the document catalog are not reachable from
the household account at all. A household view of the ledger is planned;
the controls above are the preconditions it will be built on, plus
consent for the people in the data. The Drive layer was retired partly
because indexing every colleague and contractor the account had ever shared
a file with would index people who never consented, on a disk that is not
encrypted.
