# Contributing to ShadowOS

ShadowOS is a personal project shared as source code, synthetic fixtures and
documentation. It runs in a private OpenClaw setup; this export does not
include household data, credentials or a complete installation package.

## Start with a small, reproducible change

Useful starting points include documentation clarity, synthetic conversation
cases, and the known calendar-ordering and Doctor email-concurrency issues
in [validation](docs/VALIDATION.md). Grocery is in use; Doctor is still in
early testing.

For an issue, describe the expected behavior, what happened, and a minimal
example using fictional people and data. For a pull request, explain the
problem, the change and how you checked it. Keep changes focused so a reader
can follow the reasoning.

## Check the relevant part

[Tools](tools/README.md) lists focused test commands and plugin requirements.
[Validation](docs/VALIDATION.md) lists the suites reproduced for this export.
Use temporary stores and fake services when testing; live household access
is not needed for those suites.

For documentation and graphics, check local links and the rendered result.
SVG assets live in [assets/brand](assets/brand); their conventions are in
[visual identity](docs/VISUAL-IDENTITY.md).

## Keep examples safe to share

Use synthetic messages, identities and recordings. Personal records and
production credentials belong outside the repository. Run the existing
credential and publication checks and inspect any new paths; their coverage
limits are documented in [Publishing](docs/PUBLISHING.md).

The source repository's context-sync scripts copy private machine state.
They are operational references, not a prerequisite for a contribution to
this public export.
