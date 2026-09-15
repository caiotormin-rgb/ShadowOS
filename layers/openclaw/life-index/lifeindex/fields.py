"""Lane-0 field extraction: deterministic patterns only.

Every pattern here was validated against real pilot documents -- the
compra-e-venda contract and the school documents -- not invented. Anything ambiguous is
left for a model lane, which records its own provenance.
"""
from __future__ import annotations

import re
from typing import Iterator

# Brazilian and US document identifiers, currency, and dates.
PATTERNS: dict[str, re.Pattern] = {
    "cpf":        re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"),
    "cnpj":       re.compile(r"\b\d{2}\.\d{3}\.\d{3}[./]\d{4}-\d{2}\b"),
    "rg":         re.compile(r"\bRG:?\s*n?º?\s*([\d.\-]{7,15})\b", re.I),
    "amount_brl": re.compile(r"R\$\s?\d{1,3}(?:\.\d{3})*,\d{2}\b"),
    "amount_usd": re.compile(r"\$\s?\d{1,3}(?:,\d{3})*\.\d{2}\b"),
    "matricula":  re.compile(r"matr[ií]cula\s+n?º?\s*([\d.]{3,12})", re.I),
    "date_iso":   re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b"),
    "date_br":    re.compile(r"\b(\d{2}/\d{2}/20\d{2})\b"),
    "policy_no":  re.compile(r"\b(?:policy|ap[oó]lice|account)\s*#?\s*n?º?\s*([A-Z0-9\-]{5,20})\b", re.I),
    "student_id": re.compile(r"\bStudent ID:?\s*(\d{4,12})\b", re.I),
    "order_no":   re.compile(r"\b(?:order|pedido)\s*#?\s*([A-Z0-9\-]{4,20})\b", re.I),
    "envelope_id": re.compile(r"\bEnvelope ID:?\s*([A-F0-9\-]{20,40})\b", re.I),
}

# Fields where the largest value is the interesting one (a contract's price
# beats its instalments); everything else keeps first-seen order.
MAX_FIELDS = {"amount_brl", "amount_usd"}


def _norm_amount(s: str) -> float:
    body = re.sub(r"[^\d.,]", "", s)
    if "," in body and body.rfind(",") > body.rfind("."):
        body = body.replace(".", "").replace(",", ".")
    else:
        body = body.replace(",", "")
    try:
        return float(body)
    except ValueError:
        return 0.0


def _clean(value: str) -> str:
    """Collapse internal whitespace. PDF text wraps mid-value, so a price
    split across two lines must not be stored as 'R$\n16.500,00'."""
    return re.sub(r"\s+", " ", value).strip()


def extract_fields(text: str, *, limit_per_key: int = 6) -> Iterator[tuple[str, str]]:
    """Yield (key, value). Deduplicated, order-stable, capped per key."""
    for key, rx in PATTERNS.items():
        seen: list[str] = []
        for m in rx.finditer(text):
            val = _clean(m.group(1) if m.groups() else m.group(0))
            if val and val not in seen:
                seen.append(val)
        if not seen:
            continue
        if key in MAX_FIELDS:
            seen.sort(key=_norm_amount, reverse=True)
        for val in seen[:limit_per_key]:
            yield key, val
