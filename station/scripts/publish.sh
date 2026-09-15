#!/usr/bin/env bash
# Create the private GitHub remote and push. Refuses to push if the secret
# scan fails. Safe to re-run: skips creation if the remote already exists.
#
# Requires: gh authenticated (`gh auth login`).
# Usage: station/scripts/publish.sh [repo-name]   (default: torm-workspace)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
NAME="${1:-torm-workspace}"

command -v gh >/dev/null || { echo "gh not on PATH (try: export PATH=\"\$HOME/.local/bin:\$PATH\")"; exit 2; }
gh auth status >/dev/null 2>&1 || { echo "gh is not authenticated. Run: gh auth login"; exit 2; }

echo "== secret scan =="
./station/scripts/scan-secrets.sh || { echo "REFUSING TO PUSH — secrets found"; exit 1; }

if git remote get-url origin >/dev/null 2>&1; then
  echo "== remote 'origin' already set: $(git remote get-url origin) =="
else
  echo "== creating PRIVATE repo $NAME =="
  gh repo create "$NAME" --private --source=. --remote=origin
fi

echo "== pushing $(git branch --show-current) =="
git push -u origin "$(git branch --show-current)"

echo "== verifying =="
gh repo view --json nameWithOwner,visibility,defaultBranchRef \
  -q '"repo: \(.nameWithOwner)  visibility: \(.visibility)  default: \(.defaultBranchRef.name)"'
echo "local HEAD:  $(git rev-parse --short HEAD)"
echo "origin HEAD: $(git rev-parse --short origin/$(git branch --show-current))"
