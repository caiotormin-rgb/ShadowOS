# Security

The threat model in one sentence: **the gateway is reachable from a chat app,
so anything it can read, anyone who can message it can read.** Every boundary
below follows from that.

## 1. The agent cannot write to your accounts

Sending is absent by construction, not by policy.

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
- **Systemd hardening on every timer unit.** `NoNewPrivileges`, `PrivateTmp`,
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
- **Two scanners gate every push.** `scan-secrets.sh` matches twelve
  credential shapes and has been negative-tested against planted decoys.
  `publish-check.sh` exists because the first one will happily pass a file
  full of home addresses: it matches tax-ID formats and third-party personal
  email addresses. `publish.sh` refuses to push if either fails.
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

The gateway already serves my household, through a dedicated WhatsApp account
set up to be the door into these tools. What they reach today is a shared
grocery list and a doctor finder, neither of which touches the mail index,
the ledger or the document catalog. Those three are reachable only by me.
Calendar and a limited view of the ledger are planned for them, and the
limits are the design problem, not the plumbing.

The decision register defers third-party access to the personal data
tools with the note that it is "an exfiltration primitive with a friendly
face". What has to be true before a family member gets a ledger question
answered:

1. **A verified command owner.** OpenClaw's `commands.ownerAllowFrom` governs
   who can run owner-only commands and approve dangerous actions. DM pairing
   does not make someone the owner; an unpaired-but-unowned bot is a bot
   nobody can govern. The runbook makes verifying it a precondition of
   pairing anyone.
2. **A per-channel allowlist.** A channel with no allowlist will talk to
   anyone who messages it.
3. **Per-requester tool scope.** Today the tier policy is one environment
   variable for every caller. A second user needs a policy keyed on who is
   asking: a family member might get ledger totals for shared vendors and
   nothing from the document catalog.
4. **Consent for the people in the data.** The Drive layer was retired partly
   because building a durable local social graph of every colleague, family
   member and contractor the account had ever shared a file with would index
   people who never consented, on a disk that is not encrypted.

Until those exist, the family account gets the grocery list and the doctor
finder, and the answer to "what did we pay the school" from that account is
no.
