#!/usr/bin/env bash
# Would publishing this leak personal data? Complements scan-secrets.sh, which
# only matches credential shapes and passes a file full of home addresses.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1

PUBLISHABLE=(layers docs station README.md AGENTS.md)
PATTERNS='[0-9]{3}\.[0-9]{3}\.[0-9]{3}-[0-9]{2}|[0-9]{3}-[0-9]{2}-[0-9]{4}|[0-9]{2}\.[0-9]{3}\.[0-9]{3}/[0-9]{4}-[0-9]{2}'
NAMES='[a-zA-Z0-9._%-]+@(gmail|protonmail|outlook|yahoo|hotmail)\.[a-z.]+'

fail=0
echo "== scanning publishable paths for personal data =="
for pat_name in "government/tax IDs:$PATTERNS" "personal email addresses:$NAMES"; do
  label=${pat_name%%:*}; pat=${pat_name#*:}
  hits=$(grep -rInE "$pat" "${PUBLISHABLE[@]}" 2>/dev/null \
         | grep -v 'owner@example.com' \
         | grep -vE '111\.222\.333-96|12\.345\.678/0001-95' || true)
  if [ -n "$hits" ]; then
    printf '\033[31mFAIL\033[0m  %s\n' "$label"; echo "$hits" | head -8 | sed 's/^/      /'
    fail=1
  else
    printf '\033[32m ok \033[0m  %s\n' "$label"
  fi
done

echo
echo "== paths that must NEVER be published =="
for p in records memory; do
  [ -e "$p" ] && echo "      $p/  (excluded by policy — see docs/PUBLISHING.md)"
done

exit $fail
