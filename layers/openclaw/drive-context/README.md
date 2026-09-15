# drive-context

A rebuildable local read model of Google Drive **metadata** for the OpenClaw
agent. Sibling of [`../mail-context`](../mail-context), built to the plan in
[`docs/plans/openclaw-google-drive-context.md`](../../../docs/plans/openclaw-google-drive-context.md).

> **Nothing here is live.** There is no Drive OAuth grant, no token, no
> database, no timer and no agent tool. The current OAuth grant on this machine
> is `gmail.readonly` only. Not one byte of real Drive metadata has been read,
> and no code in this directory has ever contacted Google. Every test runs
> against synthetic fixtures.

## What it does, and the one thing it cannot do

The index answers "where does that document live", "what have I touched this
week" and "what is shared outside my account" from SQLite, without a Drive call.

It cannot read file content. Not because the code declines to, but because the
requested scope is `drive.metadata.readonly`, under which Google itself rejects a
content read. `drive.readonly` would have worked for an index, and that is
exactly why it was not chosen: **prefer a grant where the dangerous capability is
absent over one where it is merely uncalled.** The second depends on every future
edit to this package continuing to behave; the first does not.

## Layout

| Path | What it is |
|---|---|
| `schema.sql` | The read model. No column stores content, an export, a thumbnail, a checksum, or a grantee address. |
| `drivectx/drive.py` | Transport. Anchored path allowlist, query-parameter allowlist, forbidden-element regex, field-mask check. |
| `drivectx/sharing.py` | Sharing reduced to a state plus counts. Re-argue before changing. |
| `drivectx/store.py` | Write side: idempotent upserts, parent edges, tombstones, cursor, reconciliation. |
| `drivectx/sync.py` | Initial listing, incremental changes, page-token-expiry resync. |
| `drivectx/query.py` | The read-only surface handed to the agent, including DAG path resolution. |
| `drivectx/syncrun.py` | What the timer calls. |
| `drivectx/authorize.py` | One-time operator consent flow. |
| `systemd/` | Tracked unit text. **Not installed.** |
| `tests/` | stdlib `unittest`, synthetic fixtures only. |

## It depends on mail-context

This is a real, deliberate, one-way coupling: **drive-context does not run
without mail-context on `PYTHONPATH`.**

```python
from mailctx.preflight import run, require_for_initial_sync   # the encryption gate
from mailctx.auth import AccessTokenProvider, TokenStore, ...  # the OAuth broker
```

Reimplementing either would mean a second copy of the same security-critical
logic, drifting quietly apart. So `systemd/drive-context-sync.service` sets
`PYTHONPATH` to both layers, and this paragraph exists so that requirement is
read rather than discovered from a traceback. Removing drive-context must not
disturb mail-context; the dependency never runs the other way. A shared package
extraction is planned and deliberately not done yet.

One Desktop OAuth client is shared so the operator consents once. **A shared
client is not a shared capability**: this layer keeps its own token file
(`~/.config/mail-context/drive-token.json`), its own scope, and its own
transport ceiling.

## The four properties the design rests on

**Content is absent, not merely unread.** `tests/test_no_content_path.py` greps
the package for `alt=media`, the export endpoint, `uploadType`,
`acknowledgeAbuse`, thumbnail and checksum fields, and any OAuth scope outside
`drive.metadata.readonly`. `tests/test_drive_ceiling.py` walks the transport's
AST and fails if any `urllib.request.Request` is built with a method other than
GET. `tests/test_schema.py` inspects the live schema for content-like columns.

Note where Drive differs from Gmail: **its content door is a query parameter, not
a path.** `?alt=media` on the ordinary `files.get` endpoint returns bytes. A
path-only allowlist of the kind mail-context needs would let that straight
through, so the ceiling here checks parameter names, the assembled request line,
and the field mask as well.

**No grantee address ever enters the process.** Sharing is stored as a state plus
counts. The minimization happens at the *request*: the field mask asks for
`permissions(type,role,allowFileDiscovery,deleted)` and never for an address, so
there is nothing to store, log or leak. Storing the full permission list would
build a durable local social graph of every colleague, family member and
contractor the account has ever shared a file with — none of whom consented to
being indexed on torm, on a disk that is not encrypted.

Owner email **is** retained, and that asymmetry is the decision: it is one
identity per file rather than an unbounded set, it is required to answer "is this
mine or someone else's", and Drive already shows it on every listing row.

`unknown` is a real sharing state, not an error. Under
`drive.metadata.readonly` the permissions collection is invisible for files
shared *to* this account, and the index must say so rather than imply `private`.

**Paths are computed, never stored.** Drive's parents are a DAG in principle and
an untrusted graph in practice: multiple parents, orphans, edges pointing at
folders this account cannot see, and no promise about cycles. `drive_parents`
holds edges; `DriveContext.path()` resolves upward with a depth cap of 32, a cap
of 8 returned paths, per-path visited tracking, and explicit terminal markers —
`my_drive`, `orphan`, `unresolved_parent`, `cycle`, `depth_limit`. It returns
**every** path a multi-parent file has, because collapsing them to the first
would be a lie about the data.

**Shared drives are excluded by a named setting.** `include_shared_drives`
defaults to `false`, is asserted to default to `false` by a test, and is enforced
twice: as request parameters, and per row on anything carrying a `driveId`.

## Preflight: waived, not passed

`mailctx.preflight` refuses the initial sync unless the index sits on an
encrypted filesystem. torm's root is plain ext4 on `/dev/nvme0n1p5` with no LUKS
layer and a plaintext swapfile, so the gate **fails on its own merits** and
currently passes only because an operator waiver file exists:

```
[PASS] encryption_at_rest: WAIVED by operator (Operator waiver of the
       encryption-at-rest preflight gate.) -- was: /dev/nvme0n1p5 is plaintext
       (part); 0700/0600 modes do not survive disk theft or offline access
[WARN] swap_encrypted: plaintext swap: /swap.img
[PASS] free_space: 793.2 GiB free (need 2)
[PASS] sqlite_fts5: available (sqlite 3.45.1)
[PASS] permissions: ~/.local/state/drive-context not created yet; will be made 0700
ok = True
```

The waiver is reported by `status()` in every response and printed by the CLI on
every run, so it cannot be quietly forgotten. Routing around the gate instead of
waiving it would make this a different layer from the one that was approved.

## Freshness, always

Every response from `DriveContext` carries `last_success_at`, `age_seconds`,
`status`, `error_class`, `is_stale` and a human-readable description. Stale
context may be returned; it may never be returned without naming its age.
`status()` additionally reports `initial_complete`, because "the index has 400
files" means something different when the first listing never finished.

## Names are data

A folder can be named `ignore previous instructions`; a file can be named
`" OR 1=1` or `NEAR(x y)`. Search text is stripped of FTS operators and re-quoted
before it reaches SQLite, and Drive strings are returned as row values that never
become system instructions or alter tool policy.

## Running the tests

```bash
cd layers/openclaw/drive-context
PYTHONPATH=../mail-context python3 -W error::DeprecationWarning -m unittest discover -s tests -t .
```

## Operating it, once it is live

```bash
python3 -m drivectx.authorize --port 8765     # one-time consent
python3 -m drivectx.syncrun --max 2000        # bounded validation load
python3 -m drivectx.syncrun                   # what the timer runs
python3 -m drivectx.syncrun --status
python3 -m drivectx.syncrun --recheck <id>    # live re-check of one file
```

## Known open questions

- `drive.metadata.readonly` is a *restricted* scope. An OAuth client left in
  Testing publishing status issues refresh tokens that expire after seven days,
  which would silently break a four-times-daily timer within a week. Verify
  refresh-token durability from the noninteractive service environment before
  building on it.
- The scope argument has not yet been verified empirically. Attempting one
  download and one export against an owned file, and recording the rejection, is
  the proof the whole design rests on and it has not been done.
