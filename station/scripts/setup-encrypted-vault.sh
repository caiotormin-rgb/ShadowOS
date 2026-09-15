#!/usr/bin/env bash
# Create a TPM-unlocked encrypted vault on torm, and migrate the existing
# plaintext life-index into it.
#
# Set-and-forget: the key is sealed to this machine's TPM, so the volume
# unlocks itself at boot with nothing typed. A recovery passphrase is also
# enrolled -- put it in Proton Pass, it is the only way back if the TPM is
# cleared or the firmware is reset.
#
# THREAT MODEL, stated plainly: this protects a stolen or discarded DISK.
# It does not protect a stolen machine that an attacker can power on, because
# the TPM will hand the key to this machine. That is the tradeoff "set and
# forget" buys; a boot passphrase would close it and would not be set-and-forget.
set -euo pipefail

VAULT_IMG=/var/lib/torm-vault.luks
VAULT_NAME=tormvault
MOUNT=/srv/vault
SIZE_GB=${SIZE_GB:-40}
OWNER=caio
OLD_STORE=/home/caio/life-index

[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

say "1/7  dependencies"
if ! command -v cryptsetup >/dev/null; then
  apt-get update -qq && apt-get install -y -qq cryptsetup-bin
fi
command -v cryptsetup >/dev/null || { echo "cryptsetup install failed" >&2; exit 1; }
[ -c /dev/tpmrm0 ] || { echo "no TPM resource manager at /dev/tpmrm0" >&2; exit 1; }

say "2/7  create the container ($SIZE_GB GB, sparse)"
if [ -e "$VAULT_IMG" ]; then
  echo "  $VAULT_IMG already exists — leaving it alone."
else
  truncate -s "${SIZE_GB}G" "$VAULT_IMG"
  chmod 600 "$VAULT_IMG"
  echo "  created $VAULT_IMG"
fi

say "3/7  LUKS2 format  ** you will be asked for a RECOVERY PASSPHRASE **"
if cryptsetup isLuks "$VAULT_IMG" 2>/dev/null; then
  echo "  already LUKS — skipping format."
else
  cat <<'MSG'
  Choose a recovery passphrase now and SAVE IT IN PROTON PASS.

  You will never type it during normal use — the TPM unlocks the volume at
  boot. You need it only if the TPM is cleared, the motherboard is replaced,
  or firmware settings are reset. Without it, that data is gone.

MSG
  cryptsetup luksFormat --type luks2 "$VAULT_IMG"
fi

say "4/7  seal a key to the TPM (this is what makes it set-and-forget)"
if systemd-cryptenroll "$VAULT_IMG" 2>/dev/null | grep -q tpm2; then
  echo "  TPM key already enrolled."
else
  # PCR 7 = secure-boot policy. Stable across kernel updates, unlike PCR 0/4,
  # so this survives ordinary patching without needing re-enrolment.
  systemd-cryptenroll --tpm2-device=auto --tpm2-pcrs=7 "$VAULT_IMG"
fi

say "5/7  filesystem + auto-unlock at boot"
cryptsetup open "$VAULT_IMG" "$VAULT_NAME"
if ! blkid "/dev/mapper/$VAULT_NAME" >/dev/null 2>&1; then
  mkfs.ext4 -q -L tormvault "/dev/mapper/$VAULT_NAME"
  echo "  filesystem created"
fi
mkdir -p "$MOUNT"
mount "/dev/mapper/$VAULT_NAME" "$MOUNT"
chown "$OWNER:$OWNER" "$MOUNT"; chmod 700 "$MOUNT"

grep -q "^$VAULT_NAME " /etc/crypttab 2>/dev/null || \
  echo "$VAULT_NAME $VAULT_IMG none tpm2-device=auto,luks" >> /etc/crypttab
grep -q " $MOUNT " /etc/fstab 2>/dev/null || \
  echo "/dev/mapper/$VAULT_NAME $MOUNT ext4 defaults,nofail 0 2" >> /etc/fstab
systemctl daemon-reload

say "6/7  migrate the plaintext life-index"
if [ -d "$OLD_STORE/plain" ] && [ ! -e "$MOUNT/life-index" ]; then
  sudo -u "$OWNER" mkdir -p "$MOUNT/life-index"
  cp -a "$OLD_STORE/plain/." "$MOUNT/life-index/"
  chown -R "$OWNER:$OWNER" "$MOUNT/life-index"
  # Only destroy the plaintext once the encrypted copy is verifiably present.
  if [ -d "$MOUNT/life-index/blobs" ] || [ -f "$MOUNT/life-index/catalog.sqlite" ]; then
    find "$OLD_STORE/plain" -type f -exec shred -u {} + 2>/dev/null || true
    rm -rf "$OLD_STORE/plain" "$OLD_STORE/PLAINTEXT-UNENCRYPTED"
    sudo -u "$OWNER" ln -sfn "$MOUNT/life-index" "$OLD_STORE/plain"
    echo "  migrated; plaintext shredded; $OLD_STORE/plain now points into the vault"
  else
    echo "  MIGRATION INCOMPLETE — plaintext left in place at $OLD_STORE" >&2
  fi
else
  echo "  nothing to migrate (or already migrated)"
fi

say "7/7  somewhere for fetched message bodies"
sudo -u "$OWNER" mkdir -p "$MOUNT/bodies" "$MOUNT/ledger"

cat <<MSG

DONE.

  vault      $MOUNT           (unlocks itself at boot, TPM-sealed)
  documents  $MOUNT/life-index
  bodies     $MOUNT/bodies    <- point the body fetch here
  ledger     $MOUNT/ledger

Verify it survives a reboot before trusting it:

  sudo reboot
  # then, after it comes back:
  mountpoint $MOUNT && ls $MOUNT

If that fails, unlock manually with the recovery passphrase:
  sudo cryptsetup open $VAULT_IMG $VAULT_NAME && sudo mount /dev/mapper/$VAULT_NAME $MOUNT
MSG
