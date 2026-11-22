"""The form's header: who may apply (person or entity) and to whom the form goes."""
import re

from app.rules.text import Context, norm

# Paper signals that a company or organisation may apply (docs/ARCHITEKTURA.md, section 2.2). Weaker models drop
# them ("imię i nazwisko/nazwa wnioskodawcy" -> "Imię i nazwisko"), which removes the entity sections from the base.
ENTITY_SIGNAL = re.compile(r"nazwa (firmy|podmiotu|wnioskodawcy)|/ ?nazwa|\bNIP\b|\bREGON\b|\bKRS\b|siedzib"
                           r"|piecz[eę][cć](?! (urz[eę]du|organu))", re.IGNORECASE)
NOT_FORM_TEXT = re.compile(r"administrator|dane osobowe|danych osobowych|RODO|inspektor|wype[lł]nia (urz|organ)",
                           re.IGNORECASE)
# Addressee in the header: a capitalized office or authority in the nominative ("Wójt Gminy Elbląg", "URZĄD GMINY").
# Case-sensitive on purpose: "wypełnia urząd" in prose is not an addressee.
_HEADS = ["Burmistrz", "Wójt", "Prezydent", "Starosta", "Starostwo", "Marszałek", "Urząd", "Zarząd", "Dyrektor",
          "Komendant", "Kierownik", "Naczelnik", "Rada", "Samorządowe Kolegium"]
ADDRESSEE = re.compile(r"(?<!\w)((?:" + "|".join(_HEADS + [h.upper() for h in _HEADS]) + r")(?![\wąćęłńóśźż]).*)$")
NOT_NAME = re.compile(r"\d|\bul\.|\bal\.|wnios|deklarac|zarzadz|regulamin|instrukcj|[.…]{3}|……")
ORGAN = re.compile(r"^(do )?(wojt|burmistrz|prezydent|starosta|marszal|dyrektor|rada|komendant|kierownik|naczelnik"
                   r"|zarzad|samorzadowe kolegium)")
OFFICE = re.compile(r"^(do )?(urzad|starostwo)")


def entity_signal(lines_text: str) -> bool:
    """True when the paper itself admits a legal entity as the applicant (RODO clause and office fields ignored)."""
    return any(ENTITY_SIGNAL.search(ln) for ln in lines_text.splitlines() if not NOT_FORM_TEXT.search(ln))


def podmiot(c: dict, ctx: Context) -> list[str]:
    """Model OR paper; the proxy variant stays the model's decision."""
    if not c["warianty"]["podmiot"] and entity_signal(ctx.text):
        c["warianty"]["podmiot"] = True
        return ["papier dopuszcza firmę lub instytucję"]
    return []


def _addressee(lines: list[str]) -> str:
    for i, ln in enumerate(lines[:80]):
        m = ADDRESSEE.search(ln)
        if not m:
            continue
        name = re.split(r"\s{3,}", m[1])[0].strip(" ,:")
        if len(name) > 90 or NOT_NAME.search(norm(name)):
            continue
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if len(name) < 45 and 0 < len(nxt) < 45 and nxt[0].isupper() and not NOT_NAME.search(norm(nxt)) \
                and not re.search(r"[.…(/]", nxt):
            name += (", " if norm(nxt).startswith("wydzial") else " ") + nxt
        return name
    return ""


def adresat(c: dict, ctx: Context) -> list[str]:
    """Fill a missing addressee from the header; office vs authority always follows the name (COI standard)."""
    notes, a = [], c["adresat"]
    if not a["nazwa"]:
        a["nazwa"] = _addressee(list(ctx.lines.values()))
        if a["nazwa"]:
            notes.append(f"nazwa z nagłówka: '{a['nazwa']}'")
    name = norm(a["nazwa"])
    kind = "organ" if ORGAN.match(name) else "urzad" if OFFICE.match(name) else a["rodzaj"]
    if kind != a["rodzaj"]:
        notes.append(f"rodzaj {a['rodzaj']} -> {kind}")
        a["rodzaj"] = kind
    return notes
