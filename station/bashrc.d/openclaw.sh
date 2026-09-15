# OpenClaw production operator command — see ~/workspace/layers/openclaw/AGENTS.md
# Sourced from ~/.bashrc. Tracked in the workspace repo; edit it here, not there.
#
# The binary under ~/.openclaw is the stopped rollback bootstrap. Keep it off
# PATH so it cannot query or start against production with the wrong state and
# Gateway token. An authorized rollback can still invoke it by absolute path.
openclaw-prod() {
  "$HOME/workspace/station/scripts/openclaw-prod" "$@"
}

# The ordinary interactive command intentionally operates production.
openclaw() {
  openclaw-prod "$@"
}
