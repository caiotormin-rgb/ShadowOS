#!/usr/bin/env bash
# refresh.sh — rebuild the ledger from the live mail index, unattended.
#
# Runs as the account that owns the index (production: `openclaw`, via
# systemd/ledger-refresh.timer). Needs no sudo and makes no network calls.
# Every path is relative to $HOME or to this script, so the same file runs
# from the repo for testing and from %h/ledger in production.
#
# The ledger is derived: a failed run leaves the previous ledger in place.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ENRICH=$HERE/../mail-enrichment        # run_extract/build_ledger import from here too
MAIL_DB=${MAIL_DB:-$HOME/.local/state/mail-context/mail-context.sqlite}
STATE=${LEDGER_STATE:-$HOME/.local/state/ledger}
export SENDERS_CSV=${SENDERS_CSV:-$HOME/.local/state/mail-enrichment/senders.csv}
LEDGER_DB=$STATE/ledger.sqlite
# Refuse a build that shrinks the ledger below this share of the current one.
# A timer that pruned the index to 730 days (see mailctl prune_check) would
# otherwise quietly replace 18 years of ledger with two.
MIN_KEEP=${MIN_KEEP:-0.8}

log() { printf '%s  %s\n' "$(date -u +%FT%TZ)" "$*"; }

[ -f "$MAIL_DB" ] || { log "FATAL: mail index $MAIL_DB missing"; exit 1; }
[ -d "$ENRICH" ]  || { log "FATAL: $ENRICH missing (deploy mail-enrichment beside ledger)"; exit 1; }
umask 077
mkdir -p "$STATE/extract" "$(dirname "$SENDERS_CSV")"

# A consistent copy: the index is WAL-mode and the mail sync may be writing.
# Under systemd PrivateTmp this lives in a private /tmp removed at stop.
export SNAP
SNAP=$(mktemp "${TMPDIR:-/tmp}/ledger-snap.XXXXXX.sqlite")
NEW=$STATE/ledger.sqlite.new
# The backup inherits WAL mode, so readers leave -wal/-shm beside the copy;
# remove those too, or plaintext mail outlives the run outside PrivateTmp.
trap 'rm -f "$SNAP" "$SNAP-wal" "$SNAP-shm" "$SNAP-journal" "$NEW"' EXIT
python3 - "$MAIL_DB" "$SNAP" <<'PY'
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2]); src.backup(dst)
dst.execute("PRAGMA journal_mode=DELETE"); dst.close(); src.close()
PY
log "snapshot ok"

python3 "$ENRICH/senders.py" "$SNAP" "$SENDERS_CSV" >/dev/null
log "sender classification ok"

python3 "$HERE/run_extract.py" "$STATE/extract" --snapshot "$SNAP"
log "extract ok"

rm -f "$NEW"
python3 "$HERE/build_ledger.py" "$STATE/extract" "$NEW" >/dev/null
python3 - "$NEW" "$LEDGER_DB" "$MIN_KEEP" <<'PY'
import os, sqlite3, sys
new, cur, keep = sys.argv[1], sys.argv[2], float(sys.argv[3])
count = lambda p: sqlite3.connect(f"file:{p}?mode=ro", uri=True).execute(
    "select count(*) from ledger").fetchone()[0]
n = count(new)
try:
    old = count(cur) if os.path.exists(cur) else 0
except sqlite3.Error:      # a torn or empty current ledger must not block repair
    old = 0
if n == 0:
    sys.exit("FATAL: built ledger is empty; keeping the current one")
if old and n < old * keep:
    sys.exit(f"FATAL: ledger would shrink {old} -> {n} events; keeping the current one")
print(f"events {old} -> {n}")
PY
chmod 600 "$NEW"
mv "$NEW" "$LEDGER_DB"     # atomic: mcp.py opens read-only per call
log "installed $LEDGER_DB"
