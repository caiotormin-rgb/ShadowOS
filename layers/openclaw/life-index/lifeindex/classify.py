"""Lane-0 document classification: type and tier from filename plus text.

Deliberately conservative. An unrecognised document is tier 3 and untyped,
never guessed into tier 1 -- misfiling a receipt as an identity document is
cheap, the reverse is not.
"""
from __future__ import annotations

import re

# (doc_type, tier, pattern). First match wins, so order is priority.
RULES: list[tuple[str, int, re.Pattern]] = [
    ("identity",   1, re.compile(r"\b(driver'?s?[ _-]?licen[cs]e|passport|birth certificate|"
                                 r"social security|green ?card|carteira de identidade|CNH|RG)\b", re.I)),
    ("tax",        1, re.compile(r"\b(W-?2|1099|1040|tax return|imposto de renda|IRPF|"
                                 r"informe de rendimentos)\b", re.I)),
    ("contract",   1, re.compile(r"\b(compra e venda|escritura|deed|lease agreement|"
                                 r"contrato|instrumento particular|closing disclosure)\b", re.I)),
    ("insurance",  1, re.compile(r"\b(ap[oó]lice|insurance polic|certificate of coverage|"
                                 r"declaration page)\b", re.I)),
    # 'immuniz' is a prefix, not a word: the real documents say
    # "IMMUNIZATION", "Immunizations", and "imunizacao". A \b-anchored word
    # match missed all of them and filed a school health notice as tier 3.
    ("education",  2, re.compile(r"\b(IEP|individualized education|diploma|hist[oó]rico escolar|"
                                 r"transcript)\b|immuniz|imuniza", re.I)),
    ("medical",    2, re.compile(r"\b(explanation of benefits|prior authorization|laudo|"
                                 r"prescription|receitu[aá]rio|insurance verification)\b", re.I)),
    ("financial",  2, re.compile(r"\b(statement|extrato|brokerage|prospectus|"
                                 r"comprovante de transfer[eê]ncia)\b", re.I)),
    ("invoice",    3, re.compile(r"\b(invoice|fatura|boleto|carta de cobran[cç]a|bill)\b", re.I)),
    ("receipt",    3, re.compile(r"\b(receipt|recibo|order confirmation|comprovante de pagamento)\b", re.I)),
]

MAX_SNIPPET = 4000


def classify(*, filename: str = "", text: str = "") -> tuple[str | None, int]:
    """Return (doc_type, tier). Filename is weighted by being checked first."""
    haystack_name = filename or ""
    haystack_text = (text or "")[:MAX_SNIPPET]
    for doc_type, tier, rx in RULES:
        if rx.search(haystack_name):
            return doc_type, tier
    for doc_type, tier, rx in RULES:
        if rx.search(haystack_text):
            return doc_type, tier
    return None, 3


def title_for(*, filename: str, text: str = "") -> str:
    """A human title: the first substantial line of the document, else the
    filename with its extension and transport noise stripped."""
    for line in (text or "").splitlines():
        s = line.strip()
        if 12 <= len(s) <= 110 and not s.lower().startswith(("docusign", "http", "page ")):
            return s
    stem = re.sub(r"\.(docx|pdf|xlsx|jpg|png)$", "", filename, flags=re.I)
    return re.sub(r"[_-]+", " ", stem).strip() or filename
