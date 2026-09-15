"""Conversational member replies; presentation only, no DB or tool authority.

Legacy CLI renderers remain unchanged. Names, quantities, choices and confirmations
come from the deterministic core; this module changes how people read them.
"""
from __future__ import annotations

import sections
from text import quantity_text

SHORT_BATCH = 6
EMOJI = {
    'produce': '🥬', 'bakery': '🍞', 'butcher': '🥩', 'seafood': '🐟',
    'dairy': '🥛', 'frozen': '🧊', 'pantry': '🥫', 'snacks': '🍪',
    'beverages': '🥤', 'household': '🧽', 'personal_care': '🧴',
    'baby': '🍼', 'pet': '🐾', 'other': '🛒',
}


def say(lang, en, pt):
    return pt if lang == 'pt' else en


def bullets(names, emoji='🛒'):
    return '\n'.join(f'{emoji} {name}' for name in names)


def item(row):
    name = row['name']
    quantity = quantity_text(float(row['quantity']))
    unit = row['unit']
    amount = f" — {quantity}{' ' + unit if unit else ''}" if quantity != '1' or unit else ''
    note = f" ({row['note']})" if row['note'] else ''
    return f'{name}{amount}{note}'


def changed(kind, names, store, lang):
    """Name short batches; keep large confirmations brief without replaying a list."""
    if not names:
        return ''
    introductions = {
        'added': say(lang, f'Got it! Added to your *{store}* list:', f'Pronto, anotei na lista de *{store}*:'),
        'updated': say(lang, f'Updated these on your *{store}* list:', f"Atualizei {'este item' if len(names) == 1 else 'estes itens'} na lista de *{store}*:"),
        'purchased': say(lang, f'Marked as bought at *{store}*:', f"Marquei como {'comprado' if len(names) == 1 else 'comprados'} em *{store}*:"),
        'needed': say(lang, f'Back on your *{store}* list:', f'Coloquei de volta na lista de *{store}*:'),
        'removed': say(lang, f'Removed from your *{store}* list:', f'Tirei da lista de *{store}*:'),
    }
    emoji = {'purchased': '✅', 'removed': '➖', 'needed': '↩️'}.get(kind, '🛒')
    if len(names) <= SHORT_BATCH:
        return introductions[kind] + '\n' + bullets(names, emoji)
    total = len(names)
    summaries = {
        'added': say(lang, f'Got it! Added {total} items to your *{store}* list.', f'Pronto, anotei os {total} itens na lista de *{store}*.'),
        'updated': say(lang, f'Updated {total} items on your *{store}* list.', f'Atualizei os {total} itens na lista de *{store}*.'),
        'purchased': say(lang, f'Marked {total} items as bought at *{store}*.', f'Marquei os {total} itens como comprados em *{store}*.'),
        'needed': say(lang, f'Put {total} items back on your *{store}* list.', f'Coloquei os {total} itens de volta na lista de *{store}*.'),
        'removed': say(lang, f'Removed {total} items from your *{store}* list.', f'Tirei os {total} itens da lista de *{store}*.'),
    }
    return f'{emoji} {summaries[kind]}'


def shopping_list(store, groups, purchased, lang):
    count = sum(len(rows) for _, rows in groups)
    if count:
        intro = say(lang, f'Here’s your *{store}* list 🛒', f'Aqui está sua lista de *{store}* 🛒')
    else:
        intro = say(lang, f'Nothing left to buy at *{store}*! ✅', f'Não falta nada na lista de *{store}*! ✅')
    out = [intro]
    for section, rows in groups:
        if count > SHORT_BATCH:
            out += ['', f'*{sections.label(section, lang)}*']
        elif len(out) == 1:
            out.append('')
        out.extend(f"{EMOJI.get(section, '🛒')} {item(row)}" for row in rows)
    if purchased:
        out += ['', say(lang, 'Already bought:', 'Já comprados:')]
        out.extend(f'✅ {item(row)}' for row in purchased)
    return '\n'.join(out)


def item_assumptions(assumptions, lang):
    """Keep consequential matching decisions, omit redundant store mechanics."""
    out = []
    for assumption in assumptions:
        kind = assumption['kind']
        if kind == 'item_partial_match':
            out.append(say(lang, f"I took *{assumption['name']}* to mean *{assumption['item']}*.",
                           f"Entendi *{assumption['name']}* como *{assumption['item']}*."))
        elif kind == 'item_ambiguous':
            unit = assumption['chosen'] or say(lang, 'no unit', 'sem unidade')
            out.append(say(lang, f"There’s more than one *{assumption['name']}*; I chose the one marked *{unit}*.",
                           f"Há mais de uma opção de *{assumption['name']}*; escolhi a de *{unit}*."))
        elif kind == 'item_not_listed':
            out.append(say(lang, f"*{assumption['name']}* wasn’t on the list, so I added it as bought.",
                           f"*{assumption['name']}* não estava na lista, então já registrei como comprado."))
        # item_choice_needed is rendered from structured candidates below.
    return '\n'.join(out)


def choices(groups, lang):
    out = []
    for group in groups:
        out.append(say(lang, f"Which one did you mean by *{group['name']}*? I haven’t marked it yet.",
                       f"Qual destes você quis dizer com *{group['name']}*? Ainda não marquei esse item."))
        out.append(bullets(group['candidates'], '🔹'))
    return '\n'.join(out)


def remaining(count, lang):
    if count == 0:
        return say(lang, 'That’s everything on the list! 🎉', 'Tudo da lista comprado! 🎉')
    if count == 1:
        return say(lang, 'Just one item left.', 'Agora só falta um item.')
    return say(lang, f'{count} items left to buy.', f'Agora faltam {count} itens.')


def preferences(person, lang, zone, changed_keys=None):
    reading = changed_keys is None
    out = [say(lang, 'Here’s how I have things set up for you:', 'Suas preferências estão assim:') if reading else
           say(lang, 'Got it, saved your preferences:', 'Combinado, salvei suas preferências:')]
    keys = {'lang', 'store', 'name', 'timezone'} if reading else set(changed_keys)
    if 'lang' in keys:
        name = {'en': say(lang, 'English', 'inglês'), 'pt': say(lang, 'Portuguese', 'português')}[person['lang']]
        out.append(say(lang, f'💬 I’ll reply in {name}.', f'💬 Vou responder em {name}.'))
    if 'store' in keys:
        store = person['default_store']
        if store:
            out.append(say(lang, f'🛒 Your usual store: {store}.', f'🛒 Sua loja de costume: {store}.'))
        else:
            out.append(say(lang, '🛒 No usual store chosen yet.', '🛒 Você ainda não escolheu uma loja de costume.'))
    if 'name' in keys and person['display_name']:
        out.append(say(lang, f"👋 I’ll call you {person['display_name']}.", f"👋 Vou chamar você de {person['display_name']}."))
    if 'timezone' in keys:
        city = zone.rsplit('/', 1)[-1].replace('_', ' ')
        if lang == 'pt':
            city = {'Sao Paulo': 'São Paulo', 'New York': 'Nova York', 'Lisbon': 'Lisboa'}.get(city, city)
        out.append(say(lang, f'🕒 Times shown for {city}.', f'🕒 Horários no fuso de {city}.'))
    return '\n'.join(out)


def store_list(names, lang):
    if not names:
        return say(lang, 'No stores yet. Which store would you like to start with?', 'Ainda não temos lojas por aqui. Em qual você quer começar?')
    return say(lang, 'Here are your stores:', 'Estas são suas lojas:') + '\n' + bullets(names)


def removal_preview(rows, store, code, lang):
    # Always enumerate the full destructive selection; never summarize it away.
    out = [say(lang, f'Want me to remove these from *{store}*?', f"Quer que eu tire {'este item' if len(rows) == 1 else 'estes itens'} da lista de *{store}*?")]
    out.append(bullets([item(row) for row in rows], '➖'))
    out += ['', say(lang, f'To confirm, send /remover {code} within 5 minutes.',
                    f'Para confirmar, mande /remover {code} em até 5 minutos.')]
    return '\n'.join(out)


def trip_closed(store, bought, carried, duplicate, lang):
    if duplicate:
        return say(lang, f'That *{store}* trip is already closed. ✅', f'Essa compra em *{store}* já está fechada. ✅')
    out = [say(lang, f'All set, closed your *{store}* trip!', f'Pronto, fechei sua compra em *{store}*!')]
    out.append(say(lang, f'✅ {bought} bought — saved in your history.', f"✅ {bought} {'item comprado ficou' if bought == 1 else 'itens comprados ficaram'} no histórico."))
    if carried:
        out.append(say(lang, f'🛒 {carried} still on the list for next time.', f"🛒 {carried} {'item continua' if carried == 1 else 'itens continuam'} na lista para a próxima compra."))
    out += ['', say(lang, 'If you need to undo that, just ask me to reopen the trip.', 'Se precisar desfazer, é só pedir para reabrir a compra.')]
    return '\n'.join(out)



def trip_reopened(store, names, lang):
    total = len(names)
    intro = say(lang, f'Reopened your *{store}* trip. These items are back on the list:',
                f'Reabri sua compra em *{store}*. Estes itens voltaram para a lista:')
    if total <= SHORT_BATCH:
        return intro + '\n' + bullets(names, '↩️')
    return say(lang, f'Reopened your *{store}* trip — {total} items are back on the list. ↩️',
               f'Reabri sua compra em *{store}*. Os {total} itens voltaram para a lista. ↩️')


def due_items(store, rows, lang, section=None):
    scope = f" ({sections.label(section, lang)})" if section else ''
    if not rows:
        return say(lang, f'I don’t see anything to restock at *{store}*{scope} based on your history.',
                   f'Pelo histórico, não vejo nada para repor em *{store}*{scope} agora.')
    out = [say(lang, f'You might want to check these for *{store}*{scope}:', f'Vale conferir estes itens para *{store}*{scope}:')]
    for row in rows:
        note = say(lang, f"last bought {row['days_since']} days ago; usually every {row['typical_interval_days']} days",
                   f"última compra há {row['days_since']} dias; costuma comprar a cada {row['typical_interval_days']} dias")
        out.append(f"🛒 {row['name']} — {note}")
    return '\n'.join(out)


def help_text(lang, onboarding=False, person=None):
    if onboarding:
        intro = say(lang,
            'Hi! I can help keep your shopping list up to date. 🛒\n\n'
            '📝 “Add milk and eggs”\n✅ “Got the milk”\n🛒 “Show my list”',
            'Oi! Posso ajudar a organizar suas compras. 🛒\n\n'
            '📝 “Anota leite e ovos”\n✅ “Peguei o leite”\n🛒 “Mostra minha lista”')
        person = person or {}
        store, preferred_lang = person.get('default_store'), person.get('lang')
        if store and preferred_lang:
            language = {'en': say(lang, 'English', 'inglês'), 'pt': say(lang, 'Portuguese', 'português')}[preferred_lang]
            closing = say(lang, f'I’ll use *{store}* and reply in {language}. Send over your items whenever you’re ready.',
                          f'Vou usar *{store}* e responder em {language}. Pode mandar os itens quando quiser.')
        elif preferred_lang:
            closing = say(lang, 'Which store do you usually shop at?', 'Qual loja você costuma usar?')
        elif store:
            closing = say(lang, f'I’ll use *{store}*. Would you prefer English or Portuguese?',
                          f'Vou usar *{store}*. Prefere conversar em português ou inglês?')
        else:
            closing = say(lang, 'Which store do you usually shop at? And would you prefer English or Portuguese?',
                          'Qual loja você costuma usar? E prefere conversar em português ou inglês?')
        return intro + '\n\n' + closing
    return say(lang,
        'Just tell me what you need. For example:\n\n'
        '📝 “Add milk and eggs”\n🛒 “What’s on my list?”\n✅ “Got the milk”\n'
        '↩️ “Put the milk back on the list”\n🏁 “Close the trip”\n'
        '🕒 “What changed today?”\n\n'
        'You can ask to remove items too — I’ll show you what will be removed and ask you to confirm first.',
        'Pode falar do seu jeito. Por exemplo:\n\n'
        '📝 “Anota leite e ovos”\n🛒 “O que tem na lista?”\n✅ “Peguei o leite”\n'
        '↩️ “Devolve o leite pra lista”\n🏁 “Fecha a compra”\n'
        '🕒 “O que mudou hoje?”\n\n'
        'Também dá para tirar itens da lista — antes eu mostro quais são e peço sua confirmação.')
