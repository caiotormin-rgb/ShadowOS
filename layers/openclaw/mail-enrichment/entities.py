"""Counterparty resolution: sender addresses -> the entity Caio thinks in.

He searches by merchant or by person ("what did I pay Acme Lawn", "when did
Dana last write"), so the entity is the primary key of the ledger and date is
a filter. Address-level identity is useless for that: Amazon sends from 21
addresses, Apple from 38.

Naive root-domain collapse gets most of it (1,953 -> ~935) but fails three
ways, all measured on the real corpus:

  1. `com.br` swallowed 112 addresses and 2,544 messages into one fake
     merchant, destroying every Brazilian entity. Needs public-suffix rules.
  2. `gmail.com` collapsed 24 individuals into a "Gmail" merchant. People must
     resolve as people.
  3. Sending platforms (Constant Contact, Shopify Email) are not merchants;
     the real sender is carried in the display name or the local part.

Stdlib only.
"""
from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

ADDR = re.compile(r"<([^>]+)>")

# Multi-label public suffixes seen in this corpus plus the common remainder.
# Not the full PSL: that is a 15k-line download, and being wrong about
# `co.zw` costs nothing here while being wrong about `com.br` costs 2,544
# messages.
PUBLIC_SUFFIXES = {
    "com.br", "net.br", "org.br", "gov.br", "edu.br", "art.br", "adv.br",
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk",
    "com.au", "net.au", "org.au", "co.jp", "or.jp", "ne.jp",
    "com.mx", "com.ar", "com.co", "com.pt", "co.nz", "co.za", "com.tr",
    "co.in", "com.sg", "com.hk", "com.cn", "com.es",
}

# Mail is sent *through* these; the entity is whoever hired them.
SENDING_PLATFORMS = {
    "ccsend.com", "shopifyemail.com", "sendgrid.net", "mailchimpapp.net",
    "mcsv.net", "rsgsv.net", "beehiiv.com", "mailgun.org", "sparkpostmail.com",
    "amazonses.com", "mandrillapp.com", "cmail19.com", "createsend.com",
    "hubspotemail.net", "klaviyomail.com", "sendinblue.com", "eumail.docusign.net",
    # Invoicing platforms: QuickBooks mails on behalf of the small businesses
    # that bill Caio, so "Acme Lawn" arrives from intuit.com and is
    # only nameable from the display name.
    "notification.intuit.com", "intuit.com", "squareup.com", "invoice.stripe.com",
    "waveapps.com", "freshbooks.com", "bill.com", "zapsign.com.br",
}

# Local parts that mean "a mailing apparatus", not a human, even when the
# display name is a person's. Caught a congressional office's bulk outreach
# list resolving as an individual.
BULK_LOCAL = re.compile(
    r"(no-?reply|donotreply|outreach|newsletter|news|info|support|service|"
    r"notification|alerts?|updates?|marketing|promo|team|hello|contact|"
    r"orders?|billing|account|admin|mailer|bounce)", re.I)

# The mailbox owner's own addresses, so self-sent mail resolves to `self:`.
# Comma-separated in LEDGER_SELF_ADDRESSES; unset means nothing is self.
SELF_ADDRESSES = {a.strip().lower() for a in
                  os.environ.get("LEDGER_SELF_ADDRESSES", "").split(",") if a.strip()}

# Words that make a display name a company, however personal it looks.
ORG_MARKERS = re.compile(
    r"\b(ltda|s\.?a\.?|llc|inc|corp|co|company|consultoria|imoveis|im[oó]veis|"
    r"servi[cç]os|solutions|group|team|store|shop|clinic|center|centre|"
    r"associates|partners|bank|banco|seguros|escrit[oó]rio)\b|[|/@#0-9]", re.I)

NAME_PARTICLES = {"de", "da", "do", "dos", "das", "van", "von", "der", "del",
                  "di", "la", "le", "bin", "el"}


def looks_like_person_name(display: str) -> bool:
    """A human name: 2-4 tokens, each capitalised or a particle, no company
    markers. 'a family member' yes; 'Acme Sleep' and 'Example Consultoria |
    Condominio/Boletos' no.

    Single-token names are rejected on corporate domains: a brand is far more
    likely than a mononym, and the freemail path does not need this test.
    """
    d = (display or "").strip()
    if not d or ORG_MARKERS.search(d):
        return False
    tokens = d.split()
    if not 2 <= len(tokens) <= 4:
        return False
    return all(t[:1].isupper() or t.lower() in NAME_PARTICLES for t in tokens)

FREEMAIL = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "yahoo.com", "yahoo.com.br", "icloud.com", "me.com", "protonmail.com",
    "proton.me", "aol.com", "bol.com.br", "uol.com.br", "terra.com.br",
}

# Big vendors whose commerce and marketing streams should stay separate: "what
# did I pay Apple" must not return newsletters.
STREAM_SPLIT = {
    "apple.com": {"orders": ("orders.apple.com", "store.apple.com"),
                  "marketing": ("insideapple.apple.com",)},
    "amazon.com": {"marketing": ("store-news",)},
    # Google is not one counterparty: Flights alerts, Maps notifications,
    # account security, and the Store are unrelated streams that happened to
    # share a registrable domain (41 addresses collapsed into "Google Flights").
    "google.com": {"security": ("accounts.google.com",),
                   "travel": ("noreply-travel",),
                   "store": ("googlestore",),
                   "maps": ("google-maps",)},
}


def address_of(raw: str | None) -> str | None:
    if not raw:
        return None
    m = ADDR.search(raw)
    a = (m.group(1) if m else raw).strip().strip('"').lower()
    return a if "@" in a and "." in a.split("@")[-1] else None


def display_of(raw: str | None) -> str:
    if not raw:
        return ""
    return ADDR.sub("", raw).strip().strip('"').strip()


def registrable_domain(domain: str) -> str:
    """eTLD+1, public-suffix aware. `nubank.com.br` stays nubank.com.br."""
    parts = domain.lower().strip(">").split(".")
    if len(parts) < 2:
        return domain
    if ".".join(parts[-2:]) in PUBLIC_SUFFIXES and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]


@dataclass
class Entity:
    key: str
    name: str
    kind: str                     # merchant | person | platform-sender | unknown
    addresses: set[str] = field(default_factory=set)
    messages: int = 0
    display_names: Counter = field(default_factory=Counter)
    stream: str | None = None     # orders | marketing, when a vendor is split

    def best_name(self) -> str:
        """For a multi-person organisation, prefer the organisation's name.

        Green Power Energy mails from 11 addresses -- three named staff and a
        project coordinator. Taking the most common display name made the
        entity "Pat Example", which is a contact, not the counterparty.
        """
        if not self.display_names:
            return self.name
        ranked = self.display_names.most_common()
        if self.kind == "merchant" and len(self.addresses) > 3:
            for name, _ in ranked:
                if not looks_like_person_name(name):
                    return name
            return self.key.split(":", 1)[-1].split("#")[0]
        return ranked[0][0]


def resolve(raw_from: str, sender_class: str | None = None,
            domain_address_count: int = 1) -> tuple[str, str, str, str | None]:
    """-> (entity_key, entity_name, kind, stream). Pure; no I/O.

    `domain_address_count` is how many distinct addresses the corpus has seen
    on this registrable domain; >3 means an organisation.
    """
    addr = address_of(raw_from)
    display = display_of(raw_from)
    if not addr:
        return ("unknown", display or "unknown", "unknown", None)
    local, _, domain = addr.partition("@")
    reg = registrable_domain(domain)

    # 1. A person. Two guards learned from the first run against real data:
    #    - phase-0's "human" class means "someone Caio corresponded with",
    #      which is not the same as "an individual" -- it labelled a retailer and
    #      a condo administration company as human because he replied to them.
    #      So a corporate domain only yields a person when the address itself
    #      is not apparatus-shaped.
    #    - a domain sending from many addresses is an organisation, whatever
    #      any single message looks like.
    if addr in SELF_ADDRESSES:
        return (f"self:{addr}", display or local, "self", None)
    is_freemail = reg in FREEMAIL
    apparatus = bool(BULK_LOCAL.search(local))
    if is_freemail or (sender_class == "human" and not apparatus
                       and domain_address_count <= 3
                       and looks_like_person_name(display)):
        name = display or local
        return (f"person:{addr}", name, "person", None)

    # 2. A sending platform: the real sender rides in the display name, and
    #    sometimes in the local part (christine-xfactormedia@shared1.ccsend.com).
    if reg in SENDING_PLATFORMS:
        real = display or local.split("-")[0]
        if not real:
            return (f"platform:{reg}", reg, "platform-sender", None)
        return (f"merchant:{_slug(real)}", real, "merchant", None)

    # 3. A merchant, possibly stream-split.
    stream = None
    for base, streams in STREAM_SPLIT.items():
        if reg == base:
            for label, markers in streams.items():
                if any(m in domain or m in local for m in markers):
                    stream = label
                    break
    key = f"merchant:{reg}" + (f"#{stream}" if stream else "")
    return (key, display or reg, "merchant", stream)


def build(rows, sender_classes: dict[str, str] | None = None) -> dict[str, Entity]:
    """rows: iterable of (from_addr, n_messages)."""
    sender_classes = sender_classes or {}
    rows = list(rows)
    # Pre-pass: how many addresses does each registrable domain use?
    per_domain: dict[str, set[str]] = defaultdict(set)
    for raw, _ in rows:
        a = address_of(raw)
        if a:
            per_domain[registrable_domain(a.partition("@")[2])].add(a)
    out: dict[str, Entity] = {}
    for raw, n in rows:
        addr = address_of(raw)
        cls = sender_classes.get(addr or "", None)
        dcount = len(per_domain.get(
            registrable_domain(addr.partition("@")[2]), ())) if addr else 1
        key, name, kind, stream = resolve(raw, cls, dcount)
        e = out.get(key)
        if e is None:
            e = out[key] = Entity(key, name, kind, stream=stream)
        if addr:
            e.addresses.add(addr)
        e.messages += n
        d = display_of(raw)
        if d:
            e.display_names[d] += n
    return out
