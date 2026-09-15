"""Sharing state, minimized on purpose.

The question this layer exists to answer is "which of my files are exposed, and
how far". That is fully answered by a state plus counts. Storing the full
permission list would instead build a durable local social graph of third
parties -- every colleague, family member and contractor the account has ever
shared a file with -- none of whom consented to being indexed on torm and none
of whom appear in the problem statement. On a disk that is currently not
encrypted, it would also make this database the most valuable file on the
machine.

So the minimization happens twice, and the first one is the one that counts:

* `drivectx.drive.FILE_FIELDS` never asks Drive for a grantee's address, so the
  addresses do not enter the process at all.
* `summarize()` below reduces whatever did arrive to a state and four numbers.

Re-argue this before changing it.
"""
from __future__ import annotations

from dataclasses import dataclass

# Broadest last. 'organizer' and 'fileOrganizer' exist only on shared drives,
# which this layer excludes, so an unrecognised role is ignored rather than
# guessed at.
ROLE_ORDER = ("reader", "commenter", "writer", "owner")

STATES = ("private", "shared_with_named", "domain", "anyone_with_link", "unknown")


@dataclass(frozen=True)
class Sharing:
    """What the index is allowed to remember about who can see a file."""

    state: str = "unknown"
    named_user_count: int = 0
    group_count: int = 0
    link_discoverable: bool = False
    max_role: str | None = None

    @classmethod
    def unknown(cls) -> "Sharing":
        return cls()


def summarize(raw: dict) -> Sharing:
    """Derive the sharing summary from one Drive file payload.

    `unknown` is a real and expected state, not an error. Under
    drive.metadata.readonly the permissions collection is only visible for files
    whose permissions this account may see; for files shared *to* the account by
    someone else it is routinely absent, and the index must say `unknown` rather
    than imply `private`. An empty list means the same thing as an absent one --
    a file always has at least its owner's permission, so seeing zero of them is
    a statement about visibility, not about sharing.
    """
    perms = raw.get("permissions")
    if not isinstance(perms, list) or not perms:
        return Sharing.unknown()

    named_users = 0
    groups = 0
    anyone = False
    discoverable = False
    domain = False
    best = -1

    for perm in perms:
        if not isinstance(perm, dict):
            continue
        if perm.get("deleted"):
            continue  # a grant to an account that no longer exists
        ptype = perm.get("type")
        role = perm.get("role")
        if role in ROLE_ORDER:
            best = max(best, ROLE_ORDER.index(role))
        if ptype == "anyone":
            anyone = True
            # allowFileDiscovery distinguishes "anyone who searches" from
            # "anyone who already has the link", which is a real difference in
            # exposure and the only reason this flag is retained.
            discoverable = discoverable or bool(perm.get("allowFileDiscovery"))
        elif ptype == "domain":
            domain = True
        elif ptype == "group":
            groups += 1
        elif ptype == "user" and role != "owner":
            named_users += 1

    if anyone:
        state = "anyone_with_link"
    elif domain:
        state = "domain"
    elif named_users or groups:
        state = "shared_with_named"
    else:
        state = "private"

    return Sharing(
        state=state,
        named_user_count=named_users,
        group_count=groups,
        link_discoverable=discoverable,
        max_role=(ROLE_ORDER[best] if best >= 0 else None),
    )
