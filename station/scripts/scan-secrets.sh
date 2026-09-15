#!/usr/bin/env bash
# Scan tracked files for credential material before pushing anywhere public.
# Usage: station/scripts/scan-secrets.sh   (exit 0 = clean, 1 = findings)
set -uo pipefail
cd "$(git rev-parse --show-toplevel)" || exit 2

fail=0
note() { printf '  %s\n' "$*"; }

echo "== scanning $(git ls-files | wc -l) tracked files =="

# 1. High-signal credential patterns. <REDACTED> placeholders are expected.
patterns=(
  'sk-[A-Za-z0-9_-]{20,}'                    # OpenAI/Anthropic style
  'ghp_[A-Za-z0-9]{36}'                      # GitHub PAT
  'github_pat_[A-Za-z0-9_]{22,}'
  'xox[baprs]-[A-Za-z0-9-]{10,}'             # Slack
  'AKIA[0-9A-Z]{16}'                         # AWS
  '-----BEGIN [A-Z ]*PRIVATE KEY-----'
  'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.'  # JWT
  'GOCSPX-[A-Za-z0-9_-]{20,}'                # Google OAuth client secret
  'ya29\.[A-Za-z0-9_-]{20,}'                 # Google OAuth access token
  '1//[A-Za-z0-9_-]{30,}'                    # Google OAuth refresh token
  '[0-9]{8,}-[a-z0-9]{20,}\.apps\.googleusercontent\.com'  # Google OAuth client ID
)
for p in "${patterns[@]}"; do
  if hits=$(git grep -nIE "$p" -- . 2>/dev/null); then
    echo "!! pattern: $p"; note "$hits"; fail=1
  fi
done

# 2. A known gateway token, verbatim. This used to read Caio's rollback
# bootstrap (~/.openclaw, removed 2026-09-13); production's config is under
# /home/openclaw (0700) and unreadable here, so the check runs only when a
# readable config exists -- and says so when it cannot, instead of passing
# silently. Checks 1 and 3 still catch a token in a "token": "..." field.
tok_file=${OPENCLAW_CONFIG:-$HOME/.openclaw/openclaw.json}
tok=$(grep -oP '"token"\s*:\s*"\K[^"]+' "$tok_file" 2>/dev/null || true)
if [ -n "${tok:-}" ] && [ ${#tok} -ge 16 ]; then
  if git grep -qF "$tok" -- . 2>/dev/null; then
    echo "!! LIVE GATEWAY TOKEN present in tracked files"; fail=1
  fi
else
  note "skipped verbatim gateway-token check: no readable config at $tok_file"
fi

# 3. Any non-redacted auth/token assignment with a long value.
if hits=$(git grep -nIE '"(token|password|apiKey|api_key|secret)"[[:space:]]*:[[:space:]]*"[^"<]{16,}"' -- . 2>/dev/null); then
  echo "!! populated credential field:"; note "$hits"; fail=1
fi

# 4. Files that must never be tracked, regardless of content.
for f in .claude.json .credentials.json openclaw.json token.json authorized_user.json; do
  if git ls-files --error-unmatch "$f" >/dev/null 2>&1 || git ls-files | grep -qx ".*/$f"; then
    echo "!! forbidden file tracked: $f"; fail=1
  fi
done

# 5. Mail/calendar/drive context databases must never be tracked.
if hits=$(git ls-files | grep -E '\.sqlite($|-wal|-shm)' 2>/dev/null); then
  echo "!! context database tracked:"; note "$hits"; fail=1
fi

[ $fail -eq 0 ] && echo "clean — no credential material in tracked files"
exit $fail
