"""Cleaning: titles without paper artefacts, no office-use or duplicated fields, statements as consents."""
import re

from app.domain.contract import leaves
from app.rules.text import Context, norm, plain_fields, sections

NUMBERING = re.compile(r"^\s*(\d{1,2}(\.\d{1,2})*[.)]?|[IVX]{1,4}[.)]|[A-H](\.\d{1,2})*[.)]?|[a-h]\))\s+")
FOOTNOTE_END = re.compile(r"((?<!m)\s*[¹²³⁴⁵⁶⁷⁸⁹⁰]+|\s*\*+|(?<=[^\d\s])\d{1,2}\))+$")  # m², m³ are units
INSTRUCTION = re.compile(r"\s*\((?:postaw|zaznacz|nale[zż]y|wype[lł]ni|niepotrzebne|dot\.|opcjonaln|wpisa|podkre[sś]l"
                         r"|drukowanymi)[^)]*\)?", re.IGNORECASE)
TRAIL = re.compile(r"[\s.…_:*]+$")
LAW = re.compile(r"\s*\(\s*Dz\.\s*U\.[^)]*\)?|,?\s*(na podstawie|zgodnie z) art\..*$", re.IGNORECASE)
ACRONYMS = {"NIP", "PESEL", "REGON", "KRS", "IBAN", "EPUAP", "CEIDG", "TERYT", "RODO", "PKD", "KW"}
OFFICE_USE = re.compile(r"wypelnia (urzad|organ|pracownik)|\b(nr|numer|znak) sprawy|data wplywu"
                        r"|potwierdzenie (przyjecia|wplywu)|adnotacj|podpis (pracownika|przyjmujacego|osoby przyjmujacej)"
                        r"|\bdecyzj\w* nr\b|kancelari|pieczec (urzedu|wplywu)|rejestr wplywu")
STATEMENT = re.compile(r"^\W*(oswiadczam|wyrazam zgode|zapoznal|zobowiazuj|jestem swiadom|przyjmuje do wiadomosci"
                       r"|potwierdzam|akceptuje)")


def _sentence_case(t: str) -> str:
    if not t.isupper() or len(t) < 4:
        return t
    words = ["/".join(p if p.strip("().,:") in ACRONYMS else p.lower() for p in w.split("/")) for w in t.split()]
    out = " ".join(words)
    return out[:1].upper() + out[1:]


def clean(title: str) -> str:
    t = INSTRUCTION.sub("", NUMBERING.sub("", title or ""))
    t = TRAIL.sub("", FOOTNOTE_END.sub("", t.strip()))
    t = _sentence_case(" ".join(t.split()))
    return t or title


def tytuly(c: dict, ctx: Context) -> list[str]:
    """Paper numbering, footnote marks, '(zaznaczyć właściwe)', dots, capitals and law references go away."""
    notes, before = [], c["tytul"]
    c["tytul"] = clean(LAW.sub("", c["tytul"])) if c["tytul"] else c["tytul"]
    if c["tytul"] != before:
        notes.append(f"tytuł formularza: '{before}' -> '{c['tytul']}'")
    things = [p for p in c["strony"]] + list(sections(c)) + [f for _, f in plain_fields(c)
                                                            if f["typ"] not in ("TEXT_BLOCK", "CONSENT_SECTION")]
    for x in things:
        new = clean(x["tytul"])
        if new != x["tytul"]:
            notes.append(f"'{x['tytul']}' -> '{new}'")
            x["tytul"] = new
    return notes


def pola_urzedu(c: dict, ctx: Context) -> list[str]:
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


def duplikaty(c: dict, ctx: Context) -> list[str]:
    """Small models loop on long tables and repeat the same field; keep the first one in each section."""
    notes = []
    for sec in sections(c):
        seen, keep = set(), []
        for f in sec["pola"]:
            key = (f["wzorzec"], norm(f["tytul"]), f["typ"], tuple(norm(o) for o in f["opcje"]), str(f["warunek"]),
                   tuple(f["linie"]))  # same label under another condition or on other lines is a different field
            if key in seen:
                notes.append(f"'{f['tytul']}' w '{sec['tytul']}'")
                continue
            seen.add(key)
            keep.append(f)
        sec["pola"] = keep
    return notes


def _referenced(c: dict) -> set[str]:
    conds = [p["warunek"] for p in c["strony"]] + [s["warunek"] for s in sections(c)] + \
            [f["warunek"] for s in sections(c) for f in s["pola"]]
    return {leaf["pole"] for cond in conds for leaf in leaves(cond)}


def oswiadczenia(c: dict, ctx: Context) -> list[str]:
    """A lone 'Oświadczam…' checkbox becomes a consent (COI standard: no standalone checkboxes for statements),
    unless another part of the form depends on it."""
    notes, used = [], _referenced(c)
    for _, f in plain_fields(c):
        lone = not f["opcje"] or [norm(o) for o in f["opcje"]] == [norm(f["tytul"])]
        if f["typ"] == "CHECKBOX" and lone and f["techName"] not in used and STATEMENT.search(norm(f["tytul"])):
            f["typ"], f["opcje"], f["walidatory"] = "CONSENT_SECTION", [], []
            notes.append(f"'{f['tytul'][:60]}' -> sekcja zgód")
    return notes
