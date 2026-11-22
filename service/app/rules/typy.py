"""Field type, format validator and format hint from what the paper shows: the label and the character cells."""
import re

from app.rules.text import Context, field_lines, norm, plain_fields

ACCOUNT = "26 cyfr numeru rachunku"
# (expression on the normalized label, type, validator, hint); first match wins. Format validators exist only in
# TEXT_FIELD (COI technical rules), so identifiers stay text fields even though they are digits.
LABEL = [(re.compile(rx), typ, val, tip) for rx, typ, val, tip in [
    (r"e-?mail|poczty elektronicznej", "TEXT_FIELD", "EMAIL", None),
    (r"epuap", "TEXT_FIELD", "EPUAP", None),
    (r"e-?doreczen", "TEXT_FIELD", "ADE", None),
    (r"dowod\w* osobist|seria i (nr|numer) dowodu", "TEXT_FIELD", "SERIAL_NUMBER_ID_CARD", "Np. ABC123456"),
    (r"\bpesel\b", "TEXT_FIELD", "PESEL", "11 cyfr"),
    (r"\bnip\b", "TEXT_FIELD", "NIP", "10 cyfr"),
    (r"\bregon\b", "TEXT_FIELD", "REGON", "9 lub 14 cyfr"),
    (r"^(?!.*wlasciciel).*(\b(nr|numer)\w* (rachunk|konta)|rachunku bankowego|konta bankowego|\biban\b)",
     "TEXT_FIELD", None, ACCOUNT),
    (r"kod\w* pocztow", "TEXT_FIELD", "ZIP_CODE", "Format: 00-000"),
    (r"telefon|\btel\b|komork", "TEXT_FIELD", None, "Np. 600 700 800"),
    (r"^(?!.*miejsc).*(\bdata\b|\bdnia\b|\bdzien\b|dd-mm|rrrr|\burodzeni)", "DATEPICKER", None, None),
    (r"^(?!.*slownie).*(\bkwot|\bstawk|\bwysokosc\b|\bzl\b|\bliczba\b|\bilosc\b|powierzchni|\bm2\b|\bha\b|\bsztuk)",
     "NUMBER", None, None),
    (r"uzasadnieni|\bopis\b|\buwagi\b|wyjasnieni|informacje dodatkowe", "TEXTAREA", None, None),
]]
# Rows of character cells, as lines.py writes them: "[#2] - [#2] - [#4]" is one group of three parts.
CELL_GROUP = re.compile(r"\[#\d+\](?:\s*[-./]\s*\[#\d+\])*")
CELLS = {(2, 2, 4): ("DATEPICKER", None, None), (4, 2, 2): ("DATEPICKER", None, None),
         (2, 3): ("TEXT_FIELD", "ZIP_CODE", "Format: 00-000"), (11,): ("TEXT_FIELD", "PESEL", "11 cyfr"),
         (10,): ("TEXT_FIELD", "NIP", "10 cyfr"), (9,): ("TEXT_FIELD", "REGON", "9 lub 14 cyfr"),
         (14,): ("TEXT_FIELD", "REGON", "9 lub 14 cyfr"), (26,): ("TEXT_FIELD", None, ACCOUNT)}
COUNTRY = re.compile(r"^(kraj|panstwo|obywatelstwo|narodowosc)( (urodzenia|zamieszkania|pochodzenia|wydania))?$")
MULTI = re.compile(r"mozna zaznaczyc (kilka|wiele|wiecej)|wielokrotn|zaznacz\w* wszystkie|kilka odpowiedzi"
                   r"|dowolna liczb")


def _set(f, typ, val, tip, why):
    before = (f["typ"], f["walidatory"], f["tooltip"])
    f["typ"], f["walidatory"], f["tooltip"] = typ, [val] if val else [], f["tooltip"] or tip
    return [f"'{f['tytul']}' {before[0]}{before[1] or ''} -> {typ}{f['walidatory'] or ''} ({why})"] \
        if (f["typ"], f["walidatory"], f["tooltip"]) != before else []


def typ_z_etykiety(c: dict, ctx: Context) -> list[str]:
    """Only text and number fields: a choice, file or date the model picked is never overridden."""
    notes = []
    for _, f in plain_fields(c):
        if f["typ"] not in ("TEXT_FIELD", "NUMBER"):
            continue
        label = norm(f["tytul"])
        hits = [(typ, val, tip) for rx, typ, val, tip in LABEL if rx.search(label)]
        if not hits or len({val or tip for _, val, tip in hits if val or tip}) > 1:
            continue  # "NIP/REGON", "telefon/e-mail": one field, two formats; the model's choice stays
        typ, val, tip = hits[0]
        if not (f["typ"] == "NUMBER" and typ in ("NUMBER", "TEXTAREA")):
            notes += _set(f, typ, val, tip, "etykieta")
    return notes


def typ_z_kratek(c: dict, ctx: Context) -> list[str]:
    """Exactly one group of character cells on the field's lines decides; several groups = not ours to guess."""
    notes = []
    for _, f in plain_fields(c):
        if f["typ"] not in ("TEXT_FIELD", "NUMBER") or f["walidatory"] or f["tooltip"]:
            continue  # the label rule already decided
        groups = [g for ln in field_lines(f, ctx) for g in CELL_GROUP.findall(ln)]
        parts = tuple(int(n) for n in re.findall(r"\d+", groups[0])) if len(groups) == 1 else None
        if parts in CELLS:
            notes += _set(f, *CELLS[parts], f"kratki {groups[0]}")
    return notes


def slownik_kraje(c: dict, ctx: Context) -> list[str]:
    notes = []
    for _, f in plain_fields(c):
        if f["typ"] in ("TEXT_FIELD", "SELECT") and f["slownik"] != "Kraje" \
                and COUNTRY.match(norm(f["tytul"]).strip(" .:")):
            f["typ"], f["slownik"], f["opcje"], f["walidatory"] = "SELECT", "Kraje", [], []
            notes.append(f"'{f['tytul']}' -> SELECT ze słownikiem Kraje")
    return notes


def _previous_line(line_id: str) -> str:
    return f"L{int(line_id[1:]) - 1:0{len(line_id) - 1}d}"


def wielokrotny_wybor(c: dict, ctx: Context) -> list[str]:
    """'Można zaznaczyć kilka' next to a list = checkboxes (COI standard: radio is a single choice)."""
    notes = []
    for sec, f in plain_fields(c):
        if f["typ"] != "RADIO" or not f["opcje"]:
            continue
        around = [ctx.lines.get(_previous_line(f["linie"][0]), "")] if f["linie"] else []
        text = norm(" ".join([sec["tytul"], f["tytul"], f["opis"] or ""] + around + field_lines(f, ctx)))
        if MULTI.search(text):
            f["typ"] = "CHECKBOX"
            notes.append(f"'{f['tytul']}' RADIO -> CHECKBOX (wielokrotny wybór)")
    return notes
