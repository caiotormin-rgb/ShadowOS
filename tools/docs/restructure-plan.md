# Workspace source layout

The root stays small: OpenClaw's canonical agent files remain in place, while
all workspace-owned code lives under `tools/`.

```text
tools/
  grocery-list/
    core/       # Python engine, tests, scripts, docs, and local data
    plugin/     # OpenClaw TypeScript adapter
    skill/      # maintained agent-facing behavior
  media-transcription/
    bin/
    tests/
    benchmarks/
```

`automations/` and `docs/` remain top-level because they are workspace-wide,
not implementations of a particular tool. Runtime-managed directories
(`skills/`, `memory/`, `media/`, `.tmp/`, and `.clawhub/`) remain at their
OpenClaw-defined locations and are not source domains.

## Dependency direction

```text
grocery-list/skill -> grocery-list/plugin -> grocery-list/core
OpenClaw config --------------------------> media-transcription/bin
```

The grocery core does not import its plugin or skill. Runtime paths are
configured explicitly in `~/.openclaw/openclaw.json`.
