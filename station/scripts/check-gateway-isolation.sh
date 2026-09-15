#!/usr/bin/env bash
# Assert the OpenClaw gateway's account boundary still holds.
#
# The gateway is reachable from WhatsApp and Telegram, so anything it can read,
# anyone who can message it can read. Several decisions depend on it running as
# uid 1001 `openclaw` and being unable to traverse /home/caio — notably the
# plaintext personal mail archive in ~/archive, which was put there on the
# strength of this boundary.
#
# Read-only. Needs no sudo. Exits non-zero if any assertion fails.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1

PORT=18789
fail=0
ok()   { printf '\033[32m ok \033[0m  %s\n' "$1"; }
bad()  { printf '\033[31mFAIL\033[0m  %s\n' "$1"; [ $# -gt 1 ] && printf '        %s\n' "$2"; fail=1; }
skip() { printf '\033[33mskip\033[0m  %s\n' "$1"; }

echo "== live gateway process =="

# Find the gateway without assuming a pid. Match only a process whose argv[0]
# is a node binary — otherwise any shell whose command line merely mentions the
# gateway path (this script's own caller, for one) matches and skews the result.
gw_pid=$(ps -eo pid=,args= | awk '$2 ~ /\/node$/ && $0 ~ /openclaw\/dist\/index\.js gateway/ {print $1; exit}')

if [ -z "$gw_pid" ]; then
  skip "gateway not running — process assertions not evaluated"
else
  gw_uid=$(awk '/^Uid:/{print $2}' "/proc/$gw_pid/status" 2>/dev/null)
  gw_user=$(ps -o user= -p "$gw_pid" 2>/dev/null | tr -d ' ')
  gw_cmd=$(tr '\0' ' ' < "/proc/$gw_pid/cmdline" 2>/dev/null)

  if [ "$gw_uid" = "$(id -u openclaw 2>/dev/null)" ]; then
    ok "runs as openclaw (pid $gw_pid, uid $gw_uid)"
  else
    bad "gateway does not run as openclaw" "pid $gw_pid runs as ${gw_user:-?} (uid ${gw_uid:-?})"
  fi

  case "$gw_cmd" in
    *"/home/openclaw/"*) ok "executes out of /home/openclaw" ;;
    *) bad "gateway executes outside /home/openclaw" "$gw_cmd" ;;
  esac

  case "$gw_cmd" in
    *"/home/caio/"*) bad "gateway command line references /home/caio" "$gw_cmd" ;;
    *) ok "no /home/caio path on the gateway command line" ;;
  esac
fi

echo
echo "== account boundary =="

if id openclaw >/dev/null 2>&1; then
  shared=$(comm -12 \
    <(id -Gn openclaw | tr ' ' '\n' | sort -u) \
    <(id -Gn caio     | tr ' ' '\n' | sort -u))
  if [ -z "$shared" ]; then
    ok "openclaw and caio share no group"
  else
    bad "openclaw and caio share a group" "$(echo "$shared" | tr '\n' ' ')"
  fi
else
  bad "no openclaw account on this machine"
fi

# Others must have no execute bit on /home/caio, or the gateway can traverse it.
home_mode=$(stat -c '%a' /home/caio 2>/dev/null)
if [ -n "$home_mode" ] && [ $(( 8#$home_mode & 8#0001 )) -eq 0 ]; then
  ok "/home/caio is mode $home_mode — not traversable by others"
else
  bad "/home/caio is world-traversable (mode ${home_mode:-?})" \
      "the gateway can reach ~/archive and every other tier-1 path"
fi

echo
echo "== bind =="

listeners=$(ss -ltnH "sport = :$PORT" 2>/dev/null | awk '{print $4}')
if [ -z "$listeners" ]; then
  skip "nothing listening on $PORT"
elif echo "$listeners" | grep -qvE '^(127\.0\.0\.1|\[::1\]):'; then
  bad "port $PORT is bound off-loopback" "$(echo "$listeners" | tr '\n' ' ')"
else
  ok "port $PORT is loopback only"
fi

echo
echo "== repo cannot deploy a caio-homed gateway =="

# A file systemd will actually load is one with a real unit suffix. If any such
# file in the repo points the gateway at /home/caio, someone can install it.
loadable=$(find station layers -type f \
  \( -name '*.service' -o -name '*.socket' -o -name '*.timer' -o -name '*.mount' \) \
  2>/dev/null | sort)

offenders=""
for u in $loadable; do
  if grep -q 'openclaw/dist/index.js gateway' "$u" 2>/dev/null && grep -q '/home/caio' "$u" 2>/dev/null; then
    offenders="$offenders$u"$'\n'
  fi
done

if [ -n "$offenders" ]; then
  bad "a loadable unit in this repo would run the gateway as caio" \
      "$(echo "$offenders" | tr '\n' ' ')"
else
  ok "no loadable unit in this repo runs the gateway from /home/caio"
fi

echo
[ "$fail" -eq 0 ] && echo "boundary intact" || echo "boundary NOT intact — see FAIL lines above"
exit $fail
