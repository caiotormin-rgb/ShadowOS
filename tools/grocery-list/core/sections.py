#!/usr/bin/env python3
"""Bilingual (pt-BR + English) grocery product -> store section classifier.

Standalone and dependency free: Python standard library only, no model, no
data files, no network. This is a small curated keyword map, deliberately
sized for a household list of a few hundred distinct products rather than a
retail catalogue (see ROADMAP.md item 5 for why the open datasets were
rejected).

Public surface
--------------
``SECTIONS``          ordered ``{key: {"en": label, "pt": label}}`` in the order
                      a shopper walks the store.
``SECTION_KEYS``      tuple of section keys, same order.
``DEFAULT_SECTION``   ``"other"`` -- the catch-all bucket. Never returned by
                      ``classify``; it exists so callers have a stable key for
                      items that belong to no aisle.
``classify(name)``    -> section key, or ``None`` when genuinely unknown.
``label(key, lang)``  -> display label for ``"en"`` or ``"pt"``.
``section_list(lang)``-> ``[(key, label), ...]`` in store-walk order.
``fold(text)``        -> the normalized matching form (mirrors grocery.py's
                      ``clean_text``/``normalized``, then folds accents).
``term_count()``      -> how many distinct terms the map recognizes.

``classify`` returns ``None`` rather than guessing. The caller (an LLM agent)
classifies an unknown product once and persists the answer, so a confidently
wrong section costs more than an honest miss.

Matching is whole-token only -- never substrings -- so "leite" does not match
inside "leiteira". It is robust to case, accents ("pao" == "pão"), plurals in
both languages ("bananas", "limões", "tomatoes"), quantity/unit noise
("2 kg arroz"), and multi-word names, where a listed multi-word term always
wins over its parts ("leite de coco" -> pantry, not dairy).
"""

from __future__ import annotations

import re
import unicodedata
from itertools import product

__all__ = [
    "SECTIONS",
    "SECTION_KEYS",
    "DEFAULT_SECTION",
    "LANGS",
    "classify",
    "label",
    "section_list",
    "fold",
    "term_count",
]


# --------------------------------------------------------------------------
# Sections. Order is store-walk order: perimeter first, then the aisles.
# --------------------------------------------------------------------------

SECTIONS: dict[str, dict[str, str]] = {
    "produce": {"en": "Produce", "pt": "Hortifrúti"},
    "bakery": {"en": "Bakery", "pt": "Padaria"},
    "butcher": {"en": "Butcher", "pt": "Açougue"},
    "seafood": {"en": "Seafood", "pt": "Peixaria"},
    "dairy": {"en": "Dairy & Eggs", "pt": "Laticínios e Ovos"},
    "frozen": {"en": "Frozen", "pt": "Congelados"},
    "pantry": {"en": "Pantry", "pt": "Mercearia"},
    "snacks": {"en": "Snacks", "pt": "Petiscos e Doces"},
    "beverages": {"en": "Beverages", "pt": "Bebidas"},
    "household": {"en": "Household", "pt": "Limpeza e Casa"},
    "personal_care": {"en": "Personal Care", "pt": "Higiene e Beleza"},
    "baby": {"en": "Baby", "pt": "Bebê"},
    "pet": {"en": "Pet", "pt": "Pet Shop"},
    "other": {"en": "Other", "pt": "Outros"},
}

SECTION_KEYS: tuple[str, ...] = tuple(SECTIONS)
DEFAULT_SECTION = "other"
LANGS: tuple[str, ...] = ("en", "pt")

# Store-walk orders. A supermarket is a grid entered at produce, with staples
# (milk, eggs, bread) pushed to the back so you cross the aisles. A warehouse
# club carries a fraction of the SKUs, opens on seasonal and non-food, and
# keeps perishables on the back perimeter. Walking a Costco in supermarket
# order means crossing the building twice, so the order belongs to the store.
LAYOUTS: dict[str, tuple[str, ...]] = {
    "supermarket": (
        "produce", "bakery", "butcher", "seafood", "dairy", "frozen",
        "pantry", "snacks", "beverages", "household", "personal_care",
        "baby", "pet", "other",
    ),
    "warehouse": (
        "household", "personal_care", "baby", "pet", "snacks", "pantry",
        "beverages", "bakery", "produce", "butcher", "seafood", "dairy",
        "frozen", "other",
    ),
}

DEFAULT_LAYOUT = "supermarket"


def layout_order(layout: str = DEFAULT_LAYOUT) -> tuple[str, ...]:
    """Section keys in the order you would walk them in that kind of store."""
    try:
        return LAYOUTS[layout]
    except KeyError:
        raise ValueError(f"unknown layout: {layout}") from None


# --------------------------------------------------------------------------
# Curated bilingual keyword map: section -> language -> terms.
#
# Terms are written naturally (accents, spaces) and normalized at import.
# Multi-word terms are also indexed without their connectors, so "leite de
# coco" and "leite coco" both resolve.
#
# Deliberate omissions, because the term is genuinely ambiguous and a wrong
# answer is worse than None: "salsa" (pt parsley / en sauce), bare "creme",
# bare "pasta", bare "prato", bare "pepper", bare "lenço", bare "papel".
# --------------------------------------------------------------------------

KEYWORDS: dict[str, dict[str, tuple[str, ...]]] = {
    "produce": {
        "en": (
            "apple", "arugula", "avocado", "banana", "basil", "beet", "beetroot",
            "bell pepper", "blackberry", "blueberry", "broccoli", "cabbage",
            "carrot", "cauliflower", "celery", "chard", "cilantro", "coconut",
            "corn", "cucumber", "eggplant", "fruit", "garlic", "ginger", "grape",
            "grapefruit", "green bean", "greens", "guava", "herbs", "kale",
            "kiwi", "leek", "lemon", "lettuce", "lime", "mandarin", "mango",
            "melon", "mint", "mushroom", "okra", "onion", "orange", "papaya",
            "parsley", "passion fruit", "peach", "pear", "pineapple", "plantain",
            "plum", "potato", "potatoes", "pumpkin", "radish", "raspberry",
            "salad", "scallion", "spinach", "squash", "strawberry",
            "sweet potato", "tangerine", "tomato", "tomatoes", "turnip",
            "vegetable", "watermelon", "yam", "zucchini",
        ),
        "pt": (
            "abacate", "abacaxi", "abóbora", "abobrinha", "acelga", "agrião",
            "aipim", "alface", "alho", "alho poró", "ameixa", "amora", "banana",
            "batata", "batata doce", "bergamota", "berinjela", "beterraba",
            "brócolis", "caju", "cebola", "cebolinha", "cenoura", "cheiro verde",
            "chuchu", "coco", "couve", "couve flor", "ervilha", "espinafre",
            "framboesa", "fruta", "gengibre", "goiaba", "hortaliça", "hortelã",
            "hortifruti", "inhame", "jiló", "laranja", "legume", "limão", "maçã",
            "mamão", "mandioca", "mandioquinha", "manga", "manjericão",
            "maracujá", "melancia", "melão", "mexerica", "milho", "morango",
            "nabo", "pepino", "pera", "pêssego", "pimentão", "quiabo",
            "rabanete", "repolho", "rúcula", "salsinha", "tangerina", "tomate",
            "uva", "vagem", "verdura",
        ),
    },
    "bakery": {
        "en": (
            "baguette", "bagel", "bread", "breadstick", "brioche", "bun", "cake",
            "croissant", "donut", "doughnut", "muffin", "pastry", "pie", "pita",
            "roll", "sourdough", "toast", "tortilla", "whole wheat bread",
        ),
        "pt": (
            "baguete", "bisnaga", "bolo", "broa", "coxinha", "croissant", "cuca",
            "empada", "esfiha", "massa folhada", "pão", "pão de alho",
            "pão de forma", "pão de queijo", "pão francês", "pão integral",
            "panetone", "rosca", "rosquinha", "salgado", "sonho", "torrada",
            "torta",
        ),
    },
    "butcher": {
        "en": (
            "bacon", "beef", "brisket", "chicken", "chicken breast",
            "chicken thigh", "drumstick", "ground beef", "ham", "hamburger",
            "hot dog", "lamb", "liver", "meat", "meatball", "pepperoni", "pork",
            "pork chop", "ribs", "salami", "sausage", "steak", "turkey", "veal",
        ),
        "pt": (
            "acém", "alcatra", "almôndega", "asa de frango", "bacon", "bife",
            "bisteca", "calabresa", "carne", "carne de sol", "carne moída",
            "carneiro", "charque", "contrafilé", "cordeiro", "costela",
            "costelinha", "coxa", "coxão duro", "coxão mole", "cupim",
            "file de frango", "filé mignon", "fígado", "fraldinha", "frango",
            "hambúrguer", "linguiça", "lombo", "maminha", "mortadela", "músculo",
            "pancetta", "patinho", "peito de frango", "pernil", "peru", "picanha",
            "picadinho", "porco", "presunto", "salame", "salsicha", "sobrecoxa",
            "toucinho",
        ),
    },
    "seafood": {
        "en": (
            "anchovy", "clam", "cod", "crab", "fish", "hake", "lobster",
            "mussel", "octopus", "oyster", "salmon", "sardine", "scallop",
            "seafood", "shrimp", "squid", "tilapia", "trout", "tuna",
        ),
        "pt": (
            "anchova", "atum", "bacalhau", "camarão", "caranguejo", "corvina",
            "dourada", "frutos do mar", "lagosta", "lula", "marisco", "merluza",
            "mexilhão", "ostra", "peixe", "pescada", "polvo", "robalo", "salmão",
            "sardinha", "siri", "tainha", "tilápia", "truta", "vieira",
        ),
    },
    "dairy": {
        "en": (
            "buttermilk", "butter", "cheddar", "cheese", "condensed milk",
            "cottage cheese", "cream", "cream cheese", "egg", "eggs",
            "evaporated milk", "greek yogurt", "heavy cream", "kefir",
            "margarine", "milk", "mozzarella", "parmesan", "ricotta",
            "skim milk", "sour cream", "whole milk", "yoghurt", "yogurt",
        ),
        "pt": (
            "bebida láctea", "catupiry", "coalhada", "creme de leite",
            "cream cheese", "iogurte", "queijo", "queijo minas",
            "queijo ralado", "leite", "leite condensado", "leite desnatado",
            "leite integral", "leite semidesnatado", "manteiga", "margarina",
            "muçarela", "mussarela", "nata", "ovo", "parmesão", "requeijão",
            "ricota",
        ),
    },
    "frozen": {
        "en": (
            "frozen", "frozen pizza", "frozen vegetables", "french fries",
            "fries", "ice", "ice cream", "lasagna", "nugget", "pizza",
            "popsicle", "waffle",
        ),
        "pt": (
            "açaí", "batata frita", "congelado", "empanado", "gelo", "lasanha",
            "nuggets", "picolé", "pizza congelada", "polpa de fruta", "sorvete",
        ),
    },
    "pantry": {
        "en": (
            "baking powder", "baking soda", "bay leaf", "beans", "black pepper",
            "bouillon", "bread crumbs", "broth", "canned corn", "canned tuna",
            "cereal", "chickpea", "cinnamon", "cocoa", "coconut milk",
            "condiment", "cooking oil", "corn flour", "cornstarch", "couscous",
            "cumin", "curry", "flour", "gelatin", "granola", "honey", "jam",
            "jelly", "ketchup", "lentil", "mayo", "mayonnaise", "milk powder",
            "molasses", "mustard", "noodle", "oat", "oatmeal", "oil", "olive",
            "olive oil", "oregano", "paprika", "pasta", "peanut butter",
            "pickles", "quinoa", "raisin", "rice", "salt", "sauce", "seasoning",
            "soup", "soy sauce", "spaghetti", "spice", "sugar", "sweetener",
            "syrup", "tomato paste", "tomato sauce", "vanilla", "vinegar",
            "wheat flour", "yeast",
        ),
        "pt": (
            "achocolatado", "açúcar", "adoçante", "alho em pó", "amido de milho",
            "arroz", "atum em lata", "aveia", "azeite", "azeitona",
            "bicarbonato", "cacau", "café", "caldo", "caldo de galinha", "canela",
            "cereal", "chá", "chocolate em pó", "coco ralado", "colorau",
            "colorífico", "cominho", "creme de cebola", "cuscuz", "doce de leite",
            "ervilha em lata", "espaguete", "extrato de tomate", "farinha",
            "farinha de mandioca", "farinha de rosca", "farinha de trigo",
            "farofa", "feijão", "feijão carioca", "feijão preto", "fermento",
            "fubá", "gelatina", "geleia", "grão de bico", "ketchup",
            "leite de coco", "leite em pó", "lentilha", "louro", "macarrão",
            "macarrão instantâneo", "maisena", "maionese", "mel", "milho verde",
            "miojo", "molho", "molho de tomate", "mostarda", "óleo", "orégano",
            "pasta de amendoim", "manteiga de amendoim", "picles", "pimenta",
            "pimenta do reino", "polenta", "polvilho", "pudim", "quinoa", "sal",
            "sardinha em lata", "shoyu", "sopa", "sucrilhos", "tapioca",
            "tempero", "trigo", "uva passa", "vinagre",
        ),
    },
    "snacks": {
        "en": (
            "almond", "candy", "cashew", "cereal bar", "chips", "chocolate",
            "chocolate bar", "cookie", "cracker", "granola bar", "gum",
            "marshmallow", "nuts", "peanut", "pistachio", "popcorn", "potato chips",
            "pretzel", "snack", "trail mix", "wafer", "walnut",
            "hazelnut spread", "popcorn kernels",
        ),
        "pt": (
            "amêndoa", "amendoim", "bala", "barra de cereal", "batata chips",
            "batata palha", "milho de pipoca", "creme de avelã",
            "biscoito", "bolacha", "bombom", "brigadeiro", "castanha",
            "castanha de caju", "castanha do pará", "chiclete", "chocolate",
            "cream cracker", "doce", "nozes", "paçoca", "pé de moleque",
            "pipoca", "pistache", "salgadinho", "torresmo", "wafer",
        ),
    },
    "beverages": {
        "en": (
            "apple juice", "beer", "champagne", "cider", "coconut water", "coke",
            "cola", "energy drink", "gin", "iced tea", "juice", "kombucha",
            "lemonade", "orange juice", "rum", "soda", "soft drink",
            "sparkling water", "sports drink", "tequila", "tonic", "vodka",
            "whiskey", "wine", "water",
        ),
        "pt": (
            "água", "água com gás", "água de coco", "água mineral", "cachaça",
            "cerveja", "champanhe", "chá gelado", "energético", "espumante",
            "gim", "guaraná", "isotônico", "licor", "limonada", "néctar",
            "refresco", "refrigerante", "rum", "sidra", "suco",
            "suco de laranja", "suco em pó", "vinho", "vodka", "whisky",
        ),
    },
    "household": {
        "en": (
            "air freshener", "aluminum foil", "battery", "bleach", "broom",
            "candle", "cleaner", "cling film", "detergent", "dish soap",
            "disinfectant", "dishwasher", "fabric softener", "garbage bag",
            "glove", "insecticide", "laundry detergent", "light bulb", "matches",
            "mop", "napkin", "paper towel", "plastic wrap", "scouring pad",
            "sponge", "steel wool", "toilet paper", "trash bag",
            "charcoal", "lighter", "paper plate", "parchment paper",
            "plastic cup", "wax paper",
        ),
        "pt": (
            "água sanitária", "álcool", "alvejante", "amaciante", "aromatizante",
            "bucha", "cloro", "desengordurante", "desinfetante", "desodorizador",
            "detergente", "esponja", "filme plástico", "fósforo", "guardanapo",
            "inseticida", "lâmpada", "limpador", "lustra móveis", "luva",
            "multiuso", "palha de aço", "pano de chão",
            "pano de prato", "papel alumínio", "papel filme", "papel higiênico",
            "papel toalha", "pilha", "rodo", "sabão", "sabão em pó",
            "sabão líquido", "saco de lixo", "saponáceo", "vassoura", "vela",
            "carvão", "copo descartável", "isqueiro", "papel manteiga",
            "prato descartável", "talher descartável",
        ),
    },
    "personal_care": {
        "en": (
            "aspirin", "band aid", "bandage", "bar soap", "body wash", "comb",
            "conditioner", "cotton", "cotton swab", "deodorant", "floss",
            "hair gel", "hairspray", "hand sanitizer", "ibuprofen", "lotion",
            "makeup", "medicine", "moisturizer", "mouthwash", "nail clipper",
            "hairbrush", "perfume", "razor", "sanitary pad", "shampoo",
            "shaving cream",
            "soap", "sunscreen", "tampon", "tissue", "toothbrush", "toothpaste",
            "vitamin",
        ),
        "pt": (
            "absorvente", "álcool em gel", "algodão", "antisséptico bucal",
            "aparelho de barbear", "condicionador", "cortador de unha",
            "cotonete", "creme de barbear", "creme dental", "curativo",
            "desodorante", "dipirona", "enxaguante bucal", "escova de dente",
            "esmalte", "fio dental", "gilete", "hidratante", "lâmina de barbear",
            "lenço de papel", "maquiagem", "paracetamol", "pasta de dente",
            "escova de cabelo", "pente", "perfume", "protetor solar",
            "remédio", "sabonete",
            "sabonete líquido", "shampoo", "talco", "vitamina", "xampu",
        ),
    },
    "baby": {
        "en": (
            "baby food", "baby lotion", "baby shampoo", "baby wipe", "diaper",
            "formula", "pacifier", "teether", "wipes",
        ),
        "pt": (
            "chupeta", "fralda", "fralda descartável", "fórmula infantil",
            "leite em pó infantil", "lenço umedecido", "mamadeira", "papinha",
            "pomada para assadura", "sabonete infantil", "talco infantil",
        ),
    },
    "pet": {
        "en": (
            "cat food", "cat litter", "catnip", "dog food", "dog treat",
            "kibble", "leash", "pet food",
        ),
        "pt": (
            "antipulgas", "areia para gato", "areia sanitária", "coleira",
            "petisco", "ração", "ração de cachorro", "ração de gato",
            "tapete higiênico",
        ),
    },
    "other": {"en": (), "pt": ()},
}


# --------------------------------------------------------------------------
# Normalization. Mirrors grocery.clean_text/normalized so that keys line up,
# then folds accents and punctuation for matching.
# --------------------------------------------------------------------------

_BULLET_RE = re.compile(r"^[\s\-–—*•]+")
_SPACE_RE = re.compile(r"\s+")
_NONWORD_RE = re.compile(r"[^0-9a-z]+")
_QUANTITY_RE = re.compile(r"^\d+(kg|kgs|g|gr|mg|ml|l|lt|un|und|oz|lb|lbs|cx|pct)?$")

# Measurement noise that carries no product meaning.
_UNITS = frozenset(
    "kg kgs g gr grs mg ml l lt lts un und unid unids pc pcs pct cx dz oz lb lbs x".split()
)

# Dropped when building the alias form of a multi-word term, so that
# "leite de coco" also answers to "leite coco".
_CONNECTORS = frozenset(
    "de da do das dos d e em no na nos nas com sem ao aos a o os as um uma "
    "para pra of the and in for with to".split()
)

# Function words that only appear in Portuguese phrasing. Their presence means
# the head noun comes first ("peito de frango"); English puts it last
# ("chicken breast").
_PT_MARKERS = frozenset(
    "de da do das dos com sem ao aos em no na nos nas para pra".split()
)

# A frozen product lives in the frozen aisle whatever it is made of, so this
# marker overrides everything else.
_FROZEN_MARKERS = frozenset(
    "frozen congelado congelada congelados congeladas".split()
)

_MAX_NGRAM = 4


def fold(text: str) -> str:
    """Normalize for matching: NFKC, casefold, strip accents and punctuation.

    The first three steps mirror ``grocery.clean_text``/``grocery.normalized``
    so a name keyed there folds to the same stem here.
    """
    text = unicodedata.normalize("NFKC", str(text))
    text = _BULLET_RE.sub("", text)
    text = _SPACE_RE.sub(" ", text).strip(" ,.;").casefold()
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return _NONWORD_RE.sub(" ", text).strip()


def _tokens(name: str) -> list[str]:
    """Fold to whole tokens, dropping quantities and units."""
    return [
        token
        for token in fold(name).split()
        if token not in _UNITS and not _QUANTITY_RE.match(token)
    ]


def _variants(token: str) -> tuple[str, ...]:
    """The token plus plausible singular forms, English and Portuguese.

    Candidates are generated rather than picked, because the same ending is a
    different plural in each language ("limões"/"limao" vs "tomatoes"/"tomato").
    The index decides which candidate is real, so an over-generated form that
    matches nothing is harmless.
    """
    out = [token]
    size = len(token)
    if size >= 4:
        if token.endswith("oes"):          # limões -> limao ; tomatoes -> tomato
            out.append(token[:-3] + "ao")
        elif token.endswith("aes"):        # pães -> pao
            out.append(token[:-3] + "ao")
        elif token.endswith("ies"):        # berries -> berry
            out.append(token[:-3] + "y")
        elif token[-3:] in ("ais", "eis", "ois", "uis"):   # papéis -> papel
            out.append(token[:-2] + "l")
        if token.endswith("es"):           # peixes -> peixe (via -s) ; tomatoes -> tomato
            out.append(token[:-2])
        if token.endswith("ns"):           # bombons -> bombom
            out.append(token[:-2] + "m")
    out = [form for form in out if form]
    if size >= 4 and token.endswith("s"):  # bananas -> banana, ovos -> ovo
        out.append(token[:-1])
    # Preserve order (raw first) while dropping duplicates.
    return tuple(dict.fromkeys(out))


def _build_index() -> dict[str, tuple[str, str]]:
    """term -> (section, lang). ``lang`` is "xx" when both languages share it."""
    index: dict[str, tuple[str, str]] = {}
    for section, groups in KEYWORDS.items():
        for lang, terms in groups.items():
            for term in terms:
                key = fold(term)
                if not key:
                    continue
                alias = " ".join(w for w in key.split() if w not in _CONNECTORS)
                for form in (key, alias):
                    if not form:
                        continue
                    previous = index.get(form)
                    if previous is None:
                        index[form] = (section, lang)
                    elif previous[0] != section:
                        raise ValueError(
                            f"term {form!r} is claimed by both "
                            f"{previous[0]!r} and {section!r}"
                        )
                    elif previous[1] != lang:
                        index[form] = (section, "xx")
    return index


_INDEX: dict[str, tuple[str, str]] = _build_index()


def term_count() -> int:
    """How many distinct normalized terms (including aliases) are indexed."""
    return len(_INDEX)


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


def _head_is_first(tokens: list[str], matches: list[tuple[int, str, str]]) -> bool:
    """True when the name reads Portuguese, where the head noun comes first."""
    if any(token in _PT_MARKERS for token in tokens):
        return True
    pt = sum(1 for _, _, lang in matches if lang == "pt")
    en = sum(1 for _, _, lang in matches if lang == "en")
    return pt > en


def classify(name: str) -> str | None:
    """Return the section key for a product name, or None if unknown.

    Never guesses: an unrecognized product returns None so the caller can
    classify it once and persist the answer.
    """
    if not name:
        return None
    tokens = _tokens(name)
    if not tokens:
        return None

    if any(token in _FROZEN_MARKERS for token in tokens):
        return "frozen"

    variants = [_variants(token) for token in tokens]
    count = len(tokens)

    # Longest listed phrase wins: "leite de coco" is pantry, not dairy.
    for size in range(min(count, _MAX_NGRAM), 1, -1):
        for start in range(count - size + 1):
            for combo in product(*variants[start : start + size]):
                hit = _INDEX.get(" ".join(combo))
                if hit is not None:
                    return hit[0]

    matches: list[tuple[int, str, str]] = []
    for position, forms in enumerate(variants):
        for form in forms:
            hit = _INDEX.get(form)
            if hit is not None:
                matches.append((position, hit[0], hit[1]))
                break

    if not matches:
        return None
    if len({section for _, section, _ in matches}) == 1:
        return matches[0][1]
    return matches[0][1] if _head_is_first(tokens, matches) else matches[-1][1]


# --------------------------------------------------------------------------
# Labels
# --------------------------------------------------------------------------

_LANG_ALIASES = {
    "en": "en", "eng": "en", "english": "en", "ingles": "en",
    "pt": "pt", "por": "pt", "portuguese": "pt", "portugues": "pt", "br": "pt",
}


def _lang_code(lang: str) -> str:
    code = fold(lang).replace(" ", "-").split("-")[0]
    try:
        return _LANG_ALIASES[code]
    except KeyError:
        raise ValueError(f"unsupported language: {lang!r}; use 'en' or 'pt'") from None


def label(section: str, lang: str = "en") -> str:
    """Display label for a section key in "en" or "pt" (pt-BR accepted)."""
    key = str(section).strip().casefold()
    if key not in SECTIONS:
        raise KeyError(f"unknown section: {section!r}")
    return SECTIONS[key][_lang_code(lang)]


def section_list(lang: str = "en") -> list[tuple[str, str]]:
    """(key, label) pairs in store-walk order, for grouping a rendered list."""
    code = _lang_code(lang)
    return [(key, SECTIONS[key][code]) for key in SECTION_KEYS]


def main() -> None:
    import json
    import sys

    names = sys.argv[1:]
    if not names:
        print("usage: sections.py NAME [NAME ...]", file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps({name: classify(name) for name in names}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
