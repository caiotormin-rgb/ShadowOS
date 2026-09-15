# What may be published

This repo has a GitHub remote. Git history is permanent, so the question is
settled once per file, not per push.

## The boundary

| Path | Publishable | Why |
|---|---|---|
| `layers/` | **yes** | code and tests; identifiers in fixtures are synthetic |
| `docs/` | **yes** | plans and procedures describe shape, not personal detail |
| `station/` | **yes** | operational scripts |
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

If publication ever happens, the practical route is a filtered export of
`layers/`, `docs/`, `station/` and the top-level README — not a push of this
repo, whose history already contains record READMEs.
