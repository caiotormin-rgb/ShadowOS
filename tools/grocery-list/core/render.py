"""Turning a list into text a messaging app will not mangle."""

from __future__ import annotations

import sqlite3

import people
import sections
from catalog import section_for
from items import current_items
from text import now_iso, parse_timestamp, quantity_text


def grouped_items(
    conn: sqlite3.Connection,
    store: str,
    include_purchased: bool = False,
    group_id: int | None = None,
) -> tuple[sqlite3.Row, list[tuple[str, list[sqlite3.Row]]]]:
    """Items bucketed by section, in the order you would walk that store."""
    store_data, rows = current_items(conn, store, include_purchased, group_id)
    buckets: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        key = section_for(conn, row["name"]) or sections.DEFAULT_SECTION
        buckets.setdefault(key, []).append(row)
    order = sections.layout_order(store_data["layout"])
    return store_data, [(k, buckets[k]) for k in order if k in buckets]


def item_line(row: sqlite3.Row) -> str:
    quantity = quantity_text(float(row["quantity"]))
    unit = f" {row['unit']}" if row["unit"] else ""
    amount = "" if quantity == "1" and not row["unit"] else f" x{quantity}{unit}"
    note = f" ({row['note']})" if row["note"] else ""
    return f"{row['name']}{amount}{note}"


def render_grouped(
    store: sqlite3.Row,
    groups: list[tuple[str, list[sqlite3.Row]]],
    lang: str = "en",
    purchased: list[sqlite3.Row] | None = None,
    stamp: str = "",
) -> str:
    """Plain text for a messaging app: no tables, no headers, bold via asterisks."""
    needed = sum(len(rows) for _, rows in groups)
    header = f"{store['name']} — {NEEDED[lang].format(n=needed)}"
    if stamp:
        header = f"{header} · {stamp}"
    out = [header]
    for key, rows in groups:
        out.append("")
        out.append(f"*{sections.label(key, lang)}*")
        out.extend(f"- {item_line(row)}" for row in rows)
    if not groups:
        out.append("")
        out.append("Nothing needed." if lang == "en" else "Nada na lista.")
    if purchased:
        out.append("")
        out.append(f"*{BOUGHT[lang]} ({len(purchased)})*: "
                   + ", ".join(r["name"] for r in purchased))
    return "\n".join(out)


def render_list(store: sqlite3.Row, rows: list[sqlite3.Row], rendered_at: str) -> str:
    lines = [f"Grocery list — {store['name']} — {rendered_at[:10]}"]
    if not rows:
        lines.append("(No items needed)")
    for row in rows:
        quantity = quantity_text(float(row["quantity"]))
        suffix = "" if quantity == "1" and not row["unit"] else f" × {quantity}{(' ' + row['unit']) if row['unit'] else ''}"
        note = f" — {row['note']}" if row["note"] else ""
        lines.append(f"- {row['name']}{suffix}{note}")
    return "\n".join(lines)


# Short confirmations, rendered here rather than composed by the agent. Every
# sentence the CLI can write itself is a model round trip that does not happen,
# and on bad service a round trip is a failure mode, not just a cost.
PHRASES: dict[str, dict[str, str]] = {
    # One item is worth naming: it confirms what was understood. Several are
    # not — enumerating them replays the list the reader already has.
    "added":     {"en": "Added to {store}: {items}.",
                  "pt": "Adicionado em {store}: {items}."},
    "added_n":   {"en": "Added {count} items to {store}.",
                  "pt": "Adicionados {count} itens em {store}."},
    "updated":   {"en": "Updated at {store}: {items}.",
                  "pt": "Atualizado em {store}: {items}."},
    "updated_n": {"en": "Updated {count} items at {store}.",
                  "pt": "Atualizados {count} itens em {store}."},
    "purchased": {"en": "Bought at {store}: {items}.",
                  "pt": "Comprado em {store}: {items}."},
    "purchased_n": {"en": "Bought {count} items at {store}.",
                    "pt": "Comprados {count} itens em {store}."},
    "needed":    {"en": "Back on the {store} list: {items}.",
                  "pt": "De volta à lista de {store}: {items}."},
    "needed_n":  {"en": "{count} items back on the {store} list.",
                  "pt": "{count} itens de volta na lista de {store}."},
    "removed":   {"en": "Removed from {store}: {items}.",
                  "pt": "Removido de {store}: {items}."},
    "removed_n": {"en": "Removed {count} items from {store}.",
                  "pt": "Removidos {count} itens de {store}."},
    "closed":    {"en": "{store} trip closed: {bought} bought, {carried} carried over.",
                  "pt": "Compra em {store} fechada: {bought} comprados, {carried} pendentes."},
    "duplicate": {"en": "{store} trip was already closed.",
                  "pt": "A compra em {store} já estava fechada."},
    "reopened":  {"en": "Reopened {store}: {count} items back on the list.",
                  "pt": "{store} reaberto: {count} itens de volta na lista."},
    "remaining": {"en": "{count} still needed.", "pt": "Faltam {count}."},
    "undo":      {"en": "reopen undoes it.", "pt": "reopen desfaz."},
}


def phrase(key: str, lang: str = "en", **fields: object) -> str:
    return PHRASES[key][lang].format(**fields)


def confirm(
    key: str,
    lang: str = "en",
    assumptions: list[str] | None = None,
    extra: str = "",
    **fields: object,
) -> str:
    """One line: what happened, what was assumed, and what is left.

    Never replays the list — that is what `list` is for.
    """
    line = phrase(key, lang, **fields)
    if extra:
        line = f"{line} {extra}"
    if assumptions:
        line = f"{line} ({'; '.join(assumptions)})"
    return line


ASSUMPTIONS: dict[str, dict[str, str]] = {
    "store_last_touched": {"en": "used {store}, the last store touched",
                           "pt": "usei {store}, a última loja mexida"},
    "store_only":         {"en": "used {store}, the only store on file",
                           "pt": "usei {store}, a única loja cadastrada"},
    "store_personal_default": {"en": "used {store}, the store you set as yours",
                              "pt": "usei {store}, a loja que você definiu"},
    "store_default":      {"en": "used {store}, the configured default",
                           "pt": "usei {store}, o padrão configurado"},
    "item_ambiguous":     {"en": "{name} matched {matches} rows ({choices}); took {chosen}",
                           "pt": "{name} bateu com {matches} linhas ({choices}); peguei {chosen}"},
    "item_partial_match": {"en": "took {name} as {item}",
                           "pt": "entendi {name} como {item}"},
    "item_choice_needed": {"en": "{name} could be {choices}; marked nothing, which one?",
                           "pt": "{name} pode ser {choices}; não marquei nada, qual é?"},
    "item_not_listed":    {"en": "{name} was not on the list; added it as bought",
                           "pt": "{name} não estava na lista; adicionei como comprado"},
}

UNITLESS = {"en": "the unitless one", "pt": "a sem unidade"}


def render_assumption(assumption: dict, lang: str = "en") -> str:
    """Turn a structured assumption into a clause in the reader's language."""
    fields = {k: v for k, v in assumption.items() if k != "kind"}
    if assumption["kind"] == "item_ambiguous" and not fields.get("chosen"):
        fields["chosen"] = UNITLESS[lang]
    return ASSUMPTIONS[assumption["kind"]][lang].format(**fields)


def render_assumptions(assumptions: list[dict], lang: str = "en") -> list[str]:
    return [render_assumption(a, lang) for a in assumptions]


AGE = {
    "now":  {"en": "just now", "pt": "agora mesmo"},
    "min":  {"en": "{n} min ago", "pt": "há {n} min"},
    "hour": {"en": "{n}h ago", "pt": "há {n}h"},
    "day":  {"en": "{n}d ago", "pt": "há {n}d"},
}


def humanize_age(then: str, lang: str = "en", now: str | None = None) -> str:
    seconds = (parse_timestamp(now or now_iso()) - parse_timestamp(then)).total_seconds()
    if seconds < 90:
        return AGE["now"][lang]
    if seconds < 3600:
        return AGE["min"][lang].format(n=int(seconds // 60))
    if seconds < 86400:
        return AGE["hour"][lang].format(n=int(seconds // 3600))
    return AGE["day"][lang].format(n=int(seconds // 86400))


BY = {"en": "by {who}", "pt": "por {who}"}
UPDATED = {"en": "updated {when}", "pt": "atualizada {when}"}
NEEDED = {"en": "{n} needed", "pt": "{n} na lista"}
BOUGHT = {"en": "Already bought", "pt": "Já comprados"}


def last_touched(conn, store: str, group_id: int | None = None) -> dict | None:
    """When the list last changed and who changed it, from the event log.

    Scoped to the household: two of them may each have a Costco, and reading
    the log by store name alone printed the neighbouring household's member —
    their phone number — in the footer of this household's list.
    """
    clause, params = "", [store]
    if group_id is not None:
        clause = "AND group_id = ?"
        params.append(group_id)
    row = conn.execute(
        f"""
        SELECT occurred_at, actor FROM events
        WHERE store = ? {clause}
          AND action IN ('added','merged','purchased','unpurchased','removed',
                         'trip_purchased','trip_missing','reopened')
        ORDER BY occurred_at DESC, id DESC LIMIT 1
        """,
        params,
    ).fetchone()
    if not row:
        return None
    # The name they go by; a phone number with no name is masked, because the
    # footer is read by everyone else in the household.
    return {"at": row["occurred_at"], "actor": people.public_label(conn, row["actor"])}


def render_stamp(touched: dict | None, lang: str = "en") -> str:
    if not touched:
        return ""
    out = UPDATED[lang].format(when=humanize_age(touched["at"], lang))
    if touched["actor"]:
        out = f"{out} {BY[lang].format(who=touched['actor'])}"
    return out


def confirm_items(
    key: str,
    names: list[str],
    lang: str = "en",
    assumptions: list[str] | None = None,
    extra: str = "",
    **fields: object,
) -> str:
    """Confirm an action over items, naming one and counting several.

    Per-item detail is available from `list` and from JSON output; a
    confirmation should say what happened, not recite what the reader asked for.
    """
    if len(names) == 1:
        return confirm(key, lang, assumptions, extra, items=names[0], **fields)
    return confirm(f"{key}_n", lang, assumptions, extra, count=len(names), **fields)


DUE = {
    "header":  {"en": "Usually bought by now, not on the {store} list{scope}:",
                "pt": "Normalmente já comprado a esta altura e fora da lista de {store}{scope}:"},
    "none":    {"en": "Nothing looks overdue at {store}{scope}.",
                "pt": "Nada parece atrasado em {store}{scope}."},
    "scope":   {"en": " in {section}", "pt": " em {section}"},
    "line":    {"en": "- {name} — every {interval}d, last bought {since}d ago",
                "pt": "- {name} — a cada {interval}d, última compra há {since}d"},
    "thin":    {"en": "(Based on {n} products with a repeat pattern.)",
                "pt": "(Com base em {n} produtos com padrão de repetição.)"},
}


def render_due(
    store: str,
    rows: list[dict],
    lang: str = "en",
    section: str | None = None,
) -> str:
    """Answer 'am I missing something from produce?' without a model call."""
    scope = DUE["scope"][lang].format(section=sections.label(section, lang)) if section else ""
    if not rows:
        return DUE["none"][lang].format(store=store, scope=scope)
    out = [DUE["header"][lang].format(store=store, scope=scope)]
    out += [
        DUE["line"][lang].format(
            name=r["name"], interval=r["typical_interval_days"], since=r["days_since"]
        )
        for r in rows
    ]
    return "\n".join(out)
