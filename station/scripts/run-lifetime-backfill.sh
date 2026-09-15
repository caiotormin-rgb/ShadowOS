#!/usr/bin/env bash
# Lifetime Gmail metadata backfill. Run with sudo from an interactive terminal.
#   sudo bash station/scripts/run-lifetime-backfill.sh
set -euo pipefail
OC=/home/openclaw/mail-context
SRC=/home/caio/workspace/layers/openclaw/mail-context
UNIT=/home/openclaw/.config/systemd/user/mail-context-sync.service
RUN="sudo -u openclaw env HOME=/home/openclaw PYTHONPATH=$OC python3"

[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

say "1/5  stop the timer first"
# A scheduled run mid-backfill would prune with the old 730-day window and
# delete everything the backfill just added.
sudo -u openclaw XDG_RUNTIME_DIR=/run/user/$(id -u openclaw) \
  systemctl --user stop mail-context-sync.timer 2>/dev/null || true
echo "  timer stopped"

say "2/5  redeploy the code"
cp -r "$SRC/mailctx" "$SRC/schema.sql" "$OC/"
chown -R openclaw:openclaw "$OC"
$RUN -c 'import mailctx.syncrun, mailctx.targetrun; print("  imports OK")'

say "3/5  fix the prune window BEFORE loading anything"
if grep -q 'syncrun --quiet$' "$UNIT" 2>/dev/null; then
  sed -i 's|-m mailctx.syncrun --quiet$|-m mailctx.syncrun --quiet --window 0|' "$UNIT"
  echo "  unit now uses --window 0"
elif grep -q -- '--window 0' "$UNIT" 2>/dev/null; then
  echo "  already --window 0"
else
  echo "  WARNING: could not find the ExecStart line to patch — check $UNIT" >&2
fi
sudo -u openclaw XDG_RUNTIME_DIR=/run/user/$(id -u openclaw) systemctl --user daemon-reload

say "4/5  sent mail first (~3 min) — the correspondence history"
# --full is REQUIRED. sync() dispatches on whether an initial load ever
# completed, and yours did — so without it the run takes the incremental
# history path and the window and query are ignored entirely. Already-indexed
# ids are skipped, so a --full run over an overlapping range is cheap.
$RUN -m mailctx.syncrun --window 0 --query 'in:sent' --full
$RUN -m mailctx.syncrun --status

say "5/5  the rest of lifetime, junk excluded (~1 h, backgrounded)"
nohup sudo -u openclaw env HOME=/home/openclaw PYTHONPATH=$OC \
  python3 -m mailctx.syncrun --window 0 \
    --query '-category:promotions -category:social' --full \
  > /home/openclaw/lifetime-sync.log 2>&1 &
echo "  started. watch it with:"
echo "    sudo tail -f /home/openclaw/lifetime-sync.log"
echo
echo "  when it finishes, re-enable the timer:"
echo "    sudo -u openclaw XDG_RUNTIME_DIR=/run/user/\$(id -u openclaw) systemctl --user start mail-context-sync.timer"
