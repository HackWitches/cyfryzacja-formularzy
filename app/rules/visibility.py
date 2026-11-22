"""When a field is shown and whether it is required."""

import re
from difflib import SequenceMatcher

from app.domain.contract import CHOICE_TYPES, leaves
from app.rules.text import OPTIONAL, Context, field_lines, norm, plain_fields, sections

PARENTHESIS = re.compile(r"\s*\([^()]*\)")
IF_YES = re.compile(r"^\W*(jezeli|jesli|w przypadku|gdy)\b.*\btak\b|^\W*(jezeli|jesli) (tak|zaznaczono)")
OTHER = re.compile(
    r"^\W*(inne|inny|inna|innego|innych)\b(?! uwag)|^\W*(jakie|jaki|jaka)\b.*\binn"
    r"|^\W*(prosze )?(podac|wskazac|wpisac) (jak|inn)"
)
OTHER_OPTION = re.compile(r"^(inne|inny|inna|innego|innych)\b")
TWIN_MIN_FIELDS, TWIN_SAME = 3, 0.8  # twin sections: at least 3 fields, 80% of the titles alike


def condition_other_or_yes(c: dict, ctx: Context) -> list[str]:
    """A field after a list that names one of its options shows only when that option is picked.
    Examples: 'Inne: ……', 'Jeżeli tak, podaj ……'. Fields that already have a condition stay as they are."""
    notes = []
    for sec in sections(c):
        prev = None
        for f in sec["pola"]:
            if f["typ"] in CHOICE_TYPES and f["opcje"]:
                prev = f
                continue
            if prev is None or f["wzorzec"] != "POLE":
                continue
            if f["warunek"]:
                if not any(leaf["pole"] == prev["techName"] for leaf in leaves(f["warunek"])):
                    prev = None
                continue
            label = norm(f"{f['tytul']} {f['opis'] or ''}").strip(" .:…")
            options = {norm(o).strip(" .:…"): o for o in prev["opcje"]}
            value = options.get(norm(f["tytul"]).strip(" .:…"))
            if value is None and IF_YES.search(label):
                value = options.get("tak")
            if value is None and OTHER.search(label):
                value = next((o for k, o in options.items() if OTHER_OPTION.match(k)), None)
            if value:
                f["warunek"] = {"pole": prev["techName"], "wartosc": value}
                notes.append(f"'{f['tytul']}' widoczne po wyborze '{value}' w '{prev['tytul']}'")
            else:
                prev = None  # only fields right after the list (or its other dependants) can belong to it
    return notes


def _twins(a: dict, b: dict) -> bool:
    """The same fields in the same order, mostly numbers: two variants of one calculation table."""
    fa, fb = a["pola"], b["pola"]
    if len(fa) != len(fb) or len(fa) < TWIN_MIN_FIELDS:
        return False
    same = sum(
        SequenceMatcher(None, norm(x["tytul"]), norm(y["tytul"])).ratio() >= TWIN_SAME
        for x, y in zip(fa, fb, strict=True)
    )
    return same >= TWIN_SAME * len(fa) and sum(f["typ"] == "NUMBER" for f in fa + fb) * 2 >= len(fa + fb)


def twin_sections(c: dict, ctx: Context) -> list[str]:
    """Two sections with the same number fields are alternatives; the twin without a condition gets the opposite one.
    Example: "obliczenie opłaty" and "obliczenie opłaty ze zwolnieniem".
    ponytail: number tables only; twin text sections (home and other address) are not alternatives."""
    notes, secs = [], list(sections(c))
    for a in secs:
        for b in secs:
            free = not b["warunek"] and not any(f["warunek"] for f in b["pola"])
            if a is not b and a["warunek"] and "nie" not in a["warunek"] and free and _twins(a, b):
                b["warunek"] = {"nie": a["warunek"]}
                notes.append(f"sekcja '{b['tytul']}' widoczna, gdy nie jest widoczna '{a['tytul']}'")
    return notes


def required(c: dict, ctx: Context) -> list[str]:
    """'Nieobowiązkowe' or 'jeśli posiada' makes a field optional; a conditional field is required when shown.
    The note ("(opcjonalnie)") leaves the title, since the e-form marks optional fields itself."""
    notes = []
    for _, f in plain_fields(c):
        if f["typ"] in ("TEXT_BLOCK", "CONSENT_SECTION"):
            continue
        optional = OPTIONAL.search(norm(" ".join([f["tytul"], f["opis"] or ""] + field_lines(f, ctx))))
        f["tytul"] = (
            PARENTHESIS.sub(lambda m: "" if OPTIONAL.search(norm(m[0])) else m[0], f["tytul"]).strip() or f["tytul"]
        )
        want = False if optional else True if f["warunek"] else f["wymagane"]
        if want != f["wymagane"]:
            f["wymagane"] = want
            notes.append(f"'{f['tytul']}' {'required' if want else 'nieobowiązkowe'}")
    return notes
