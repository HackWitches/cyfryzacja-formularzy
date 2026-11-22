"""Polish paper-form rules applied to the model's contract before the builder.

The model guesses; these rules fix what the paper states plainly (field type from the label or the character cells,
optional fields, "inne / jeżeli tak" conditions, the addressee). Ideas come from the form2json project; the code and
expressions are our own. Each rule is a plain function `rule(contract, ctx) -> notes`: it changes the normalized
contract in place and returns short notes for the debug output. A rule stays on RULES only if it improves the
comparison of saved model answers with the expected contracts of our nine forms (README, "Reguły PL").
"""
from app.rules import naglowek, porzadek, typy, widocznosc
from app.rules.text import Context

RULES = [  # order matters: clean titles first, types before visibility, visibility before required
    ("tytuly", porzadek.tytuly),
    ("pola_urzedu", porzadek.pola_urzedu),
    ("duplikaty", porzadek.duplikaty),
    ("oswiadczenia", porzadek.oswiadczenia),
    ("typ_z_etykiety", typy.typ_z_etykiety),
    ("typ_z_kratek", typy.typ_z_kratek),
    ("slownik_kraje", typy.slownik_kraje),
    ("wielokrotny_wybor", typy.wielokrotny_wybor),
    ("warunek_inne_tak", widocznosc.warunek_inne_tak),
    ("wymagane", widocznosc.wymagane),
    ("podmiot", naglowek.podmiot),
    ("adresat", naglowek.adresat),
]


def apply(contract: dict, ctx: Context, only=None) -> list[str]:
    """Run the rules (all, or the names in `only`) on a normalized contract; returns the notes."""
    notes = []
    for name, rule in RULES:
        if only is None or name in only:
            notes += [f"{name}: {n}" for n in rule(contract, ctx)]
    return notes


__all__ = ["Context", "RULES", "apply"]
