"""When a field is shown and whether it is required."""
import re

from app.domain.contract import leaves
from app.rules.text import Context, field_lines, norm, plain_fields, sections

CHOICE = {"RADIO", "CHECKBOX", "SELECT"}
OPTIONAL = re.compile(r"opcjonaln|nieobowiazk|(jesli|jezeli) (dotyczy|posiada)|o ile (posiada|dotyczy)|fakultatywn"
                      r"|w przypadku posiadania")
IF_YES = re.compile(r"^\W*(jezeli|jesli|w przypadku|gdy)\b.*\btak\b|^\W*(jezeli|jesli) (tak|zaznaczono)")
OTHER = re.compile(r"^\W*(inne|inny|inna|innego|innych)\b(?! uwag)|^\W*(jakie|jaki|jaka)\b.*\binn"
                   r"|^\W*(prosze )?(podac|wskazac|wpisac) (jak|inn)")
OTHER_OPTION = re.compile(r"^(inne|inny|inna|innego|innych)\b")


def warunek_inne_tak(c: dict, ctx: Context) -> list[str]:
    """A field right after a list that names one of its options ('Inne: ……', 'Jeżeli tak, podaj ……') is shown only
    after that option is picked. Fields that already have a condition are left alone."""
    notes = []
    for sec in sections(c):
        prev = None
        for f in sec["pola"]:
            if f["typ"] in CHOICE and f["opcje"]:
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


def wymagane(c: dict, ctx: Context) -> list[str]:
    """'Nieobowiązkowe', 'jeśli posiada' -> optional; a field shown only after a choice is required when shown."""
    notes = []
    for _, f in plain_fields(c):
        if f["typ"] in ("TEXT_BLOCK", "CONSENT_SECTION"):
            continue
        optional = OPTIONAL.search(norm(" ".join([f["tytul"], f["opis"] or ""] + field_lines(f, ctx))))
        want = False if optional else True if f["warunek"] else f["wymagane"]
        if want != f["wymagane"]:
            f["wymagane"] = want
            notes.append(f"'{f['tytul']}' {'wymagane' if want else 'nieobowiązkowe'}")
    return notes
