#!/usr/bin/env bash
# Assert that no tracked systemd unit hardcodes one account's home directory.
#
# A unit that names /home/<someone> deploys correctly only for that account, so
# every other deployment depends on an operator remembering a rewrite step. That
# is how mail-context's installed copy drifted two releases behind the repo
# (records/2026-08-25-mail-context-unit-paths) and how the gateway unit came to
# describe an account boundary that was not the one running
# (records/2026-08-25-gateway-unit-drift).
#
# systemd expands %h to the home of whichever user manager loads the unit. Use
# it. Comments are exempt -- explaining the history is not hardcoding a path.
#
# Read-only. Needs no sudo. Exits non-zero if any assertion fails.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1

fail=0
warn=0
checked=0

units=$(find station layers -type f \
  \( -name '*.service' -o -name '*.timer' -o -name '*.socket' -o -name '*.mount' \) \
  ! -name '*.disabled' ! -name '*.reference' 2>/dev/null | sort)

# Only real directives count -- a comment explaining the history is not a path.
directives() { grep -nvE '^[[:space:]]*(#|;|$)' "$1"; }

echo "== no tracked unit may name Caio's home =="
echo "   (these layers deploy to the isolated \`openclaw\` account, which cannot"
echo "    traverse /home/caio at all -- such a unit is broken or boundary-breaking)"
echo

for u in $units; do
  checked=$((checked + 1))
  hits=$(directives "$u" | grep -E '/home/caio' || true)
  if [ -n "$hits" ]; then
    printf '\033[31mFAIL\033[0m  %s\n' "$u"
    echo "$hits" | sed 's/^/        /'
    fail=1
  fi
done
[ "$fail" -eq 0 ] && printf '\033[32m ok \033[0m  %s unit(s), none names /home/caio\n' "$checked"

echo
echo "== advisory: units that deploy to one account only =="

for u in $units; do
  hits=$(directives "$u" | grep -E '/home/[a-z_][a-z0-9_-]*' || true)
  if [ -n "$hits" ]; then
    n=$(echo "$hits" | wc -l)
    printf '\033[33mwarn\033[0m  %s (%s hardcoded path(s))\n' "$u" "$n"
    warn=$((warn + 1))
  fi
done
if [ "$warn" -eq 0 ]; then
  printf '\033[32m ok \033[0m  every unit uses %%h and deploys to any account\n'
else
  echo
  echo "      %h expands to the home of whichever user manager loads the unit."
  echo "      mail-context/systemd/ is the worked example. Advisory only —"
  echo "      these name their own deploy target, so they are not wrong today."
fi

if [ "$checked" -eq 0 ]; then
  printf '\033[31mFAIL\033[0m  no units found — the search path is wrong, not the repo clean\n'
  fail=1
fi

echo
[ "$fail" -eq 0 ] && echo "no unit depends on a remembered path rewrite to reach the right account" \
                  || echo "a tracked unit names /home/caio — fix it before it is deployed"
exit $fail
