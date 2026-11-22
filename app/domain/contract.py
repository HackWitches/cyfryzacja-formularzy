"""The contract: the small JSON the model returns (pages, sections, fields, conditions, variants).

normalize() makes any model output safe to walk: every key present, every value of the expected type.
expand() turns the ADRES and OSOBA macros into plain fields. Standard library only.
"""

import re
import unicodedata

TYPES = {
    "TEXT_FIELD",
    "TEXTAREA",
    "NUMBER",
    "DATEPICKER",
    "RADIO",
    "CHECKBOX",
    "SELECT",
    "FILE_UPLOADER",
    "CONSENT_SECTION",
    "TEXT_BLOCK",
}
VALIDATORS = {"EMAIL", "PESEL", "NIP", "REGON", "ZIP_CODE", "EPUAP", "ADE", "SERIAL_NUMBER_ID_CARD"}
APPLICANT_DATA = {"PESEL", "NIP", "REGON", "KRS", "EPUAP", "ADE", "DOWOD", "DATA_URODZENIA"}
# COI rule: only these types take options or a dictionary, and only they may drive a visibility condition.
CHOICE_TYPES = {"SELECT", "RADIO", "CHECKBOX"}
BASE_PAGES = ("Dane", "Kontakt i zgody")
DEFAULT_PAGE_TITLE = "Szczegóły wniosku"
LINE_ID = re.compile(r"L\d+")  # line ids as app/ingest/lines.py numbers them
# Aliases the model sometimes uses for COI type names; mapping them keeps a near-miss from becoming TEXT_FIELD.
TYPE_ALIASES = {"TEXT": "TEXT_FIELD", "DATE": "DATEPICKER", "FILE": "FILE_UPLOADER"}


def json_schema() -> dict:
    """JSON Schema of the contract, for servers that constrain generation (vLLM guided decoding).
    The model then cannot return broken JSON or an unknown type; normalize() still runs afterwards."""
    s, b, arr = {"type": "string"}, {"type": "boolean"}, lambda item: {"type": "array", "items": item}
    leaf = {
        "type": "object",
        "properties": {"pole": s, "wartosc": s},
        "required": ["pole", "wartosc"],
        "additionalProperties": False,
    }

    def node(op, item):
        return {"type": "object", "properties": {op: item}, "required": [op], "additionalProperties": False}

    cond = {"$ref": "#/$defs/warunek"}
    opt_cond = {"anyOf": [cond, {"type": "null"}]}
    field = {
        "type": "object",
        "additionalProperties": False,
        "required": ["wzorzec", "techName", "tytul", "typ", "wymagane", "walidatory", "opcje", "warunek", "linie"],
        "properties": {
            "wzorzec": {"enum": ["POLE", "ADRES", "OSOBA"]},
            "techName": s,
            "tytul": s,
            "typ": {"enum": sorted(TYPES)},
            "wymagane": b,
            "walidatory": arr({"enum": sorted(VALIDATORS)}),
            "opcje": arr(s),
            "slownik": {"anyOf": [s, {"type": "null"}]},
            "opis": {"anyOf": [s, {"type": "null"}]},
            "warunek": opt_cond,
            "linie": arr({"type": "string", "pattern": "^L[0-9]{3,4}$"}),
        },
    }
    section = {
        "type": "object",
        "additionalProperties": False,
        "required": ["tytul", "techName", "powtarzalna", "warunek", "pola"],
        "properties": {"tytul": s, "techName": s, "powtarzalna": b, "warunek": opt_cond, "pola": arr(field)},
    }
    page = {
        "type": "object",
        "additionalProperties": False,
        "required": ["tytul", "warunek", "sekcje"],
        "properties": {"tytul": s, "warunek": opt_cond, "sekcje": arr(section)},
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["tytul", "adresat", "warianty", "dane_wnioskodawcy", "strony", "nowe_slowniki", "pominiete"],
        "$defs": {"warunek": {"anyOf": [leaf, node("i", arr(cond)), node("lub", arr(cond)), node("nie", cond)]}},
        "properties": {
            "tytul": s,
            "adresat": {
                "type": "object",
                "additionalProperties": False,
                "required": ["nazwa", "rodzaj"],
                "properties": {"nazwa": s, "rodzaj": {"enum": ["urzad", "organ"]}},
            },
            "warianty": {
                "type": "object",
                "additionalProperties": False,
                "required": ["podmiot", "pelnomocnik"],
                "properties": {"podmiot": b, "pelnomocnik": b},
            },
            "dane_wnioskodawcy": arr({"enum": sorted(APPLICANT_DATA)}),
            "strony": arr(page),
            "nowe_slowniki": arr(
                {
                    "type": "object",
                    "required": ["nazwa", "wartosci"],
                    "properties": {"nazwa": s, "zastosowanie": s, "wartosci": arr(s)},
                }
            ),
            "pominiete": arr(
                {"type": "object", "required": ["linie", "powod"], "properties": {"linie": arr(s), "powod": s}}
            ),
        },
    }


def ascii_fold(text) -> str:
    """Lower case without Polish diacritics: the one place that knows 'ł' has no Unicode decomposition."""
    text = unicodedata.normalize("NFKD", str(text or "").replace("ł", "l").replace("Ł", "L"))
    return text.encode("ascii", "ignore").decode().lower()


def snake(text, limit: int = 60) -> str:
    return re.sub(r"[^a-z0-9]+", "_", ascii_fold(text)).strip("_")[:limit].rstrip("_") or "pole"


def leaves(cond: dict | None) -> list[dict]:
    """All {pole, wartosc} leaves of a condition tree."""
    if not cond:
        return []
    if "pole" in cond:
        return [cond]
    if "nie" in cond:
        return leaves(cond["nie"])
    return [x for sub in cond.get("i", []) + cond.get("lub", []) for x in leaves(sub)]


def both(a, b):
    return {"i": [a, b]} if a and b else a or b


def _cond(c):
    if not isinstance(c, dict):
        return None
    if "pole" in c:
        return {"pole": snake(c["pole"]), "wartosc": str(c.get("wartosc", c.get("rowna", "")))}
    if "nie" in c:
        sub = _cond(c["nie"])
        return {"nie": sub} if sub else None
    for op in ("i", "lub"):
        if isinstance(c.get(op), list):
            subs = [s for s in map(_cond, c[op]) if s]
            return None if not subs else subs[0] if len(subs) == 1 else {op: subs}
    return None


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _text(value) -> str | None:
    """A non-empty string or None (a model without schema enforcement may put a list or a number here)."""
    return value.strip() or None if isinstance(value, str) else None


def _field(f):
    typ = str(f.get("typ") or "TEXT_FIELD").upper()
    typ = TYPE_ALIASES.get(typ, typ)
    typ = typ if typ in TYPES else "TEXT_FIELD"
    val = [v for v in (str(v).upper() for v in _list(f.get("walidatory"))) if v in VALIDATORS]
    if val and typ in ("NUMBER", "TEXTAREA"):
        typ = "TEXT_FIELD"  # format validators exist only in TEXT_FIELD; a PESEL as NUMBER would lose leading zeros
    elif typ != "TEXT_FIELD":
        val = []  # COI rule: other types take only REQUIRED
    return {
        "wzorzec": str(f.get("wzorzec") or "POLE").upper(),
        "techName": snake(f.get("techName") or f.get("tytul") or f.get("etykieta")),
        "tytul": str(f.get("tytul") or f.get("etykieta") or "").strip(),
        "typ": typ,
        "wymagane": f.get("wymagane") is not False,  # COI rule: every field states it; unknown means required
        "walidatory": val,
        "opcje": [str(o).strip() for o in _list(f.get("opcje")) if str(o).strip()],
        "slownik": _text(f.get("slownik")),
        "opis": _text(f.get("opis")) or _text(f.get("podpowiedz")),
        "warunek": _cond(f.get("warunek") or f.get("pokaz_gdy")),
        "linie": [i for i in _list(f.get("linie")) if isinstance(i, str) and LINE_ID.fullmatch(i)],
        "tooltip": _text(f.get("tooltip")),
    }


# Titles of the applicant's own data, which the base holds (twoje_dane, adres_zamieszkania, kontakt).
# The e-service replaces signature and date. Weaker models generate these fields despite the prompt.
BASE_TITLE = re.compile(
    r"^(imie|nazwisko|imie i nazwisko|nazwisko i imie|imie nazwisko|nazwa|imie i nazwisko nazwa|nazwisko imie nazwa"
    r"|nazwa firmy|imie i nazwisko nazwa wnioskodawcy"
    r"|adres|adres zamieszkania|adres siedziby|adres zamieszkania siedziba"
    r"|(nr |numer )?(telefonu?|tel)( kontaktowy| wnioskodawcy)?|(adres )?e ?mail|nr telefonu adres e ?mail"
    r"|numer telefonu adres e ?mail|telefon e ?mail|podpis.*|data|data zlozenia.*|miejscowosc i data|miejscowosc data"
    r"|pesel|nip|regon|krs)( wnioskodawcy| wnioskujacego| skladajacego)?$"
)
# Words naming another person or an object (property, building): such fields are subject data, not base data.
OTHER_PERSON = re.compile(
    r"innej|wspolmalzon|kandydat|dziec|pelnomocnik|wlasciciel|trener|podopieczn|odpowiedzialn"
    r"|nieruchomosc|korespondenc|budynk|dzialk|klubu"
)


PAPER_NUMBERING = re.compile(r"^(\d{1,2} )+")  # "3. Data" -> "3 data" after snake(): the paper's item number


def is_base_field(f: dict) -> bool:
    title = PAPER_NUMBERING.sub("", " ".join(snake(f.get("tytul") or f.get("etykieta") or "", 200).split("_")))
    name = f.get("techName") or ""
    if OTHER_PERSON.search(title) or OTHER_PERSON.search(name):
        return False
    return bool(BASE_TITLE.match(title)) or name.startswith(
        ("podpis", "data_zlozenia", "imie_nazwisko_wnioskodawcy", "adres_wnioskodawcy", "telefon_wnioskodawcy")
    )


def normalize(raw: dict) -> dict:
    """Turns any model output into a full contract and drops fields the base already has.
    Dropped titles go to `_pola_bazy`. Idempotent: a second call only rechecks the shape.
    So the base-field guard runs once, on the model's own titles, before the rules rewrite them."""
    c = _shape(raw)
    if "_pola_bazy" in raw:
        return c
    for page in c["strony"]:
        for sec in page["sekcje"]:
            where = " ".join(snake(sec["tytul"] + " " + sec["techName"], 300).split("_"))
            if OTHER_PERSON.search(where):
                continue  # e.g. "Dane kandydata": its name fields are the subject, not the applicant
            c["_pola_bazy"] += [f["tytul"] or f["techName"] for f in sec["pola"] if is_base_field(f)]
            sec["pola"] = [f for f in sec["pola"] if not is_base_field(f)]
    return c


def _shape(raw: dict) -> dict:
    """Every key present and of the expected type; a flat answer (sections without pages) becomes one page."""
    pages = _list(raw.get("strony")) or [{"tytul": raw.get("tytul") or DEFAULT_PAGE_TITLE, "sekcje": raw.get("sekcje")}]
    adresat = raw.get("adresat") if isinstance(raw.get("adresat"), dict) else {}
    warianty = raw.get("warianty") if isinstance(raw.get("warianty"), dict) else {}
    return {
        "tytul": str(raw.get("tytul") or "").strip(),
        "adresat": {
            "nazwa": str(adresat.get("nazwa") or "").strip(),
            "rodzaj": "organ" if adresat.get("rodzaj") == "organ" else "urzad",
        },
        "warianty": {
            "podmiot": bool(warianty.get("podmiot", False)),
            "pelnomocnik": warianty.get("pelnomocnik") is not False,
        },
        "dane_wnioskodawcy": [
            d for d in (str(x).upper() for x in _list(raw.get("dane_wnioskodawcy"))) if d in APPLICANT_DATA
        ],
        "strony": [
            {
                "tytul": str(p.get("tytul") or DEFAULT_PAGE_TITLE).strip(),
                "warunek": _cond(p.get("warunek")),
                "sekcje": [
                    {
                        "tytul": str(s.get("tytul") or s.get("nazwa") or "").strip(),
                        "techName": snake(s.get("techName") or s.get("tytul") or s.get("nazwa")),
                        "powtarzalna": bool(s.get("powtarzalna")),
                        "warunek": _cond(s.get("warunek")),
                        "pola": [_field(f) for f in _list(s.get("pola")) if isinstance(f, dict)],
                    }
                    for s in _list(p.get("sekcje"))
                    if isinstance(s, dict)
                ],
            }
            for p in pages
            if isinstance(p, dict)
        ],
        "nowe_slowniki": [
            {
                "nazwa": str(d["nazwa"]),
                "zastosowanie": str(d.get("zastosowanie") or ""),
                "wartosci": [str(v).strip() for v in _list(d.get("wartosci")) if str(v).strip()],
            }
            for d in _list(raw.get("nowe_slowniki"))
            if isinstance(d, dict) and d.get("nazwa")
        ],
        "_pola_bazy": [str(t) for t in _list(raw.get("_pola_bazy"))],  # its presence marks a normalized contract
        "pominiete": _list(raw.get("pominiete")),
    }


def _plain(tech, title, typ="TEXT_FIELD", required=False, validators=(), dictionary=None, cond=None, lines=()):
    return {
        "wzorzec": "POLE",
        "techName": tech,
        "tytul": title,
        "typ": typ,
        "wymagane": required,
        "walidatory": list(validators),
        "opcje": [],
        "slownik": dictionary,
        "opis": None,
        "warunek": cond,
        "linie": list(lines),
        "tooltip": None,
    }


def _address(f):
    s = f["techName"]
    pl = {"pole": f"kraj_{s}", "wartosc": "Polska"}
    abroad = {"nie": pl}
    rows = [  # same fields and order as the base 'adres_zamieszkania'
        (f"kraj_{s}", "Kraj", "SELECT", True, (), "Kraje", None),
        (f"miejscowosc_{s}_zagranica", "Miejscowość", "TEXT_FIELD", True, (), None, abroad),
        (f"kod_pocztowy_{s}_zagranica", "Kod pocztowy", "TEXT_FIELD", False, (), None, abroad),
        (f"ulica_{s}_zagranica", "Ulica", "TEXT_FIELD", False, (), None, abroad),
        (f"wojewodztwo_{s}", "TERYT_4G_DOWODOWY", "SELECT", True, (), "TERYT_4G_DOWODOWY", pl),
        (f"kod_pocztowy_{s}", "Kod pocztowy", "TEXT_FIELD", True, ("ZIP_CODE",), None, pl),
        (f"ulica_{s}", "Ulica", "TEXT_FIELD", False, (), None, pl),
        (f"numer_budynku_{s}", "Numer budynku", "TEXT_FIELD", True, (), None, None),
        (f"numer_lokalu_{s}", "Numer lokalu", "TEXT_FIELD", False, (), None, None),
    ]
    return [_plain(t, ti, ty, rq, va, di, both(c, f["warunek"]), f["linie"]) for t, ti, ty, rq, va, di, c in rows]


def _person(f):
    s = f["techName"]
    return [
        _plain(f"imie_{s}", "Imię", required=True, cond=f["warunek"], lines=f["linie"]),
        _plain(f"nazwisko_{s}", "Nazwisko", required=True, cond=f["warunek"], lines=f["linie"]),
    ]


MACROS = {"ADRES": _address, "OSOBA": _person}


def expand(contract: dict) -> dict:
    """Replace macro fields by their plain fields (in place) and return the contract."""
    for page in contract["strony"]:
        for sec in page["sekcje"]:
            sec["pola"] = [x for f in sec["pola"] for x in (MACROS[f["wzorzec"]](f) if f["wzorzec"] in MACROS else [f])]
    return contract


def fields(contract: dict):
    """Every field with its page and section (used by the evaluation tools that compare contracts)."""
    for page in contract["strony"]:
        for sec in page["sekcje"]:
            for f in sec["pola"]:
                yield page, sec, f
