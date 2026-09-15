# Validation log

## Final offline checks

Baseline: a4280e29; isolated worktree codex/shadow-0914. No production member
databases or conversations used as test fixtures. Public Doctor Census ZIP
fixture copied into the worktree (initial missing-fixture failure resolved).

| Suite | Passed |
|---|---:|
| Grocery Python | 394 |
| Doctor Python | 33 |
| Access plugin | 25 |
| Grocery plugin | 31 |
| Doctor plugin | 10 |
| Router | 11 |
| Installed-host callback composition | 2 |
| Contract harness offline | 6 |
| Candidate configuration builder | 8 |
| Total | 520 |

Access, Grocery and Doctor TypeScript builds and router syntax checks pass.
Doctor failure-path tests deliberately log caught exceptions; suite exit is 0.
Full Python logs: /tmp/shadow-0914-grocery-final.log and
/tmp/shadow-0914-doctor-final.log. Callback tests use actual 2026.9.4 hook-context
construction, plugin callbacks and temporary real SQLite state. They mock the
transport and subprocess delivery; they are not an inbound WhatsApp test.

Reproduce from repository root:

```sh
PYTHONPATH=tools/grocery-list/core python3 -m unittest discover -s tools/grocery-list/core/tests -p 'test_*.py'
(cd tools/doctor-search/core && PYTHONPATH=. python3 -m unittest discover -s tests -p 'test_*.py')
(cd tools/access/plugin && npm run build && npm test)
(cd tools/grocery-list/plugin && npm run build && npm test)
(cd tools/doctor-search/plugin && npm run build && npm test)
node --test tools/household-router/test/*.test.mjs
node --test tools/household-config/test/integration.mjs
node --test tools/grocery-list/bench/contract/test.mjs
python3 tools/household-config/test_prepare.py
```

## Model readiness versus workflow evidence

Stateless local READY probes passed Sol, Terra, Luna and Gemini Flash Lite.
Concurrent CLI startup caused state-lifecycle lock collisions; sequential
retries passed. This is availability evidence only, not workflow correctness
or full-turn latency. No production model was changed.

See MODEL-EVALUATION.md for the bounded Gemini tool-contract pilot, raw artifact
paths and manual review. Sol remains configured. Live configuration/service
verification and approved routing activation are recorded in DEPLOYMENT.md.

## Router activation after explicit approval

Repeated 11 router tests plus 2 actual-host callback integration tests: all 13
passed. Candidate/live config validate without warnings. Runtime inspection
shows loaded router, four native commands, three typed hooks, household-mode
contract, both hook permissions true, and no diagnostics. Gateway restarted;
health reports router loaded without plugin errors and both WhatsApp accounts
connected. Initial immediate health check raced startup; the next passed.
No actual inbound WhatsApp smoke or outbound message was performed.
