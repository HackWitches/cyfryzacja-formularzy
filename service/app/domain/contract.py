"""Kontrakt v2: what the LLM returns (docs/ARCHITEKTURA_v2.md, section 5).

Standard library only.
normalize() makes any LLM output safe to walk; expand() turns the ADRES and OSOBA macros into plain fields.
"""
import re
import unicodedata

TYPES = {"TEXT_FIELD", "TEXTAREA", "NUMBER", "DATEPICKER", "RADIO", "CHECKBOX", "SELECT",
         "FILE_UPLOADER", "CONSENT_SECTION", "TEXT_BLOCK"}
VALIDATORS = {"EMAIL", "PESEL", "NIP", "REGON", "ZIP_CODE", "EPUAP", "ADE", "SERIAL_NUMBER_ID_CARD"}
APPLICANT_DATA = {"PESEL", "NIP", "REGON", "KRS", "EPUAP", "ADE", "DOWOD", "DATA_URODZENIA"}
BASE_PAGES = ("Dane", "Kontakt i zgody")
LEGACY_TYPES = {"TEXT": "TEXT_FIELD", "DATE": "DATEPICKER", "FILE": "FILE_UPLOADER"}  # old colleague prompt


def json_schema() -> dict:
    """JSON Schema of the contract for servers that constrain generation (vLLM guided decoding).
    The model then cannot return broken JSON or a type outside the lists; normalize() still runs after."""
    s, b, arr = {"type": "string"}, {"type": "boolean"}, lambda item: {"type": "array", "items": item}
    leaf = {"type": "object", "properties": {"pole": s, "wartosc": s}, "required": ["pole", "wartosc"],
            "additionalProperties": False}
    node = lambda op, item: {"type": "object", "properties": {op: item}, "required": [op],  # noqa: E731
                             "additionalProperties": False}
    cond = {"$ref": "#/$defs/warunek"}
    opt_cond = {"anyOf": [cond, {"type": "null"}]}
    field = {"type": "object", "additionalProperties": False,
             "required": ["wzorzec", "techName", "tytul", "typ", "wymagane", "walidatory", "opcje", "warunek", "linie"],
             "properties": {"wzorzec": {"enum": ["POLE", "ADRES", "OSOBA"]}, "techName": s, "tytul": s,
                            "typ": {"enum": sorted(TYPES)}, "wymagane": b,
                            "walidatory": arr({"enum": sorted(VALIDATORS)}), "opcje": arr(s),
                            "slownik": {"anyOf": [s, {"type": "null"}]}, "opis": {"anyOf": [s, {"type": "null"}]},
                            "warunek": opt_cond, "linie": arr({"type": "string", "pattern": "^L[0-9]{3,4}$"})}}
    section = {"type": "object", "additionalProperties": False,
               "required": ["tytul", "techName", "powtarzalna", "warunek", "pola"],
               "properties": {"tytul": s, "techName": s, "powtarzalna": b, "warunek": opt_cond, "pola": arr(field)}}
    decision = {"type": "object", "additionalProperties": False, "required": ["pole", "pytanie", "linie", "opcje"],
                "properties": {"pole": s, "pytanie": s, "linie": arr(s), "opcje": arr(
                    {"type": "object", "additionalProperties": False, "required": ["opcja", "pokaz"],
                     "properties": {"opcja": s, "pokaz": arr(s)}})}}
    page = {"type": "object", "additionalProperties": False, "required": ["tytul", "warunek", "sekcje"],
            "properties": {"tytul": s, "warunek": opt_cond, "sekcje": arr(section)}}
    return {
        "type": "object", "additionalProperties": False,
        "required": ["tytul", "adresat", "warianty", "dane_wnioskodawcy", "decyzje", "strony", "nowe_slowniki",
                     "pominiete"],
        "$defs": {"warunek": {"anyOf": [leaf, node("i", arr(cond)), node("lub", arr(cond)), node("nie", cond)]}},
        "properties": {
            "tytul": s,
            "adresat": {"type": "object", "additionalProperties": False, "required": ["nazwa", "rodzaj"],
                        "properties": {"nazwa": s, "rodzaj": {"enum": ["urzad", "organ"]}}},
            "warianty": {"type": "object", "additionalProperties": False, "required": ["podmiot", "pelnomocnik"],
                         "properties": {"podmiot": b, "pelnomocnik": b}},
            "dane_wnioskodawcy": arr({"enum": sorted(APPLICANT_DATA)}),
            "decyzje": arr(decision),  # before "strony": the model plans what depends on what, then lists fields
            "strony": arr(page),
            "nowe_slowniki": arr({"type": "object", "required": ["nazwa", "wartosci"],
                                  "properties": {"nazwa": s, "zastosowanie": s, "wartosci": arr(s)}}),
            "pominiete": arr({"type": "object", "required": ["linie", "powod"],
                              "properties": {"linie": arr(s), "powod": s}}),
        },
    }


def snake(text, limit=60):
    text = unicodedata.normalize("NFKD", str(text or "").replace("ł", "l").replace("Ł", "L"))
    text = text.encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")[:limit].rstrip("_") or "pole"


def leaves(cond):
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


def _field(f):
    typ = str(f.get("typ") or "TEXT_FIELD").upper()
    typ = LEGACY_TYPES.get(typ, typ)
    typ = typ if typ in TYPES else "TEXT_FIELD"
    val = [v for v in (str(v).upper() for v in f.get("walidatory") or []) if v in VALIDATORS]
    if val and typ in ("NUMBER", "TEXTAREA"):
        typ = "TEXT_FIELD"  # format validators exist only in TEXT_FIELD; a PESEL as NUMBER would lose leading zeros
    elif typ != "TEXT_FIELD":
        val = []  # other types take only REQUIRED (spec: component tables)
    return {
        "wzorzec": str(f.get("wzorzec") or "POLE").upper(),
        "techName": snake(f.get("techName") or f.get("tytul") or f.get("etykieta")),
        "tytul": str(f.get("tytul") or f.get("etykieta") or "").strip(),
        "typ": typ,
        "wymagane": f.get("wymagane") is not False,  # spec 8.4.6: every field states it; unknown = required
        "walidatory": val,
        "opcje": [str(o).strip() for o in f.get("opcje") or [] if str(o).strip()],
        "slownik": f.get("slownik") or None,
        "opis": f.get("opis") or f.get("podpowiedz") or None,
        "warunek": _cond(f.get("warunek") or f.get("pokaz_gdy")),
        "linie": list(f.get("linie") or []),
        "tooltip": f.get("tooltip") or None,
    }


# Fields that are the applicant's own data: the base already has them (twoje_dane, adres_zamieszkania, kontakt,
# signature/date are replaced by the e-service). Weaker local models generate them despite the prompt.
BASE_TITLE = re.compile(
    r"^(imie|nazwisko|imie i nazwisko|nazwisko i imie|imie nazwisko|nazwa|imie i nazwisko nazwa|nazwisko imie nazwa"
    r"|nazwa firmy|imie i nazwisko nazwa wnioskodawcy|adres|adres zamieszkania|adres siedziby|adres zamieszkania siedziba"
    r"|(nr |numer )?(telefonu?|tel)( kontaktowy| wnioskodawcy)?|(adres )?e ?mail|nr telefonu adres e ?mail"
    r"|numer telefonu adres e ?mail|telefon e ?mail|podpis.*|data|data zlozenia.*|miejscowosc i data|miejscowosc data"
    r"|pesel|nip|regon|krs)( wnioskodawcy| wnioskujacego| skladajacego)?$")
OTHER_PERSON = re.compile(r"innej|wspolmalzon|kandydat|dziec|pelnomocnik|wlasciciel|trener|podopieczn|odpowiedzialn"
                          r"|nieruchomosc|korespondenc|budynk|dzialk|klubu")


def is_base_field(f: dict) -> bool:
    title = " ".join(snake(f.get("tytul") or f.get("etykieta") or "", 200).split("_"))
    name = f.get("techName") or ""
    if OTHER_PERSON.search(title) or OTHER_PERSON.search(name):
        return False
    return bool(BASE_TITLE.match(title)) or name.startswith(("podpis", "data_zlozenia", "imie_nazwisko_wnioskodawcy",
                                                             "adres_wnioskodawcy", "telefon_wnioskodawcy"))


def normalize(raw):
    """Any LLM output (also the old 'sekcje' format) -> a contract with every key present, decisions turned into
    visibility conditions, without fields that duplicate the base (listed in `_pola_bazy`)."""
    c = _normalize(raw)
    apply_decisions(c)
    for page in c["strony"]:
        for sec in page["sekcje"]:
            where = " ".join(snake(sec["tytul"] + " " + sec["techName"], 300).split("_"))
            if OTHER_PERSON.search(where):
                continue  # e.g. "Dane kandydata": its name fields are the subject, not the applicant
            c["_pola_bazy"] += [f["tytul"] or f["techName"] for f in sec["pola"] if is_base_field(f)]
            sec["pola"] = [f for f in sec["pola"] if not is_base_field(f)]
    return c


def _normalize(raw):
    pages = raw.get("strony") or [{"tytul": raw.get("tytul") or "Szczegóły wniosku", "sekcje": raw.get("sekcje", [])}]
    adresat = raw.get("adresat") if isinstance(raw.get("adresat"), dict) else {}
    warianty = raw.get("warianty") if isinstance(raw.get("warianty"), dict) else {}
    return {
        "tytul": str(raw.get("tytul") or "").strip(),
        "adresat": {"nazwa": str(adresat.get("nazwa") or "").strip(),
                    "rodzaj": "organ" if adresat.get("rodzaj") == "organ" else "urzad"},
        "warianty": {"podmiot": bool(warianty.get("podmiot", False)),
                     "pelnomocnik": warianty.get("pelnomocnik") is not False},
        "dane_wnioskodawcy": [d for d in (str(x).upper() for x in raw.get("dane_wnioskodawcy") or [])
                              if d in APPLICANT_DATA],
        "strony": [{
            "tytul": str(p.get("tytul") or "Szczegóły wniosku").strip(),
            "warunek": _cond(p.get("warunek")),
            "sekcje": [{
                "tytul": str(s.get("tytul") or s.get("nazwa") or "").strip(),
                "techName": snake(s.get("techName") or s.get("tytul") or s.get("nazwa")),
                "powtarzalna": bool(s.get("powtarzalna")),
                "warunek": _cond(s.get("warunek")),
                "pola": [_field(f) for f in s.get("pola") or [] if isinstance(f, dict)],
            } for s in p.get("sekcje") or [] if isinstance(s, dict)],
        } for p in pages if isinstance(p, dict)],
        "decyzje": [_decision(d) for d in raw.get("decyzje") or [] if isinstance(d, dict) and d.get("pole")],
        "nowe_slowniki": [d for d in raw.get("nowe_slowniki") or [] if isinstance(d, dict) and d.get("nazwa")],
        "_pola_bazy": list(raw.get("_pola_bazy") or []),  # keeps normalize() idempotent
        "pominiete": raw.get("pominiete") or [],
    }


def _decision(d):
    opts = [{"opcja": str(o.get("opcja") or "").strip(), "pokaz": [snake(t) for t in o.get("pokaz") or []]}
            for o in d.get("opcje") or [] if isinstance(o, dict) and str(o.get("opcja") or "").strip()]
    return {"pole": snake(d["pole"]), "pytanie": str(d.get("pytanie") or "").strip(),
            "linie": list(d.get("linie") or []), "opcje": opts}


def apply_decisions(c):
    """`decyzje` -> conditions: every section, page or field listed under an option is shown only when that option
    is chosen (several options of one question = "lub", several questions = "i"). A question the model planned but
    did not list as a field is added as a RADIO at the start of the first page. Targets whose condition already
    uses that question are left alone, so applying twice changes nothing."""
    things = {snake(p["tytul"]): p for p in c["strony"]}
    for p in c["strony"]:
        for s in p["sekcje"]:
            things[s["techName"]] = s
            things.update({f["techName"]: f for f in s["pola"]})
    wanted = {}  # target name -> {question: [options]}
    for d in c["decyzje"]:
        question = things.get(d["pole"])
        if question is None and c["strony"]:
            question = _plain(d["pole"], d["pytanie"] or d["pole"], "RADIO", True, lines=d["linie"])
            question["opcje"] = [o["opcja"] for o in d["opcje"]]
            c["strony"][0]["sekcje"].insert(0, {"tytul": d["pytanie"] or "Rodzaj wniosku", "techName": f"{d['pole']}_wybor",
                                                "powtarzalna": False, "warunek": None, "pola": [question]})
            things[d["pole"]] = question
        own = {s["techName"] for p in c["strony"] for s in p["sekcje"] if question in s["pola"]}
        for o in d["opcje"]:
            for t in o["pokaz"]:
                if t != d["pole"] and t not in own and t in things:
                    wanted.setdefault(t, {}).setdefault(d["pole"], []).append(o["opcja"])
    for t, by_question in wanted.items():
        target = things[t]
        used = {leaf["pole"] for leaf in leaves(target["warunek"])}
        for q, opts in by_question.items():
            if q in used:
                continue
            new = [{"pole": q, "wartosc": o} for o in dict.fromkeys(opts)]
            target["warunek"] = both(target["warunek"], new[0] if len(new) == 1 else {"lub": new})


def _plain(tech, title, typ="TEXT_FIELD", required=False, validators=(), dictionary=None, cond=None, lines=()):
    return {"wzorzec": "POLE", "techName": tech, "tytul": title, "typ": typ, "wymagane": required,
            "walidatory": list(validators), "opcje": [], "slownik": dictionary, "opis": None,
            "warunek": cond, "linie": list(lines), "tooltip": None}


def _address(f):
    s = f["techName"]
    pl = {"pole": f"kraj_{s}", "wartosc": "Polska"}
    abroad = {"nie": pl}
    rows = [  # same fields and order as the base 'adres_zamieszkania' (reference 5)
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
    return [_plain(f"imie_{s}", "Imię", required=True, cond=f["warunek"], lines=f["linie"]),
            _plain(f"nazwisko_{s}", "Nazwisko", required=True, cond=f["warunek"], lines=f["linie"])]


MACROS = {"ADRES": _address, "OSOBA": _person}


def expand(contract):
    """Replace macro fields by their plain fields (in place) and return the contract."""
    for page in contract["strony"]:
        for sec in page["sekcje"]:
            sec["pola"] = [x for f in sec["pola"] for x in (MACROS[f["wzorzec"]](f) if f["wzorzec"] in MACROS else [f])]
    return contract


def fields(contract):
    for page in contract["strony"]:
        for sec in page["sekcje"]:
            for f in sec["pola"]:
                yield page, sec, f
