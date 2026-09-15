# What may be published

ShadowOS is a public export of two private repositories. Personal records,
agent memory and credentials stay in the private sources. This document
separates that publication boundary from the operational conventions retained
elsewhere in the export.

## The boundary

| Path | Publishable | Why |
|---|---|---|
| `layers/` | **yes** | code and tests; identifiers in fixtures are synthetic |
| `docs/` | **yes** | plans and procedures describe shape, not personal detail |
| `station/` | **yes** | operational scripts |
| `tools/` | **yes, after filtering** | custom tools and synthetic fixtures; private configuration, databases and recordings excluded |
| `records/**/README.md` | **no** | findings name real properties, prices, people |
| `records/**/outputs/`, `records/**/work/` | **no** | gitignored: correspondents, message snippets, document inventories |
| `memory/` | **no** | a snapshot of agent memory about the operator |

Records are the evidence trail and are *supposed* to be specific — that is
what makes them useful, and what makes them private. Do not sanitise them;
exclude them.

## Rules that keep the line

- **Identifiers in code and tests are synthetic.** Format-valid, never real.
  Two real ones reached fixtures on 2026-08-24 and were replaced.
- **Plans describe shape; records hold specifics.** "Surfaced a forgotten
  property" belongs in a plan. The address belongs in a record.
- **`scan-secrets.sh` does not check for personal data.** It matches credential
  shapes — API keys, tokens, Google client secrets. It will happily pass a file
  full of home addresses. Run `publish-check.sh` as well.

## Before publishing anything

```bash
station/scripts/scan-secrets.sh     # credentials
station/scripts/publish-check.sh    # personal data
```

The exported `publish-check.sh` currently omits `tools/`, and `publish.sh`
only invokes the credential scanner. A successful exit from those scripts is
not a complete publication review. Inspect all intended changes, including
new paths, and keep private records outside this public repository.

The private source repos contain personal implementation records in their
history. Continue exporting selected content from them; do not push their
history to ShadowOS. The runbooks and machine conventions retained here are
operational references, not permission to publish private source material.
