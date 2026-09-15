"""Transaction extraction as a layered cascade. Stdlib only, Python 3.12.

The rule that shapes everything here: **each layer must either TERMINATE or
EXTRACT, never merely annotate.** A layer that only adds a label leaves the
same volume for the next layer, so the funnel never narrows and the expensive
lane at the bottom pays for everything.

    L0  sender class            marketing -> TERMINATE                (free)
    L1  per-sender template     match     -> EXTRACT or TERMINATE     (free, cached)
    L2  generic deterministic   cue       -> EXTRACT low-confidence   (free)
    L3  escalation queue        residue   -> local model lane         (costly)

The template key is the **sender address**, not the subject. Measured on the
snapshot: sender+3-subject-words yields 10,767 families, 82% of them
singletons; sender alone yields 1,163, and 41 senders cover half the
non-marketing corpus. So a template learned once for `noreply@mytix.
njtransit.com` serves all 119 of its messages forever -- decide per sender,
not per message.

Templates are *data* (see `templates.py`), persisted to `extraction_templates`
and executed by the interpreter below. They are inspectable and editable
without touching this file.
"""
from __future__ import annotations

import html
import json
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

# ---------------------------------------------------------------- normalising

# Gmail snippets are padded with zero-width and invisible characters used by
# bulk senders to defeat clipping ("Shipped: \"X\"͏ ‌ ͏ ‌ ͏ ‌ ..."). Left in,
# they break every regex that spans a boundary.
_INVISIBLE = "".join(chr(c) for c in (
    0x00AD, 0x034F, 0x061C, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x180E,
    0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x2060, 0x2061, 0x2062,
    0x2063, 0x2064, 0x206A, 0x206B, 0x206C, 0x206D, 0x206E, 0x206F,
    0xFEFF, 0x2066, 0x2067, 0x2068, 0x2069,
))
_INVIS_RE = re.compile("[" + re.escape(_INVISIBLE) + "]")


def clean(text: str | None) -> str:
    if not text:
        return ""
    t = html.unescape(text)
    t = _INVIS_RE.sub("", t)
    t = unicodedata.normalize("NFKC", t)
    t = t.replace("’", "'").replace("‘", "'")
    t = t.replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", t).strip()


# ---------------------------------------------------------------- amounts

_AMT_BODY = re.compile(r"\d[\d.,\s]*\d|\d")


def parse_amount(raw: str) -> tuple[float | None, str | None]:
    """-> (value, currency). Handles US 1,234.56 and BR 1.234,56.

    Venmo writes the amount twice, the second time spaced out for its big
    display type: "You paid a family member $ 160 . 00". Spaces are stripped
    before the separator logic runs, so both forms land on 160.0.
    """
    if not raw:
        return (None, None)
    s = clean(raw)
    cur = "BRL" if "R$" in s else ("USD" if "$" in s or "USD" in s.upper() else None)
    m = _AMT_BODY.search(s)
    if not m:
        return (None, cur)
    body = m.group(0).replace(" ", "")
    if "," in body and "." in body:
        body = (body.replace(".", "").replace(",", ".")
                if body.rfind(",") > body.rfind(".") else body.replace(",", ""))
    elif "," in body:
        # A lone comma is a decimal comma only when it has exactly two digits
        # behind it; "1,234" is US thousands, "1,23" is BR cents.
        body = body.replace(",", ".") if re.search(r",\d{2}$", body) else body.replace(",", "")
    try:
        return (round(float(body), 2), cur)
    except ValueError:
        return (None, cur)


# ---------------------------------------------------------------- dates

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MONTHS.update({m: i for i, m in enumerate(
    ["janeiro", "fevereiro", "marco", "abril", "maio", "junho",
     "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"], 1)})

_D_MDY = re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b")
_D_MD = re.compile(r"\b(\d{1,2})/(\d{1,2})\b")
_D_ISO = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_D_NAME = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(20\d{2})?\b")
_D_DNAME = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?\s*(20\d{2})?\b")


def parse_date(raw: str | None, ref_ts: int | None = None,
               dayfirst: bool = False) -> str | None:
    """-> 'YYYY-MM-DD' or None.

    `ref_ts` supplies the year when the source omits it (A booking site writes
    "Appointment with Dr. Example on 5/22"). Picking the message's own year is
    right far more often than any other guess, and the message is at most days
    from the event in every sender that does this.
    """
    if not raw:
        return None
    s = clean(raw)
    ref_year = (datetime.fromtimestamp(ref_ts, timezone.utc).year
                if ref_ts else datetime.now(timezone.utc).year)

    if (m := _D_ISO.search(s)):
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
        return _mk(y, mo, d)
    if (m := _D_MDY.search(s)):
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        mo, d = (b, a) if dayfirst else (a, b)
        if mo > 12:              # 17/07/2025 is unambiguous whatever the flag
            mo, d = d, mo
        return _mk(y, mo, d)
    if (m := _D_NAME.search(s)) and m[1][:3].lower() in _MONTHS:
        mo = _MONTHS[m[1][:3].lower()] if len(m[1]) <= 4 else _MONTHS.get(
            m[1].lower(), _MONTHS[m[1][:3].lower()])
        return _mk(int(m[3]) if m[3] else ref_year, mo, int(m[2]))
    if (m := _D_DNAME.search(s)) and m[2][:3].lower() in _MONTHS:
        return _mk(int(m[3]) if m[3] else ref_year,
                   _MONTHS[m[2][:3].lower()], int(m[1]))
    if (m := _D_MD.search(s)):
        a, b = int(m[1]), int(m[2])
        mo, d = (b, a) if dayfirst else (a, b)
        return _mk(ref_year, mo, d)
    return None


def _mk(y: int, mo: int, d: int) -> str | None:
    try:
        return datetime(y, mo, d).strftime("%Y-%m-%d")
    except ValueError:
        return None


def ts_date(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- model

KINDS = {"purchase", "subscription", "booking", "appointment", "payment", "shipment"}


@dataclass
class Message:
    message_id: str
    from_addr: str
    subject: str
    snippet: str
    internal_ts: int
    thread_id: str = ""

    @property
    def text(self) -> str:
        return clean(self.subject) + " || " + clean(self.snippet)


@dataclass
class Row:
    entity_key: str
    entity_name: str
    counterparty: str
    date: str
    kind: str
    amount: float | None
    currency: str | None
    ref_number: str | None
    service_dates: str | None
    message_id: str
    confidence: float
    lane: str
    rule: str
    description: str | None = None
    link_key: str | None = None
    link_basis: str | None = None
    event_id: int | None = None


@dataclass
class Outcome:
    """A cascade layer returns exactly one of these. `action` is never
    'annotate' -- that is the whole point of the design."""
    action: str                  # extract | terminate | escalate
    layer: str
    reason: str = ""
    rows: list[Row] = field(default_factory=list)


# ---------------------------------------------------------------- L1 interpreter

_FIELD_ORDER = ("amount", "ref_number", "date", "counterparty",
                "service_dates", "description", "currency")


class Template:
    """One sender's learned rules, in priority order.

    A sender is a mix of shapes -- Apple sends receipts, renewal notices and
    Find My alerts from one address -- so a template is a small ordered rule
    list, still learned once per sender. `default` decides everything no rule
    claimed: 'terminate' for senders whose residue is known noise.
    """

    def __init__(self, sender: str, rules: list[dict], default: str = "terminate",
                 entity_hint: str | None = None, notes: str = ""):
        self.sender = sender
        self.raw_rules = rules
        self.default = default
        self.entity_hint = entity_hint
        self.notes = notes
        self.rules = [self._compile(r) for r in rules]

    @staticmethod
    def _compile(r: dict) -> dict:
        c = dict(r)
        c["_match"] = re.compile(r["match"], re.I | re.S)
        c["_fields"] = {k: (re.compile(v["rx"], re.I | re.S), v.get("on", "snippet"),
                            v.get("group", 1), v.get("const"))
                        for k, v in r.get("fields", {}).items()}
        return c

    def apply(self, msg: Message, entity_key: str, entity_name: str) -> Outcome:
        subject, snippet = clean(msg.subject), clean(msg.snippet)
        both = subject + " || " + snippet
        src = {"subject": subject, "snippet": snippet, "both": both}
        for r in self.rules:
            target = src[r.get("match_on", "subject")]
            m = r["_match"].search(target)
            if not m:
                continue
            if r["action"] == "terminate":
                return Outcome("terminate", "L1", f"{r['name']}: {r.get('why','not an event')}")
            vals: dict[str, str | None] = {}
            for name, (rx, on, grp, const) in r["_fields"].items():
                if const is not None:
                    vals[name] = const
                    continue
                fm = rx.search(src[on])
                if not fm:
                    vals[name] = None
                elif fm.re.groups:
                    # First group that actually matched. Taking group(0) here
                    # leaked the label into the value ("Order ID 6161065503...")
                    # for every alternation pattern.
                    vals[name] = next((g for g in fm.groups() if g), None)
                else:
                    vals[name] = fm.group(0)
            # Groups from the rule's own match are addressable as $1..$9.
            for i in range(1, (m.lastindex or 0) + 1):
                vals.setdefault(f"${i}", m.group(i))
            for k in _FIELD_ORDER:
                v = r.get(k)
                if isinstance(v, str) and v.startswith("$") and v[1:].isdigit():
                    vals[k] = m.group(int(v[1:]))
                elif isinstance(v, str) and k not in vals:
                    vals[k] = v
            amount, currency = parse_amount(vals.get("amount") or "")
            if vals.get("currency"):
                currency = vals["currency"]
            date_src = vals.get("date")
            if not date_src and r.get("date_from_service"):
                date_src = vals.get("service_dates")
            date = parse_date(date_src, msg.internal_ts,
                              dayfirst=bool(r.get("dayfirst"))) or ts_date(msg.internal_ts)
            cp = clean(vals.get("counterparty") or "") or entity_name
            row = Row(
                entity_key=entity_key, entity_name=entity_name,
                counterparty=cp.rstrip(" .,-"), date=date, kind=r["kind"],
                amount=amount, currency=currency,
                ref_number=clean(vals.get("ref_number") or "") or None,
                service_dates=clean(vals.get("service_dates") or "") or None,
                message_id=msg.message_id, confidence=float(r.get("confidence", 0.7)),
                lane="L1", rule=f"{self.sender}:{r['name']}",
                description=clean(vals.get("description") or "") or None,
            )
            return Outcome("extract", "L1", r["name"], [row])
        return Outcome("terminate" if self.default == "terminate" else "escalate",
                       "L1", f"no rule matched (default={self.default})")


# ---------------------------------------------------------------- L2 generic

_GENERIC_KIND = [
    ("payment", re.compile(r"\byou paid\b|\bpayment confirmation\b|thanks for your payment|"
                           r"payment (?:received|processed|complete)|we've received your \$|"
                           r"you authorized|pagamento (?:realizado|efetuado|conclu)", re.I)),
    ("shipment", re.compile(r"\bshipped\b|out for delivery|has been delivered|\bdelivered:|"
                            r"on its way|dispatch(?:ed)? confirmation", re.I)),
    ("booking", re.compile(r"\breservation\b|\bitinerary\b|booking confirmation|"
                           r"\breserva\b|confirmation number", re.I)),
    ("appointment", re.compile(r"\bappointment\b|\bconsulta\b|your session with", re.I)),
    ("subscription", re.compile(r"\brenews?\b|auto-?renew|subscription (?:confirm|receipt)|"
                                r"your subscription", re.I)),
    ("purchase", re.compile(r"order confirmation|thanks? (?:you )?for (?:your|ordering)|"
                            r"\bordered:|your order|receipt|invoice|ticket purchase|"
                            r"\bpedido\b", re.I)),
]
_G_AMOUNT = re.compile(r"(?:R\$|US\$|\$)\s?\d[\d.,\s]*\d")
_G_REF = re.compile(
    r"(?:order|pedido|invoice|fatura|confirmation|transaction|trx seq|reference|ref)"
    r"\s*(?:id|no\.?|number|#|seq id|:)?\s*[:#]?\s*([A-Z0-9][A-Z0-9\-.]{4,24})", re.I)


_NOT_EVENT = re.compile(
    r"how was your|how did we do|rate your|review your|please review|your feedback|"
    r"take a (?:quick )?survey|write a review|we'd love your feedback|"
    r"payment is due on|statement is available|verification code|one time code", re.I)


def generic(msg: Message, entity_key: str, entity_name: str) -> Outcome:
    t = msg.text
    if _NOT_EVENT.search(t):
        return Outcome("terminate", "L2", "solicitation or due-date notice, not an event")
    kind = next((k for k, rx in _GENERIC_KIND if rx.search(t)), None)
    if not kind:
        return Outcome("terminate", "L2", "no transactional cue")
    am = _G_AMOUNT.search(t)
    amount, currency = parse_amount(am.group(0)) if am else (None, None)
    ref = None
    if (rm := _G_REF.search(t)):
        cand = rm.group(1).strip(".")
        if not cand.isalpha():
            ref = cand
    if amount is None and ref is None:
        # A kind and a date alone is not a ledger row -- it is a guess. Send it
        # to the model lane rather than manufacture a row nobody can verify.
        return Outcome("escalate", "L2", f"cue={kind} but no amount and no ref")
    row = Row(entity_key=entity_key, entity_name=entity_name, counterparty=entity_name,
              date=ts_date(msg.internal_ts), kind=kind, amount=amount, currency=currency,
              ref_number=ref, service_dates=None, message_id=msg.message_id,
              confidence=0.45 if amount is not None else 0.35, lane="L2", rule="generic")
    return Outcome("extract", "L2", f"generic/{kind}", [row])


# ---------------------------------------------------------------- cascade

class Cascade:
    def __init__(self, templates: dict[str, Template],
                 sender_classes: dict[str, str],
                 resolve):
        self.templates = templates
        self.sender_classes = sender_classes
        self.resolve = resolve          # addr -> (entity_key, entity_name)
        self.stats: dict[str, int] = {}

    def _bump(self, k: str) -> None:
        self.stats[k] = self.stats.get(k, 0) + 1

    def run(self, msg: Message, addr: str) -> Outcome:
        cls = self.sender_classes.get(addr, "unknown")
        if cls == "marketing":
            self._bump("L0/terminate")
            return Outcome("terminate", "L0", "marketing-class sender")
        key, name = self.resolve(addr)
        tpl = self.templates.get(addr)
        if tpl is not None:
            out = tpl.apply(msg, key, name)
            self._bump(f"L1/{out.action}")
            if out.action != "escalate":
                return out
        out = generic(msg, key, name)
        self._bump(f"L2/{out.action}")
        return out


# ---------------------------------------------------------------- event linking

_TITLE = re.compile(r'"([^"]{6,60})"')
_ART = re.compile(r"^(the|a|an)\s+", re.I)


def link_key(row: Row) -> tuple[str, str]:
    """One purchase, three emails. Return (key, basis).

    Preference order, and why:
      ref     -- an order/invoice number is the merchant's own event id.
      title   -- Amazon truncates the order number out of most snippets but
                 keeps the quoted item title in the subject, identical across
                 Ordered/Shipped/Delivered. Weaker: two orders of the same
                 item collide, so it is reported separately from ref linking.
      solo    -- nothing links it; it stays its own event and may double-count.
    """
    if row.ref_number:
        ref = row.ref_number.upper()
        # A third party can report the same order: Example Designs' delivery notice
        # came from exampledesigns.com and its review request from reviews.example, so
        # an entity-scoped key split one purchase in two. Refs that carry a
        # letter and real length are globally unique in practice; short numeric
        # ones (invoice 59115) are not, and stay scoped to the entity.
        distinctive = (len(ref) >= 8 and re.search(r"[A-Z]", ref)) or len(ref) >= 12
        return ((f"ref:{ref}", "ref") if distinctive
                else (f"ref:{row.entity_key}:{ref}", "ref"))
    m = _TITLE.search(clean(row.description or "")) or None
    if m:
        t = _ART.sub("", m.group(1)).lower().rstrip(". ")
        return (f"title:{row.entity_key}:{t}", "title")
    if row.kind in ("booking", "appointment") and row.counterparty:
        # Resy and Google Calendar both report the same dinner, in different
        # date formats ("Tue, Jul. 21 at 7:30pm" vs "Tue Jul 21, 2026 7:30pm"),
        # so the normalised ISO date is the only key that merges them.
        cp = re.sub(r"[^a-z0-9]+", "", row.counterparty.lower())[:28]
        return (f"cal:{cp}:{row.date}", "counterparty+date")
    return (f"solo:{row.message_id}", "none")


# How far apart two messages may sit and still be the same event, per basis.
# A merchant's own reference is unique for all time. A quoted item title is
# not: re-ordering the same Lavazza coffee six months later produced a
# 10-message "event" spanning three separate purchases until this window was
# added. A restaurant booking on the same normalised date needs no window.
_WINDOW_DAYS = {"ref": None, "title": 10, "counterparty+date": None, "none": None}


def _days(a: str, b: str) -> int:
    try:
        return abs((datetime.strptime(a, "%Y-%m-%d") - datetime.strptime(b, "%Y-%m-%d")).days)
    except ValueError:
        return 10 ** 6


def link_events(rows: list[Row]) -> list[Row]:
    """Assign event_id. Rows sharing a link key (within its window) are one event."""
    # The window is measured from the event's ANCHOR (its first message), not
    # from the previous one. A rolling window chains: order 5 Mar, ship 6 Mar,
    # re-order 18 Mar, ship 24 Mar all became one "event" because each hop was
    # short. Anchoring keeps an order and its shipping notices together while
    # a genuine re-order starts a new event.
    open_ev: dict[str, tuple[int, str]] = {}     # key -> (event_id, anchor date)
    n = 0
    for r in sorted(rows, key=lambda x: x.date):
        k, basis = link_key(r)
        r.link_key, r.link_basis = k, basis
        win = _WINDOW_DAYS.get(basis)
        cur = open_ev.get(k)
        if cur is None or (win is not None and _days(r.date, cur[1]) > win):
            n += 1
            open_ev[k] = (n, r.date)
        r.event_id = open_ev[k][0]
    return rows


def collapse(rows: list[Row]) -> list[Row]:
    """One row per event: the richest message wins.

    Richest = has an amount, then highest confidence, then earliest -- the
    order confirmation carries the money, the delivery notice carries nothing,
    and the ledger wants the money.
    """
    best: dict[int, Row] = {}
    for r in rows:
        cur = best.get(r.event_id)
        if cur is None or _rank(r) > _rank(cur):
            best[r.event_id] = r
    return sorted(best.values(), key=lambda r: (r.date, r.entity_name))


def _rank(r: Row) -> tuple:
    return (r.amount is not None, r.confidence, r.ref_number is not None, -len(r.message_id))


def templates_as_json(templates: dict[str, Template]) -> list[dict]:
    return [{"sender": t.sender, "default": t.default, "notes": t.notes,
             "rules": json.dumps(t.raw_rules, ensure_ascii=False)}
            for t in templates.values()]
