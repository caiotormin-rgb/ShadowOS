"""Where things live. Every path sits inside the encrypted mount except the
mount point itself, which is created before gocryptfs is mounted over it."""
import os
from pathlib import Path

STORE = Path(os.environ.get("LIFE_INDEX_STORE", Path.home() / "life-index"))
CIPHER = STORE / "cipher"          # ciphertext, safe at rest
PLAIN = STORE / "plain"            # data dir: a mount point when encrypted
PLAINTEXT_MARKER = STORE / "PLAINTEXT-UNENCRYPTED"
CONSUME = PLAIN / "consume"        # drop files here
BLOBS = PLAIN / "blobs"            # content-addressed store
DB = PLAIN / "catalog.sqlite"
REJECTED = PLAIN / "rejected"      # things the pipeline could not handle


def mounted() -> bool:
    """True when the plaintext view is actually a gocryptfs mount.

    Checked by looking for the mountpoint in /proc/self/mountinfo rather than
    by testing whether the directory has files: an unmounted PLAIN dir is a
    perfectly ordinary empty directory, and writing into it would silently
    put plaintext documents on the unencrypted disk.
    """
    try:
        target = str(PLAIN.resolve())
    except OSError:
        return False
    try:
        with open("/proc/self/mountinfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) > 4 and parts[4] == target:
                    return True
    except OSError:
        pass
    return False


def plaintext_acknowledged() -> bool:
    """True when the operator has explicitly chosen an unencrypted store.

    A marker file rather than a default: the failure this guards against is
    believing the store is encrypted when it is not, and a silent default is
    exactly how that belief forms.
    """
    return PLAINTEXT_MARKER.exists()


def mode() -> str:
    if mounted():
        return "encrypted"
    if plaintext_acknowledged():
        return "plaintext"
    return "unavailable"


def require_store() -> str:
    """Allow writes only into a mounted store, or an explicitly-acknowledged
    plaintext one. Returns the mode."""
    m = mode()
    if m == "unavailable":
        raise SystemExit(
            f"No life-index store is available at {STORE}.\n\n"
            "  Encrypted (recommended):  bin/li-init && bin/li-mount\n"
            "  Plaintext, for now:       bin/li-plaintext\n\n"
            "Refusing to guess: writing documents to an unencrypted disk is a\n"
            "decision, not a fallback.")
    return m


# Kept for callers that specifically demand encryption.
def require_mount() -> None:
    if not mounted():
        raise SystemExit(
            f"life-index store is not mounted at {PLAIN}.\n"
            f"Run:  bin/li-mount")
