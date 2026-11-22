"""Cleanup: titles without paper artefacts, no office-use or duplicate fields, statements as consents."""

import re

from app.domain.contract import leaves
from app.rules.text import OPTIONAL, Context, field_lines, norm, plain_fields, sections

# Paper numbering: "3.", "3)", "3.1", "IV.", "B.", "B.1.2", "a)". A bare number or letter is a word ("2 fotografie").
NUMBERING = re.compile(
    r"^\s*(\d{1,2}(\.\d{1,2})+[.)]?|\d{1,2}[.)]|[IVX]{1,4}[.)]|[A-H](\.\d{1,2})+[.)]?|[A-H][.)]"
    r"|[a-h]\))\s+"
)
FOOTNOTE_END = re.compile(r"((?<!m)\s*[¹²³⁴⁵⁶⁷⁸⁹⁰]+|\s*\*+)+$")  # m², m³ are units
FOOTNOTE_PAREN = re.compile(r"(?<=[^\d\s])\d{1,2}\)$")  # "Nazwisko1)"; only when the bracket closes nothing: "(m2)"
INSTRUCTION = re.compile(
    r"\s*\((?:postaw|zaznacz|nale[zż]y|wype[lł]ni|niepotrzebne|dot\.|opcjonaln|wpisa|podkre[sś]l"
    r"|drukowanymi)[^)]*\)?",
    re.IGNORECASE,
)
TRAIL = re.compile(r"[\s.…_:*]+$")
LAW = re.compile(r"\s*\(\s*Dz\.\s*U\.[^)]*\)?|,?\s*(na podstawie|zgodnie z) art\..*$", re.IGNORECASE)
ACRONYMS = {"NIP", "PESEL", "REGON", "KRS", "IBAN", "EPUAP", "CEIDG", "TERYT", "RODO", "PKD", "KW"}
OFFICE_USE = re.compile(
    r"wypelnia (urzad|organ|pracownik)|\b(nr|numer|znak) sprawy|data wplywu"
    r"|potwierdzenie (przyjecia|wplywu)|adnotacj|podpis (pracownika|przyjmujacego|osoby przyjmujacej)"
    r"|\bdecyzj\w* nr\b|kancelari|pieczec (urzedu|wplywu)|rejestr wplywu"
)
# Fields the base already holds; the model still generates them for other people named on the paper.
PROXY = re.compile(r"reprezentant|pelnomocni")
PERSON_DATA = re.compile(r"imie|nazwisko|telefon|dowod|pesel|adres|dane|seria|email|e mail|krs")
SIGNATURE = re.compile(r"^(czytelny )?(data i )?podpis")
SIGNATURE_BLOCK = re.compile(r"(data|imie i nazwisko|imie|nazwisko)( \w+){0,4}")
DATA_CONSENT = re.compile(r"danych osobowych|\brodo\b")
CONSENT_WORD = re.compile(r"wyraz|zgod|informacj|klauzul")
PROXY_PAPER = re.compile(r"dokument|oryginal|odpis|oplat|dowod|potwierdzen|zalacznik")
PAYMENT = re.compile(r"oplat|dowod|potwierdzen")
STATEMENT = re.compile(
    r"^\W*(oswiadczam|wyrazam zgode|zapoznal|zobowiazuj|jestem swiadom|przyjmuje do wiadomosci"
    r"|potwierdzam|akceptuje)"
)


def _sentence_case(t: str) -> str:
    if not t.isupper() or len(t) < 4:
        return t
    words = ["/".join(p if p.strip("().,:") in ACRONYMS else p.lower() for p in w.split("/")) for w in t.split()]
    out = " ".join(words)
    return out[:1].upper() + out[1:]


def _without_footnotes(t: str) -> str:
    while True:
        new = FOOTNOTE_END.sub("", t)
        if new.count(")") > new.count("("):
            new = FOOTNOTE_PAREN.sub("", new)
        if new == t:
            return t
        t = new


def clean(title: str, keep_optional_note: bool = False) -> str:
    """A title without paper artefacts.
    With `keep_optional_note`, a note that the field may stay empty ("(wypełnić, jeśli dotyczy)") stays.
    The 'required' rule reads that note and then removes it."""

    def instruction(m):
        return m[0] if keep_optional_note and OPTIONAL.search(norm(m[0])) else ""

    t = INSTRUCTION.sub(instruction, NUMBERING.sub("", title or ""))
    t = TRAIL.sub("", _without_footnotes(t.strip()))
    t = _sentence_case(" ".join(t.split()))
    return t or title


def clean_titles(c: dict, ctx: Context) -> list[str]:
    """Paper numbering, footnote marks, '(zaznaczyć właściwe)', dots, capitals and law references go away."""
    notes, before = [], c["tytul"]
    c["tytul"] = clean(LAW.sub("", c["tytul"])) if c["tytul"] else c["tytul"]
    if c["tytul"] != before:
        notes.append(f"tytuł formularza: '{before}' -> '{c['tytul']}'")
    fields = [f for _, f in plain_fields(c) if f["typ"] not in ("TEXT_BLOCK", "CONSENT_SECTION")]
    for x in c["strony"] + list(sections(c)) + fields:
        new = clean(x["tytul"], keep_optional_note=x in fields)
        if new != x["tytul"]:
            notes.append(f"'{x['tytul']}' -> '{new}'")
            x["tytul"] = new
    return notes


def drop_office_fields(c: dict, ctx: Context) -> list[str]:
    """'Wypełnia urząd', case number, date of receipt: the official fills these, not the citizen."""
    notes = []
    for page in c["strony"]:
        keep = []
        for sec in page["sekcje"]:
            if OFFICE_USE.search(norm(sec["tytul"])):
                notes.append(f"sekcja '{sec['tytul']}'")
                continue
            dropped = [f for f in sec["pola"] if f["wzorzec"] == "POLE" and OFFICE_USE.search(norm(f["tytul"]))]
            sec["pola"] = [f for f in sec["pola"] if f not in dropped]
            notes += [f"pole '{f['tytul']}'" for f in dropped]
            keep.append(sec)
        page["sekcje"] = keep
    return notes


def drop_duplicates(c: dict, ctx: Context) -> list[str]:
    """Small models loop on long tables and repeat the same field; keep the first one in each section."""
    notes = []
    for sec in sections(c):
        seen, keep = set(), []
        for f in sec["pola"]:
            key = (
                f["wzorzec"],
                norm(f["tytul"]),
                f["typ"],
                tuple(norm(o) for o in f["opcje"]),
                str(f["warunek"]),
                tuple(f["linie"]),
            )  # same label under another condition or on other lines is a different field
            if key in seen:
                notes.append(f"'{f['tytul']}' w '{sec['tytul']}'")
                continue
            seen.add(key)
            keep.append(f)
        sec["pola"] = keep
    return notes


def _referenced(c: dict) -> set[str]:
    conds = (
        [p["warunek"] for p in c["strony"]]
        + [s["warunek"] for s in sections(c)]
        + [f["warunek"] for s in sections(c) for f in s["pola"]]
    )
    return {leaf["pole"] for cond in conds for leaf in leaves(cond)}


def statements_to_consents(c: dict, ctx: Context) -> list[str]:
    """A lone 'Oświadczam…' checkbox becomes a consent, unless another part of the form depends on it.
    COI rule: statements are consents, not standalone checkboxes."""
    notes, used = [], _referenced(c)
    for _, f in plain_fields(c):
        lone = not f["opcje"] or [norm(o) for o in f["opcje"]] == [norm(f["tytul"])]
        if f["typ"] == "CHECKBOX" and lone and f["techName"] not in used and STATEMENT.search(norm(f["tytul"])):
            f["typ"], f["opcje"], f["walidatory"] = "CONSENT_SECTION", [], []
            notes.append(f"'{f['tytul'][:60]}' -> sekcja zgód")
    return notes


def _words(s) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", norm(s)))


def _base_reason(f: dict, sec: dict, ctx: Context) -> str | None:
    t, s = _words(f["tytul"]), _words(sec["tytul"])
    if PROXY.search(t) and PERSON_DATA.search(t):
        return "dane reprezentanta lub pełnomocnika"
    if PROXY.search(s) and PERSON_DATA.search(t) and f["typ"] != "CHECKBOX":
        return "dane w sekcji reprezentanta lub pełnomocnika"
    if SIGNATURE.search(t) or ("podpis" in s and SIGNATURE_BLOCK.fullmatch(t)):
        return "podpis"
    if CONSENT_WORD.search(t) and DATA_CONSENT.search(t) and not PROXY.search(t):
        return "zgoda na przetwarzanie danych"
    if "pelnomocnictw" in t and PROXY_PAPER.search(t):
        return "pełnomocnictwo"
    if (
        f["typ"] in ("FILE_UPLOADER", "CHECKBOX")
        and PAYMENT.search(t)
        and "pelnomocnictw" in _words(" ".join(field_lines(f, ctx)))
    ):
        return "opłata za pełnomocnictwo"
    return None


def drop_base_person_fields(c: dict, ctx: Context) -> list[str]:
    """Drops proxy, representative, signature and RODO-consent fields: the base already covers them.
    Power of attorney and its fee go too. The base has proxy and entity variants and an electronic signature.
    The model adds these when the paper names another person ("reprezentant klubu"), and normalize() lets them pass.
    Runs last, on the final types. Dropped titles join `_pola_bazy`, so the official sees them as a flag."""
    notes = []
    for sec in sections(c):
        reasons = [(f, _base_reason(f, sec, ctx)) for f in sec["pola"]]
        sec["pola"] = [f for f, why in reasons if not why]
        c["_pola_bazy"] += [f["tytul"] or f["techName"] for f, why in reasons if why]
        notes += [f"'{f['tytul'][:60]}': {why}" for f, why in reasons if why]
    return notes
