"""Onboarding and help text, in the language the reader was greeted in.

These are messages sent to people over WhatsApp, so they show what to *say*,
not what to type. Nobody in a supermarket is going to run a subcommand.
"""

from __future__ import annotations

import sqlite3

import sections
from people import language_for


WELCOME = {
    "en": "Hi{name}! This is the household grocery list.",
    "pt": "Oi{name}! Esta é a lista de compras da casa.",
}

JUST_WRITE = {
    "en": "Just write normally — there are no commands to learn:",
    "pt": "É só escrever normalmente — não precisa decorar comando:",
}

EXAMPLES = {
    "en": [
        '"add milk and rice to Costco"',
        '"what\'s still missing?"',
        '"mark the milk as bought"',
        '"close the trip"',
        '"am I missing anything from produce?"',
    ],
    "pt": [
        '"adiciona leite e arroz no Costco"',
        '"o que ainda falta?"',
        '"marca o leite como comprado"',
        '"fecha a compra"',
        '"falta alguma coisa do hortifrúti?"',
    ],
}

MULTIMODAL = {
    "en": "A photo of the fridge or a voice note works too — I read both.",
    "pt": "Foto da geladeira ou áudio também funciona — eu entendo os dois.",
}

# Onboarding is the one moment where asking beats assuming: it happens once,
# nobody is standing in an aisle, and the answers remove questions later.
ASK = {
    "en": "Two quick questions, so I stop guessing:",
    "pt": "Duas perguntas rápidas, pra eu parar de adivinhar:",
}

ASK_STORE = {
    "en": "1. Which store do you usually mean? I will assume it when you do not say.",
    "pt": "1. Qual loja você normalmente quer dizer? Vou assumir ela quando você não falar.",
}

ASK_LANG = {
    "en": "2. Portuguese or English? I am answering in {lang} right now.",
    "pt": "2. Português ou inglês? Estou respondendo em {lang} agora.",
}

ANSWER_HINT = {
    "en": 'Answer however you like — "Costco, and English is fine" works.',
    "pt": 'Responde do jeito que quiser — "Costco, e português tá bom" já serve.',
}

SETTINGS = {
    "en": "Or skip it — you can change either whenever you like:",
    "pt": "Ou pula — dá pra mudar as duas quando quiser:",
}

SETTING_LANG = {
    "en": '• Language — I answer in {lang}. Say "answer in Portuguese" to switch.',
    "pt": '• Idioma — respondo em {lang}. Diga "responde em inglês" para trocar.',
}

SETTING_STORE = {
    "en": '• Default store — I use the last one touched. Say "my store is Costco".',
    "pt": '• Loja padrão — uso a última que foi mexida. Diga "minha loja é o Costco".',
}

KNOWN_STORES = {
    "en": "Stores on file: {stores}.",
    "pt": "Lojas cadastradas: {stores}.",
}

SHARED = {
    "en": "This list is shared with the household, so everyone sees what you add "
          "and who added it.",
    "pt": "A lista é compartilhada com a casa, então todo mundo vê o que você "
          "adiciona e quem adicionou.",
}

CLOSING = {
    "en": 'Say "help" any time.',
    "pt": 'Escreva "ajuda" quando precisar.',
}

LANG_NAME = {"en": {"en": "English", "pt": "Portuguese"},
             "pt": {"en": "inglês", "pt": "português"}}

HELP_TITLE = {"en": "What I can do", "pt": "O que eu faço"}

HELP_ROWS = {
    "en": [
        ("Add", '"add milk, rice and 2 dozen eggs to Costco"'),
        ("See the list", '"what\'s on the list?" — grouped by aisle'),
        ("Mark bought", '"got the milk" or "bought milk and bread"'),
        ("Undo", '"put the milk back" / "I did not buy that"'),
        ("Finish a trip", '"close the trip" — bought items go to history, the rest stays'),
        ("Undo a trip", '"reopen the trip"'),
        ("Remove", '"take the milk off the list" — this one I will confirm first'),
        ("What is missing", '"anything missing from produce?" — from what we usually buy'),
        ("History", '"when did we last buy rice?"'),
        ("What changed", '"what changed on the list today?" or "who bought the milk?"'),
        ("Store order", '"Costco is a warehouse" — changes the aisle order'),
    ],
    "pt": [
        ("Adicionar", '"adiciona leite, arroz e 2 dúzias de ovos no Costco"'),
        ("Ver a lista", '"o que tem na lista?" — agrupado por corredor'),
        ("Marcar comprado", '"peguei o leite" ou "comprei leite e pão"'),
        ("Desfazer", '"devolve o leite pra lista" / "não comprei isso"'),
        ("Fechar a compra", '"fecha a compra" — o comprado vai pro histórico, o resto fica'),
        ("Reabrir", '"reabre a compra"'),
        ("Remover", '"tira o leite da lista" — essa eu confirmo antes'),
        ("O que falta", '"falta algo do hortifrúti?" — pelo que a gente costuma comprar'),
        ("Histórico", '"quando compramos arroz pela última vez?"'),
        ("O que mudou", '"o que mudou na lista hoje?" ou "quem comprou o leite?"'),
        ("Ordem da loja", '"o Costco é atacado" — muda a ordem dos corredores'),
    ],
}

DESTRUCTIVE_NOTE = {
    "en": "I assume rather than ask when a mistake is easy to undo, and tell you "
          "what I assumed. Removing something is the one thing I check first.",
    "pt": "Eu assumo em vez de perguntar quando o erro é fácil de desfazer, e "
          "digo o que assumi. Remover é a única coisa que eu confirmo antes.",
}


def store_names(conn: sqlite3.Connection, group_id: int | None) -> list[str]:
    if group_id is None:
        return []
    return [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM stores WHERE group_id = ? ORDER BY normalized_name",
            (group_id,),
        )
    ]


def onboarding(
    conn: sqlite3.Connection,
    actor: str | None = None,
    lang: str | None = None,
    group_id: int | None = None,
) -> str:
    lang = lang or language_for(conn, actor)
    name = ""
    if actor:
        row = conn.execute(
            "SELECT display_name FROM people WHERE actor = ?", (actor,)
        ).fetchone()
        if row and row["display_name"]:
            name = f", {row['display_name']}"

    out = [WELCOME[lang].format(name=name), "", JUST_WRITE[lang]]
    out += [f"• {line}" for line in EXAMPLES[lang]]
    out += ["", MULTIMODAL[lang], "", ASK[lang], ASK_STORE[lang],
            ASK_LANG[lang].format(lang=LANG_NAME[lang][lang])]

    stores = store_names(conn, group_id)
    if stores:
        out.append(KNOWN_STORES[lang].format(stores=", ".join(stores)))
    out += [ANSWER_HINT[lang], "", SHARED[lang], "", CLOSING[lang]]
    return "\n".join(out)


def help_text(
    conn: sqlite3.Connection,
    actor: str | None = None,
    lang: str | None = None,
    group_id: int | None = None,
) -> str:
    lang = lang or language_for(conn, actor)
    out = [f"*{HELP_TITLE[lang]}*", ""]
    out += [f"• *{label}* — {example}" for label, example in HELP_ROWS[lang]]
    out += ["", DESTRUCTIVE_NOTE[lang]]

    stores = store_names(conn, group_id)
    if stores:
        out += ["", KNOWN_STORES[lang].format(stores=", ".join(stores))]
    sect = ", ".join(sections.label(k, lang) for k in ("produce", "dairy", "pantry"))
    out += ["", {"en": f"Aisles I know include: {sect}, and more.",
                 "pt": f"Corredores que eu conheço: {sect}, entre outros."}[lang]]
    return "\n".join(out)
