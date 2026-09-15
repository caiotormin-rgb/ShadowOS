#!/usr/bin/env python3
"""Bilingual (pt-BR + English) grocery product synonym map.

Standalone and dependency free: Python standard library only, no model, no
data files, no network. A sibling of ``sections.py`` and built the same way --
a small curated map sized for one household's vocabulary, not a retail
catalogue (ROADMAP.md item 3, "Cross-language dedup is a genuine gap").

The problem it solves: item identity is ``(store, normalized_name, unit)`` and
``text.normalized`` only casefolds, so one person's "leite" and another's
"milk" become two rows on the same list. ``canonical`` gives both the same
dedup key.

Public surface
--------------
``GROUPS``            ``{canonical key: (term, ...)}`` -- the curated map, terms
                      written naturally (accents, spaces) and normalized at
                      import.
``CANONICAL_KEYS``    tuple of canonical keys, in ``GROUPS`` order.
``canonical(name)``   -> the dedup key. Always a string, never ``None``.
``same_product(a, b)``-> whether two names key to the same product.
``group_for(name)``   -> the group's spellings, or ``None`` when the name is in
                      no group. For telling a user *why* two entries merged.
``fold(text)``        -> the normalized matching form (identical to
                      ``sections.fold``; duplicated, not imported, so this
                      module stays standalone).
``group_count()`` / ``term_count()`` -> coverage, honestly countable.

Matching rule -- the whole name, or nothing
-------------------------------------------
This is the one place this module deliberately differs from ``sections.py``.
The classifier there walks n-grams and falls back to a head noun, because
guessing an aisle from part of a name is cheap and reversible. Here a wrong
answer *merges two different products into one row and destroys a list entry*,
so the entire name (after dropping quantity/unit noise) must match a listed
term. There is no head-noun fallback, by design:

    "leite de coco"  -> coconut milk   (its own group)
    "creme de leite" -> creme de leite (in no group; passes through unchanged)
    "leite"          -> milk

None of those three can reach another's key, because "leite" is never matched
inside a longer name. The cost is under-merging: "leite de vaca" and "cow's
milk" both pass through unchanged rather than merging. That is the intended
trade -- an un-merged pair is a visible duplicate the user can fix, a false
merge is silent data loss.

Matching is otherwise as forgiving as ``sections.py``: case, accents
("maca" == "maçã"), plurals in both languages ("bananas", "ovos", "tomatoes",
"limões"), quantity and unit noise ("2 kg de arroz"), and connector words, so
"leite de coco" and "leite coco" resolve alike.

Coverage and deliberate omissions
---------------------------------
About 280 groups covering the staples of a bilingual household list. Thin by
design: prepared foods, brands, cuts of beef beyond the common few, baking
specialties, and anything regional enough to be one family's word for it.
Unknown names pass through, so a gap costs nothing but a missed merge.

Pairs that differ only by accent or spacing ("orégano"/"oregano",
"tilápia"/"tilapia") need no group -- ``fold`` already unifies them.

Terms left out because merging them would be a *guess*, and a guess here is
destructive:

- bare "salsa"     -- parsley in pt, sauce in en.
- bare "detergent" -- laundry in en, dish soap ("detergente") in pt-BR. Only
                      the unambiguous "dish soap" spellings are grouped.
- bare "soap"      -- could be bar soap, dish soap, or laundry. Only "sabonete"
                      / "bar soap" are grouped.
- bare "fermento"  -- yeast or baking powder depending on the household; only
                      the qualified forms are grouped.
- bare "vitamina"  -- vitamin supplement, or a fruit smoothie.
- bare "pimenta" / "pepper" -- chili, black pepper, or bell pepper. Only
                      "pimenta do reino"/"black pepper" and
                      "pimentão"/"bell pepper" are grouped.
- bare "creme", "massa", "farofa", "torta", "caldo" -- ambiguous heads.
- "carne" is grouped as "meat" and "carne bovina" as "beef", kept apart on
  purpose: a household that writes both probably means different things.
- "limão" is grouped with "lime", not "lemon"; "limão siciliano" is the lemon.
- "kale" is not merged into "couve" (collard greens).
"""

from __future__ import annotations

import re
import unicodedata
from itertools import product

__all__ = [
    "GROUPS",
    "CANONICAL_KEYS",
    "canonical",
    "same_product",
    "group_for",
    "fold",
    "group_count",
    "term_count",
]


# --------------------------------------------------------------------------
# The curated map: canonical key -> the spellings that mean that product.
#
# The key is the folded English term where one exists, else the folded pt-BR
# term. It is a stable dedup key, not a display string -- render the item name
# the person actually wrote (ROADMAP item 3: "keep stored item names as the
# person wrote them").
#
# Every key must itself be one of the group's folded terms; enforced at import.
# Multi-word terms are indexed with and without their connectors, so
# "leite de coco" and "leite coco" both resolve.
# --------------------------------------------------------------------------

GROUPS: dict[str, tuple[str, ...]] = {
    # ---- dairy and eggs --------------------------------------------------
    "milk": ("leite", "milk"),
    "whole milk": ("leite integral", "whole milk"),
    "skim milk": ("leite desnatado", "skim milk", "nonfat milk", "fat free milk"),
    "semi skimmed milk": ("leite semidesnatado", "semi skimmed milk"),
    "condensed milk": ("leite condensado", "condensed milk", "sweetened condensed milk"),
    "powdered milk": ("leite em pó", "powdered milk", "milk powder", "dry milk"),
    "coconut milk": ("leite de coco", "coconut milk"),
    "butter": ("manteiga", "butter"),
    "margarine": ("margarina", "margarine"),
    "cheese": ("queijo", "cheese"),
    "mozzarella": ("muçarela", "mussarela", "queijo mussarela", "mozzarella", "mozzarella cheese"),
    "parmesan": ("parmesão", "queijo parmesão", "parmesan", "parmesan cheese"),
    "grated cheese": ("queijo ralado", "grated cheese", "shredded cheese"),
    "ricotta": ("ricota", "ricotta"),
    "yogurt": ("iogurte", "yogurt", "yoghurt", "yogurte"),
    "greek yogurt": ("iogurte grego", "greek yogurt"),
    "egg": ("ovo", "ovos", "egg", "eggs"),
    # ---- bakery ----------------------------------------------------------
    "bread": ("pão", "bread"),
    "sourdough bread": (
        "pão sourdough", "pão de fermentação natural", "sourdough bread", "sourdough",
    ),
    "sliced bread": ("pão de forma", "pão de fôrma", "sliced bread", "sandwich bread"),
    "whole wheat bread": ("pão integral", "whole wheat bread", "wholemeal bread"),
    "pao frances": ("pão francês", "pãozinho", "pão de sal"),
    "cake": ("bolo", "cake"),
    "cookie": ("biscoito", "bolacha", "cookie", "cookies", "biscuit"),
    "toast": ("torrada", "toast", "melba toast"),
    "doughnut": ("rosquinha", "sonho", "doughnut", "donut"),
    # ---- butcher ---------------------------------------------------------
    "chicken": ("frango", "galinha", "chicken"),
    "chicken breast": ("peito de frango", "filé de frango", "chicken breast"),
    "chicken thigh": ("sobrecoxa", "chicken thigh", "chicken thighs"),
    "chicken drumstick": ("coxa de frango", "chicken drumstick", "drumstick", "drumsticks"),
    "chicken wing": ("asa de frango", "asinha de frango", "chicken wing", "chicken wings"),
    "meat": ("carne", "meat"),
    "beef": ("carne bovina", "beef"),
    "ground beef": ("carne moída", "ground beef", "minced beef"),
    "pork": ("carne de porco", "carne suína", "pork"),
    "sausage": ("linguiça", "linguica", "sausage"),
    "wiener": ("salsicha", "wiener", "frankfurter", "hot dog sausage"),
    "ham": ("presunto", "ham"),
    "bologna": ("mortadela", "bologna", "baloney"),
    "salami": ("salame", "salami"),
    "turkey": ("peru", "turkey"),
    "steak": ("bife", "steak"),
    "ribs": ("costela", "costelinha", "ribs", "rib"),
    "liver": ("fígado", "liver"),
    "lamb": ("cordeiro", "lamb"),
    "meatball": ("almôndega", "meatball", "meatballs"),
    "burger patty": ("hambúrguer", "hamburguer", "burger patty", "beef patty"),
    # ---- seafood ---------------------------------------------------------
    "fish": ("peixe", "fish"),
    "shrimp": ("camarão", "shrimp", "prawn", "prawns"),
    "tuna": ("atum", "tuna"),
    "salmon": ("salmão", "salmon"),
    "sardine": ("sardinha", "sardine", "sardines"),
    "cod": ("bacalhau", "cod", "salt cod"),
    "squid": ("lula", "squid", "calamari"),
    "octopus": ("polvo", "octopus"),
    # ---- produce ---------------------------------------------------------
    "apple": ("maçã", "apple"),
    "banana": ("banana", "bananas"),
    "orange": ("laranja", "orange"),
    "lime": ("limão", "lima", "lime"),
    "lemon": ("limão siciliano", "lemon"),
    "tangerine": ("bergamota", "mexerica", "tangerina", "poncã", "ponkan",
                  "tangerine", "mandarin", "mandarin orange"),
    "grape": ("uva", "grape", "grapes"),
    "strawberry": ("morango", "strawberry", "strawberries"),
    "blueberry": ("mirtilo", "blueberry", "blueberries"),
    "raspberry": ("framboesa", "raspberry", "raspberries"),
    "blackberry": ("amora", "blackberry", "blackberries"),
    "cherry": ("cereja", "cherry", "cherries"),
    "watermelon": ("melancia", "watermelon"),
    "melon": ("melão", "melon"),
    "mango": ("manga", "mango"),
    "papaya": ("mamão", "papaya"),
    "guava": ("goiaba", "guava"),
    "passion fruit": ("maracujá", "passion fruit"),
    "pineapple": ("abacaxi", "ananás", "pineapple"),
    "avocado": ("abacate", "avocado"),
    "coconut": ("coco", "coconut"),
    "pear": ("pera", "pear"),
    "peach": ("pêssego", "peach", "peaches"),
    "plum": ("ameixa", "plum", "plums"),
    "fig": ("figo", "fig", "figs"),
    "kiwi": ("kiwi", "quiuí", "kiwifruit"),
    "potato": ("batata", "batata inglesa", "potato", "potatoes"),
    "sweet potato": ("batata doce", "sweet potato", "sweet potatoes"),
    "cassava": ("mandioca", "aipim", "macaxeira", "cassava", "yuca", "manioc"),
    "onion": ("cebola", "onion", "onions"),
    "garlic": ("alho", "garlic"),
    "tomato": ("tomate", "tomato", "tomatoes"),
    "cherry tomato": ("tomate cereja", "cherry tomato", "cherry tomatoes"),
    "carrot": ("cenoura", "carrot", "carrots"),
    "lettuce": ("alface", "lettuce"),
    "cabbage": ("repolho", "cabbage"),
    "collard greens": ("couve", "couve manteiga", "collard greens", "collards"),
    "broccoli": ("brócolis", "brocolis", "broccoli"),
    "cauliflower": ("couve flor", "couve-flor", "cauliflower"),
    "spinach": ("espinafre", "spinach"),
    "arugula": ("rúcula", "arugula", "rocket"),
    "watercress": ("agrião", "watercress"),
    "chard": ("acelga", "chard", "swiss chard"),
    "cucumber": ("pepino", "cucumber", "cucumbers"),
    "zucchini": ("abobrinha", "zucchini", "courgette"),
    "pumpkin": ("abóbora", "jerimum", "pumpkin", "winter squash"),
    "chayote": ("chuchu", "chayote"),
    "eggplant": ("berinjela", "eggplant", "aubergine"),
    "bell pepper": ("pimentão", "bell pepper", "capsicum", "sweet pepper"),
    "beet": ("beterraba", "beet", "beetroot", "beets"),
    "corn": ("milho", "corn", "maize", "sweet corn", "milho verde"),
    "green beans": ("vagem", "green beans", "string beans", "green bean"),
    "peas": ("ervilha", "peas", "green peas", "pea"),
    "mushroom": ("cogumelo", "mushroom", "mushrooms"),
    "ginger": ("gengibre", "ginger"),
    "parsley": ("salsinha", "parsley"),
    "scallion": ("cebolinha", "scallion", "scallions", "green onion", "spring onion"),
    "cilantro": ("coentro", "cilantro", "coriander"),
    "basil": ("manjericão", "basil"),
    "mint": ("hortelã", "mint"),
    "rosemary": ("alecrim", "rosemary"),
    "leek": ("alho poró", "alho-poró", "leek", "leeks"),
    "celery": ("aipo", "salsão", "celery"),
    "yam": ("inhame", "yam", "yams"),
    "turnip": ("nabo", "turnip"),
    "radish": ("rabanete", "radish", "radishes"),
    "okra": ("quiabo", "okra"),
    "cassava flour": ("farinha de mandioca", "cassava flour", "manioc flour"),
    # ---- pantry ----------------------------------------------------------
    "rice": ("arroz", "rice"),
    "brown rice": ("arroz integral", "brown rice"),
    "beans": ("feijão", "beans", "bean"),
    "black beans": ("feijão preto", "black beans"),
    "chickpeas": ("grão de bico", "chickpeas", "chickpea", "garbanzo beans"),
    "lentils": ("lentilha", "lentils", "lentil"),
    "sugar": ("açúcar", "sugar"),
    "brown sugar": ("açúcar mascavo", "brown sugar"),
    "sweetener": ("adoçante", "sweetener"),
    "salt": ("sal", "salt"),
    "flour": ("farinha", "flour"),
    "wheat flour": ("farinha de trigo", "wheat flour", "all purpose flour", "plain flour"),
    "breadcrumbs": ("farinha de rosca", "breadcrumbs", "bread crumbs"),
    "cornstarch": ("amido de milho", "maisena", "cornstarch", "corn starch"),
    "cornmeal": ("fubá", "cornmeal", "corn meal"),
    "tapioca starch": ("polvilho", "goma de tapioca", "tapioca starch", "tapioca flour"),
    "oil": ("óleo", "oil", "cooking oil", "vegetable oil"),
    "olive oil": ("azeite", "azeite de oliva", "olive oil"),
    "vinegar": ("vinagre", "vinegar"),
    "coffee": ("café", "coffee"),
    "ground coffee": ("café moído", "ground coffee"),
    "tea": ("chá", "tea"),
    "honey": ("mel", "honey"),
    "jam": ("geleia", "geléia", "jam", "jelly", "fruit preserves"),
    "peanut butter": ("pasta de amendoim", "manteiga de amendoim", "peanut butter"),
    "oats": ("aveia", "oats", "oatmeal", "rolled oats"),
    "granola": ("granola", "muesli"),
    # Bare "pasta" is deliberately absent. In pt-BR it usually means a folder,
    # or elliptically toothpaste, and this household writes Portuguese. An
    # over-merge silently replaces someone's item; an under-merge just leaves
    # two rows, so the failure directions are not comparable.
    "noodles": ("macarrão", "noodles"),
    "spaghetti": ("espaguete", "spaghetti"),
    "lasagna": ("lasanha", "lasagna", "lasagne"),
    "instant noodles": ("miojo", "macarrão instantâneo", "instant noodles", "ramen"),
    "tomato sauce": ("molho de tomate", "tomato sauce"),
    "tomato paste": ("extrato de tomate", "massa de tomate", "tomato paste"),
    "mayonnaise": ("maionese", "mayonnaise", "mayo"),
    "mustard": ("mostarda", "mustard"),
    "soy sauce": ("shoyu", "molho de soja", "soy sauce"),
    "black pepper": ("pimenta do reino", "black pepper", "ground black pepper"),
    "cinnamon": ("canela", "cinnamon"),
    "bay leaf": ("louro", "folha de louro", "bay leaf", "bay leaves"),
    "cumin": ("cominho", "cumin"),
    "paprika": ("colorau", "colorífico", "paprika"),
    "seasoning": ("tempero", "seasoning", "seasoning mix"),
    "yeast": ("fermento biológico", "yeast", "active dry yeast"),
    "baking powder": ("fermento em pó", "fermento químico", "baking powder"),
    "baking soda": ("bicarbonato", "bicarbonato de sódio", "baking soda"),
    "gelatin": ("gelatina", "gelatin"),
    "cornflakes": ("sucrilhos", "corn flakes", "cornflakes"),
    "cocoa powder": ("cacau em pó", "cocoa powder", "unsweetened cocoa"),
    "soup": ("sopa", "soup"),
    "chicken broth": ("caldo de galinha", "chicken broth", "chicken stock", "chicken bouillon"),
    "olive": ("azeitona", "olive", "olives"),
    "raisin": ("uva passa", "passas", "raisin", "raisins"),
    "vanilla": ("baunilha", "vanilla", "vanilla extract"),
    "shredded coconut": ("coco ralado", "shredded coconut", "desiccated coconut"),
    "couscous": ("cuscuz", "couscous"),
    "pickles": ("picles", "pickles", "pickle"),
    "corn kernels": ("milho em conserva", "canned corn", "corn kernels"),
    "canned tuna": ("atum em lata", "atum enlatado", "canned tuna", "tinned tuna"),
    # ---- snacks and sweets ----------------------------------------------
    "candy": ("bala", "candy", "sweets"),
    "chewing gum": ("chiclete", "goma de mascar", "chewing gum", "gum"),
    "peanut": ("amendoim", "peanut", "peanuts"),
    "cashew": ("castanha de caju", "cashew", "cashews", "cashew nuts"),
    "walnut": ("nozes", "noz", "walnut", "walnuts"),
    "almond": ("amêndoa", "almond", "almonds"),
    "brazil nut": ("castanha do pará", "brazil nut", "brazil nuts"),
    "potato chips": ("batata chips", "potato chips", "crisps"),
    "popcorn": ("pipoca", "popcorn"),
    "popcorn kernels": ("milho de pipoca", "popcorn kernels"),
    "ice cream": ("sorvete", "ice cream"),
    "popsicle": ("picolé", "popsicle", "ice pop", "ice lolly"),
    "french fries": ("batata frita", "french fries", "fries"),
    "hazelnut spread": ("creme de avelã", "hazelnut spread", "chocolate hazelnut spread"),
    "cereal bar": ("barra de cereal", "cereal bar", "granola bar"),
    # ---- beverages -------------------------------------------------------
    "water": ("água", "water"),
    "sparkling water": ("água com gás", "sparkling water", "carbonated water", "fizzy water"),
    "mineral water": ("água mineral", "mineral water"),
    "coconut water": ("água de coco", "coconut water"),
    "juice": ("suco", "juice"),
    "orange juice": ("suco de laranja", "orange juice"),
    "soda": ("refrigerante", "soda", "soft drink", "pop"),
    "beer": ("cerveja", "beer"),
    "wine": ("vinho", "wine"),
    "sparkling wine": ("espumante", "sparkling wine"),
    "whiskey": ("uísque", "whisky", "whiskey"),
    "energy drink": ("energético", "energy drink"),
    "sports drink": ("isotônico", "sports drink"),
    "iced tea": ("chá gelado", "iced tea"),
    "lemonade": ("limonada", "lemonade"),
    "ice": ("gelo", "ice", "ice cubes"),
    # ---- household -------------------------------------------------------
    "dish soap": ("detergente", "dish soap", "dishwashing liquid", "washing up liquid"),
    "laundry detergent": ("sabão em pó", "laundry detergent", "washing powder", "laundry soap"),
    "liquid laundry detergent": ("sabão líquido", "liquid laundry detergent"),
    "fabric softener": ("amaciante", "fabric softener", "fabric conditioner"),
    "bleach": ("água sanitária", "cloro", "bleach"),
    "disinfectant": ("desinfetante", "disinfectant"),
    "all purpose cleaner": ("multiuso", "limpador multiuso", "all purpose cleaner",
                            "multipurpose cleaner"),
    "glass cleaner": ("limpa vidros", "limpa-vidros", "glass cleaner", "window cleaner"),
    "toilet paper": ("papel higiênico", "toilet paper", "bathroom tissue"),
    "paper towel": ("papel toalha", "paper towel", "paper towels", "kitchen roll"),
    "napkin": ("guardanapo", "napkin", "napkins", "serviette"),
    "aluminum foil": ("papel alumínio", "aluminum foil", "aluminium foil", "tin foil"),
    "plastic wrap": ("filme plástico", "papel filme", "plastic wrap", "cling film", "cling wrap"),
    "parchment paper": ("papel manteiga", "parchment paper", "baking paper"),
    "trash bag": ("saco de lixo", "trash bag", "trash bags", "garbage bag", "bin bag", "bin liner"),
    "sponge": ("esponja", "sponge", "sponges"),
    "steel wool": ("palha de aço", "steel wool", "scouring pad"),
    "dish cloth": ("pano de prato", "dish cloth", "dish towel", "tea towel"),
    "broom": ("vassoura", "broom"),
    "mop": ("esfregão", "mop"),
    "squeegee": ("rodo", "squeegee", "floor squeegee"),
    "insecticide": ("inseticida", "insecticide", "bug spray"),
    "light bulb": ("lâmpada", "light bulb", "lightbulb", "light bulbs"),
    "battery": ("pilha", "pilhas", "battery", "batteries"),
    "matches": ("fósforo", "fósforos", "matches", "matchbox"),
    "lighter": ("isqueiro", "lighter"),
    "candle": ("vela", "candle", "candles"),
    "charcoal": ("carvão", "charcoal"),
    "rubber gloves": ("luva de borracha", "luvas de borracha", "rubber gloves"),
    "air freshener": ("aromatizante", "odorizador", "air freshener"),
    "disposable cup": ("copo descartável", "copos descartáveis", "disposable cup", "plastic cup"),
    "paper plate": ("prato descartável", "prato de papel", "paper plate", "paper plates"),
    # ---- personal care ---------------------------------------------------
    "bar soap": ("sabonete", "bar soap", "body soap"),
    "body wash": ("sabonete líquido", "body wash", "shower gel"),
    "shampoo": ("xampu", "shampoo"),
    "conditioner": ("condicionador", "conditioner", "hair conditioner"),
    "toothpaste": ("pasta de dente", "creme dental", "toothpaste"),
    "toothbrush": ("escova de dente", "toothbrush"),
    "dental floss": ("fio dental", "dental floss", "floss"),
    "mouthwash": ("enxaguante bucal", "antisséptico bucal", "mouthwash"),
    "deodorant": ("desodorante", "deodorant"),
    "razor": ("aparelho de barbear", "lâmina de barbear", "gilete", "razor", "razor blades"),
    "shaving cream": ("creme de barbear", "espuma de barbear", "shaving cream", "shaving foam"),
    "sunscreen": ("protetor solar", "filtro solar", "sunscreen", "sunblock"),
    "moisturizer": ("creme hidratante", "hidratante", "moisturizer", "body lotion"),
    "cotton swab": ("cotonete", "cotton swab", "cotton swabs", "cotton bud", "q tip"),
    "cotton balls": ("algodão", "cotton balls", "cotton wool"),
    "sanitary pad": ("absorvente", "sanitary pad", "sanitary pads", "sanitary napkin"),
    "tampon": ("absorvente interno", "tampon", "tampons"),
    "facial tissue": ("lenço de papel", "lenços de papel", "facial tissue", "tissues"),
    "hand sanitizer": ("álcool em gel", "hand sanitizer", "hand gel"),
    "comb": ("pente", "comb"),
    "hairbrush": ("escova de cabelo", "hairbrush", "hair brush"),
    "nail clipper": ("cortador de unha", "nail clipper", "nail clippers"),
    "nail polish": ("esmalte", "esmalte de unha", "nail polish"),
    "bandage": ("curativo", "band aid", "bandage", "adhesive bandage"),
    "acetaminophen": ("paracetamol", "acetaminophen"),
    "ibuprofen": ("ibuprofeno", "ibuprofen"),
    "perfume": ("perfume", "fragrance", "eau de toilette"),
    "talcum powder": ("talco", "talcum powder", "baby powder"),
    # ---- baby ------------------------------------------------------------
    "diaper": ("fralda", "fralda descartável", "fraldas", "diaper", "diapers", "nappy", "nappies"),
    "baby wipes": ("lenço umedecido", "lenços umedecidos", "baby wipes", "wet wipes"),
    "pacifier": ("chupeta", "pacifier", "dummy"),
    "baby formula": ("fórmula infantil", "baby formula", "infant formula"),
    "baby food": ("papinha", "baby food"),
    "baby bottle": ("mamadeira", "baby bottle", "feeding bottle"),
    "diaper cream": ("pomada para assadura", "diaper cream", "diaper rash cream"),
    # ---- pet -------------------------------------------------------------
    "pet food": ("ração", "racao", "pet food"),
    "dog food": ("ração de cachorro", "ração para cachorro", "dog food"),
    "cat food": ("ração de gato", "ração para gato", "cat food"),
    "cat litter": ("areia para gato", "areia sanitária", "cat litter", "kitty litter"),
    "pee pad": ("tapete higiênico", "pee pad", "puppy pad", "puppy pads"),
    "flea treatment": ("antipulgas", "flea treatment", "flea drops"),
}

CANONICAL_KEYS: tuple[str, ...] = tuple(GROUPS)


# --------------------------------------------------------------------------
# Normalization. Byte-for-byte the same rules as sections.fold, which in turn
# mirrors text.clean_text/text.normalized, so keys line up across the three
# modules. Duplicated rather than imported to keep this module standalone.
# --------------------------------------------------------------------------

_BULLET_RE = re.compile(r"^[\s\-–—*•]+")
_SPACE_RE = re.compile(r"\s+")
_NONWORD_RE = re.compile(r"[^0-9a-z]+")
_QUANTITY_RE = re.compile(r"^\d+(kg|kgs|g|gr|mg|ml|l|lt|un|und|oz|lb|lbs|cx|pct)?$")

# Measurement noise that carries no product meaning. Same list as sections.py
# plus a few spelled-out measures. Container words (caixa, lata, pacote, saco,
# garrafa) are deliberately NOT here -- "saco de lixo" is a product.
_UNITS = frozenset(
    "kg kgs g gr grs mg ml l lt lts un und unid unids pc pcs pct cx dz oz lb lbs x "
    "litro litros liter liters litre litres gallon gallons gal duzia duzias dozen".split()
)

# Dropped when building the alias form of a term and of the name being looked
# up, so "leite de coco" and "leite coco" resolve alike and "2 kg de arroz"
# reaches "arroz".
_CONNECTORS = frozenset(
    "de da do das dos d e em no na nos nas com sem ao aos a o os as um uma "
    "para pra of the and in for with to".split()
)

# A name with more parts than this, or more spelling combinations than
# _MAX_COMBOS, is only tried in its raw and connector-stripped forms. Both
# limits are far above a real grocery name; they exist so a pasted paragraph
# cannot blow up the search.
_MAX_TOKENS = 8
_MAX_COMBOS = 512


def fold(text: str) -> str:
    """Normalize for matching: NFKC, casefold, strip accents and punctuation.

    Identical to ``sections.fold``; the first steps mirror ``text.clean_text``
    and ``text.normalized`` so a name keyed there folds to the same stem here.
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


def _strip_connectors(tokens: tuple[str, ...] | list[str]) -> str:
    """Drop connector words, except a trailing one, which is a real word.

    The exception matters: "castanha do pará" folds to "castanha do para", and
    "para" is also the preposition, so stripping blindly would leave bare
    "castanha" -- a Brazil nut group that swallows every other nut. A connector
    in final position is kept as part of the name.
    """
    tokens = list(tokens)
    last = len(tokens) - 1
    return " ".join(
        token
        for position, token in enumerate(tokens)
        if position == last or token not in _CONNECTORS
    )


def _variants(token: str) -> tuple[str, ...]:
    """The token plus plausible singular forms, English and Portuguese.

    Same generate-then-check approach as ``sections._variants``: the same
    ending is a different plural in each language ("limões"/"limao" vs
    "tomatoes"/"tomato"), so candidates are over-generated and the index
    decides which one is real. A candidate that matches nothing is harmless.
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
        if token.endswith("es"):           # peixes -> peixe ; tomatoes -> tomato
            out.append(token[:-2])
        if token.endswith("ns"):           # bombons -> bombom
            out.append(token[:-2] + "m")
    out = [form for form in out if form]
    if size >= 4 and token.endswith("s"):  # bananas -> banana, ovos -> ovo
        out.append(token[:-1])
    return tuple(dict.fromkeys(out))       # raw form first, no duplicates


def _build_index() -> dict[str, str]:
    """folded term (and connector-stripped alias) -> canonical key.

    Raises at import if the curation is inconsistent: a key that is not its own
    folded form, a key absent from its group, or a term claimed by two groups.
    """
    index: dict[str, str] = {}
    for key, terms in GROUPS.items():
        if key != fold(key):
            raise ValueError(f"canonical key {key!r} is not in folded form")
        if len(terms) < 2:
            raise ValueError(f"group {key!r} has nothing to merge")
        forms: set[str] = set()
        for term in terms:
            folded = fold(term)
            if not folded:
                raise ValueError(f"group {key!r} has an empty term")
            forms.add(folded)
            alias = _strip_connectors(folded.split())
            if alias:
                forms.add(alias)
        if key not in forms:
            raise ValueError(f"canonical key {key!r} is not one of its own terms")
        for form in forms:
            previous = index.get(form)
            if previous is not None and previous != key:
                raise ValueError(
                    f"term {form!r} is claimed by both {previous!r} and {key!r}"
                )
            index[form] = key
    return index


_INDEX: dict[str, str] = _build_index()


def group_count() -> int:
    """How many synonym groups the map defines."""
    return len(GROUPS)


def term_count() -> int:
    """How many distinct normalized spellings (including aliases) are indexed."""
    return len(_INDEX)


# --------------------------------------------------------------------------
# Lookup
# --------------------------------------------------------------------------


def _match(name: str) -> str | None:
    """Canonical key for a name, or None when the whole name is not a term.

    Whole-name matching only. A listed term is never matched inside a longer
    name, which is what keeps "leite de coco" out of the "milk" group.
    """
    tokens = _tokens(name)
    if not tokens:
        return None

    raw = " ".join(tokens)
    hit = _INDEX.get(raw)
    if hit is not None:
        return hit
    stripped = _strip_connectors(tokens)
    if stripped and stripped != raw:
        hit = _INDEX.get(stripped)
        if hit is not None:
            return hit

    if len(tokens) > _MAX_TOKENS:
        return None
    variants = [_variants(token) for token in tokens]
    combos = 1
    for forms in variants:
        combos *= len(forms)
    if combos > _MAX_COMBOS:
        return None

    for combo in product(*variants):
        candidate = " ".join(combo)
        hit = _INDEX.get(candidate)
        if hit is not None:
            return hit
        alias = _strip_connectors(combo)
        if alias and alias != candidate:
            hit = _INDEX.get(alias)
            if hit is not None:
                return hit
    return None


def canonical(name: str) -> str:
    """The dedup key for a product name.

    Returns the group's canonical key when the whole name is a listed spelling,
    otherwise the folded name unchanged. Never ``None`` and never empty for a
    name with any content, because callers use the result as an identity key:

        canonical("Leite") == canonical("milk") == "milk"
        canonical("leite de coco") == canonical("coconut milk") == "coconut milk"
        canonical("goiabada cascão") == "goiabada cascao"   # unknown, passes through

    Idempotent: ``canonical(canonical(x)) == canonical(x)``.
    """
    key = _match(name)
    if key is not None:
        return key
    folded = fold(name)
    if folded:
        return folded
    # Nothing survived folding (a name of only punctuation or emoji). Fall back
    # to whitespace-collapsed casefolded text rather than "" so the caller
    # still gets a distinguishing key.
    return _SPACE_RE.sub(" ", str(name)).strip().casefold()


def same_product(a: str, b: str) -> bool:
    """Whether two names denote the same product. Empty names match nothing."""
    key = canonical(a)
    return bool(key) and key == canonical(b)


def group_for(name: str) -> tuple[str, ...] | None:
    """The spellings that merge with this name, or None if it is in no group.

    Returns the group's curated terms as written (accents and all), including
    the name's own spelling, so a caller can say *why* two entries merged:
    "leite and milk are the same product".
    """
    key = _match(name)
    if key is None:
        return None
    return GROUPS[key]


def main() -> None:
    import json
    import sys

    names = sys.argv[1:]
    if not names:
        print("usage: synonyms.py NAME [NAME ...]", file=sys.stderr)
        raise SystemExit(2)
    print(
        json.dumps(
            {
                name: {"canonical": canonical(name), "group": group_for(name)}
                for name in names
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
