"""Builder: contract v2 (LLM output) + form base -> COI FormDefinition JSON.

Usage: python -m app.domain.builder kontrakt.json [wynik.json]
Deterministic: the same contract always gives the same structure (only UUIDs differ).
Spec: docs/ARCHITEKTURA_v2.md, sections 4 and 6. Spec: docs/ARCHITEKTURA_v2.md in the main repository.
"""
import copy
import json
import re
import sys
import uuid
from difflib import SequenceMatcher
from pathlib import Path

from app.domain import contract as K

BASE_FILE = Path(__file__).resolve().parent.parent / "resources" / "baza_formularza.json"
INPUT_TYPES = K.TYPES - {"TEXT_BLOCK"}
DICT_TYPES = {"SELECT", "RADIO", "CHECKBOX"}
TITLE_START = ("Wniosek", "Zgłoszenie", "Deklaracja", "Oświadczenie")
ENTITY_SECTIONS = {"dane_podmiotu", "adres_siedziby", "potwierdzenie_ze_jestes_reprezentantem",
                   "oplata_skarbowa_pelnomocnictwo_2"}
PROXY_SECTIONS = {"dane_innej_osoby", "adres_zamieszkania_osoby_ktorej_jestes_pelnomocnikiem",
                  "oplata_skarbowa_pelnomocnictwo_1"}
CHOICE = "wybierz_w_czyim_imieniu_wnioskujesz"
ENTITY_OPTION, PROXY_OPTION = "Osoby prawnej lub jednostki organizacyjnej", "Innej osoby"
# Standardy: obligatoriness and validators the references leave empty (spec 8.3)
BASE_VALIDATION = {"imie": ["REQUIRED"], "nazwisko": ["REQUIRED"], "numer_pesel": ["REQUIRED", "PESEL"],
                   "nazwa": ["REQUIRED"], "nip": ["REQUIRED", "NIP"], "regon": ["REGON"],
                   "kraj": ["REQUIRED"], "numer_budynku": ["REQUIRED"], "kod_pocztowy": ["REQUIRED", "ZIP_CODE"],
                   "imie_inna_osoba": ["REQUIRED"], "nazwisko_inna_osoba": ["REQUIRED"],
                   "kraj_inna_osoba": ["REQUIRED"], "numer_budynku_inna_osoba": ["REQUIRED"],
                   "kod_pocztowy_inna_osoba": ["REQUIRED", "ZIP_CODE"], "kraj_siedziba": ["REQUIRED"],
                   "numer_budynku_siedziba": ["REQUIRED"], "kod_pocztowy_siedziba": ["REQUIRED", "ZIP_CODE"],
                   "plik_dokumentu_z_pelnomocnictwem": ["REQUIRED"], "plik_z_odpisem_danych": ["REQUIRED"],
                   "adres_e_mail": ["EMAIL"], "wybierz_w_czyim_imieniu_wnioskujesz": ["REQUIRED"],
                   "wybierz_jako_kto_wnioskujesz": ["REQUIRED"], "zgoda": ["REQUIRED"]}
APPLICANT_FIELDS = {  # dane_wnioskodawcy -> (base section, field)
    "NIP": None, "PESEL": None,  # already in the base (dane_podmiotu.nip, twoje_dane.numer_pesel)
    "REGON": ("dane_podmiotu", {"type": "TEXT_FIELD", "techName": "regon", "title": "REGON", "validation": ["REGON"]}),
    "KRS": ("dane_podmiotu", {"type": "TEXT_FIELD", "techName": "numer_krs", "title": "Numer KRS",
                              "tooltipMessage": "Wpisz 10 cyfr."}),
    "EPUAP": ("dane_do_kontaktu", {"type": "TEXT_FIELD", "techName": "adres_skrzynki_epuap",
                                   "title": "Adres skrzynki ePUAP", "validation": ["EPUAP"]}),
    "ADE": ("dane_do_kontaktu", {"type": "TEXT_FIELD", "techName": "adres_do_e_doreczen",
                                 "title": "Adres do e-Doręczeń", "validation": ["ADE"]}),
    "DOWOD": ("twoje_dane", {"type": "TEXT_FIELD", "techName": "seria_i_numer_dowodu",
                             "title": "Seria i numer dowodu osobistego",
                             "validation": ["REQUIRED", "SERIAL_NUMBER_ID_CARD"]}),
    "DATA_URODZENIA": ("twoje_dane", {"type": "DATEPICKER", "techName": "data_urodzenia", "title": "Data urodzenia",
                                      "readOnly": True, "defaultValue": "PESEL.birthDate",
                                      "defaultValueType": "EXTERNAL_SYSTEM"}),
}


def html(text):
    return f"<html><head></head><body><p>{text}</p></body></html>" if text else ""


def new_field(**kw):
    f = {"id": None, "type": "TEXT_FIELD", "techName": "", "title": "", "description": "", "options": [],
         "validation": [], "readOnly": False, "repeatingSectionGrid": [], "states": [], "questionOrder": 0,
         "tooltipMessage": ""}
    f.update(kw)
    return f


def children(section):
    return section["field"]["repeatingSectionGrid"]


class Builder:
    def __init__(self, contract, base):
        self.c = K.expand(K.normalize(contract))
        self.base = base
        self.flags = [f"Pominięto pole z danymi wnioskodawcy (są w stałej części): {t}" for t in self.c["_pola_bazy"]]
        self.where = {}  # final techName -> (tab, section question, repeating?)
        self.names = {}  # contract techName -> final techName (subject fields)
        self.options = {}  # final techName -> option names (None = dictionary, any value)

    def flag(self, msg):
        self.flags.append(msg)

    # --- base -----------------------------------------------------------------------------------
    def base_sections(self):
        w, out = self.c["warianty"], {}
        for s in self.base["sections"]:
            name = s["techName"]
            if (name in ENTITY_SECTIONS and not w["podmiot"]) or (name in PROXY_SECTIONS and not w["pelnomocnik"]):
                continue
            if name == "pelnomocnictwo" and not (w["podmiot"] or w["pelnomocnik"]):
                continue
            q = copy.deepcopy(s["question"])
            out.setdefault(s["page"], []).append(q)
        for qs in out.values():
            for q in qs:
                for ch in children(q):
                    f = ch["field"]
                    f["validation"] = BASE_VALIDATION.get(f["techName"], f["validation"])
        self.applicant_choice(out["Dane"])
        self.addressee(out["Dane"])
        for code in self.c["dane_wnioskodawcy"]:
            spec = APPLICANT_FIELDS[code]
            sec = spec and next((q for qs in out.values() for q in qs if q["field"]["techName"] == spec[0]), None)
            if sec and spec[1]["techName"] not in {ch["field"]["techName"] for ch in children(sec)}:
                children(sec).append({"x": 0, "y": 0, "rows": 1, "cols": 3, "field": new_field(**copy.deepcopy(spec[1]))})
        return out

    def applicant_choice(self, dane):
        w = self.c["warianty"]
        sec = next(q for q in dane if q["field"]["techName"] == "podmiot_wniosku")
        if not (w["podmiot"] or w["pelnomocnik"]):  # only "Swoim" left: no choice to make
            dane.remove(sec)
            return
        grid = children(sec)
        choice = next(ch["field"] for ch in grid if ch["field"]["techName"] == CHOICE)
        drop = ({ENTITY_OPTION} if not w["podmiot"] else set()) | ({PROXY_OPTION} if not w["pelnomocnik"] else set())
        choice["options"] = [o for o in choice["options"] if o["name"] not in drop]
        if not w["podmiot"]:  # "jako kto" only exists for legal entities
            grid[:] = [ch for ch in grid if ch["field"]["techName"] != "wybierz_jako_kto_wnioskujesz"]
            proxy = next((q for q in dane if q["field"]["techName"] == "pelnomocnictwo"), None)
            if proxy:  # its SHOW also mentions the entity branch; keep only "Innej osoby"
                proxy["field"]["states"] = [{"type": "SHOW", "query": self.jsonata(
                    {"pole": CHOICE, "wartosc": PROXY_OPTION}, raw=True)}]

    def addressee(self, dane):
        a = self.c["adresat"]
        sec = next(q for q in dane if q["field"]["techName"] == "urzad_do_ktorego_jest_skladany_wniosek")
        field = children(sec)[0]["field"]
        field["defaultValue"] = a["nazwa"] or "Uzupełnij nazwę urzędu"
        if not a["nazwa"]:
            self.flag("Brak adresata na papierze: uzupełnij nazwę urzędu.")
        if a["rodzaj"] == "organ":  # reference 1
            sec["field"].update(techName="organ_do_ktorego_jest_skladany_wniosek",
                                title="Organ, do którego jest składany wniosek")
            field.update(techName="nazwa_organu", title="Nazwa organu")

    # --- subject ----------------------------------------------------------------------------------
    def unique(self, name, taken):
        out, i = name, 2
        while out in taken:
            out, i = f"{name}_{i}", i + 1
        taken.add(out)
        return out

    def subject_field(self, f, taken):
        typ, opts = f["typ"], f["opcje"]
        if typ == "RADIO" and len(opts) > 5:
            typ = "SELECT"  # Standard: more than 5 options -> list
        name = self.unique(f["techName"], taken)
        self.names.setdefault(f["techName"], name)
        val = (["REQUIRED"] if f["wymagane"] and typ != "TEXT_BLOCK" else []) + f["walidatory"]
        field = new_field(type=typ, techName=name, title=f["tytul"], validation=val, tooltipMessage=f["tooltip"] or "",
                          description=html(f["opis"]) if typ != "TEXT_BLOCK" else html(f["opis"] or f["tytul"]))
        if typ == "TEXT_BLOCK":
            field["title"] = ""
        elif typ == "CONSENT_SECTION":
            field["title"] = ""
            texts = opts or [f["tytul"]]
            field["consentSectionConfig"] = {"consents": [{"description": html(t), "required": f["wymagane"]}
                                                          for t in texts], "files": []}
        elif typ in DICT_TYPES:
            dictionary = f["slownik"]
            if dictionary:
                field["dictionaryName"] = dictionary
                field["multiLevelTree"] = dictionary.startswith("TERYT")
                if dictionary == "Kraje":
                    field["defaultValue"] = ["Polska"]
            else:
                field["options"] = [{"id": o, "name": o, "techName": K.snake(o)} for o in opts]
                if not opts:
                    self.flag(f"Pole wyboru '{f['tytul']}' bez opcji i bez słownika.")
            if typ == "SELECT":
                field["searchEnabled"] = bool(dictionary) or len(opts) > 15
        elif f["slownik"]:
            self.flag(f"Słownik {f['slownik']} przy polu typu {typ} ('{f['tytul']}') pominięty.")
        self.options[name] = None if (typ in DICT_TYPES and f["slownik"]) else opts
        return field

    def subject(self, pages):
        taken = {ch["field"]["techName"] for qs in pages.values() for q in qs
                 for ch in [q] + children(q)}
        tabs, conds = [], []
        for page in self.c["strony"]:
            sections = []
            for s in page["sekcje"]:
                if not s["pola"]:
                    continue
                kind = "REPEATING_SECTION" if s["powtarzalna"] else "SECTION"
                sec = {"x": 0, "y": 0, "rows": 1, "cols": 3,
                       "field": new_field(type=kind, techName=self.unique(s["techName"], taken), title=s["tytul"],
                                          description=html(""))}
                for f in s["pola"]:
                    field = self.subject_field(f, taken)
                    children(sec).append({"x": 0, "y": 0, "rows": 1, "cols": 3, "field": field})
                    conds.append((field, f["warunek"]))
                conds.append((sec["field"], K.both(s["warunek"], page["warunek"])))
                sections.append(sec)
            if not sections:
                continue
            if page["tytul"] in K.BASE_PAGES:  # sections that belong on a base page
                target = pages[page["tytul"]]
                at = len(target) - 1 if page["tytul"] == "Kontakt i zgody" else len(target)  # before consent
                target[at:at] = sections
            else:
                tabs.append((page["tytul"], sections))
        return tabs, conds

    # --- conditions -------------------------------------------------------------------------------
    def jsonata(self, cond, top=True, raw=False):
        if "pole" in cond:
            name = cond["pole"] if raw else self.names.get(cond["pole"], cond["pole"])
            tab, sec, repeating = self.where.get(name, ("Dane", "podmiot_wniosku", False)) if raw else self.where[name]
            value = cond["wartosc"].replace('"', '\\"')
            if repeating:
                return (f'("{value}" in tabs[\'{tab}\' in title].questions[\'{sec}\' in field.techName]'
                        f'.field.__userAnswer[$repeatingSectionIndex][\'{name}\' in fieldTechName].answers)')
            return (f'("{value}" in tabs[\'{tab}\' in title].questions[\'{sec}\' in field.techName]'
                    f'.field.repeatingSectionGrid[\'{name}\' in field.techName].field.__userAnswer)')
        if "nie" in cond:
            inner = self.jsonata(cond["nie"], top=True, raw=raw)
            return "$not" + inner if inner.startswith("(") and "pole" in cond["nie"] else f"$not({inner})"
        op = "and" if "i" in cond else "or"
        body = f" {op} ".join(self.jsonata(x, top=False, raw=raw) for x in cond.get("i", []) + cond.get("lub", []))
        return body if top else f"( {body} )"

    def resolve(self, cond, owner_section):
        """Check every leaf; fix near-miss option names; None when the condition cannot work."""
        if not cond:
            return None
        for leaf in K.leaves(cond):
            name = self.names.get(leaf["pole"], leaf["pole"])
            if name not in self.where:
                self.flag(f"Warunek odwołuje się do nieznanego pola '{leaf['pole']}': usunięty.")
                return None
            tab, sec, repeating = self.where[name]
            if repeating and sec != owner_section:
                self.flag(f"Warunek na polu '{name}' z sekcji powtarzającej się spoza tej sekcji: usunięty.")
                return None
            opts = self.options.get(name)
            if opts and leaf["wartosc"] not in opts:
                best = max(opts, key=lambda o: SequenceMatcher(None, o.lower(), leaf["wartosc"].lower()).ratio())
                if SequenceMatcher(None, best.lower(), leaf["wartosc"].lower()).ratio() < 0.8:
                    self.flag(f"Warunek: '{leaf['wartosc']}' nie jest opcją pola '{name}': usunięty.")
                    return None
                self.flag(f"Warunek: '{leaf['wartosc']}' poprawione na opcję '{best}'.")
                leaf["wartosc"] = best
        return cond

    # --- assembly ---------------------------------------------------------------------------------
    def build(self):
        c = self.c
        pages = self.base_sections()
        tabs, conds = self.subject(pages)
        order = [("Dane", "MAIN", pages["Dane"])] + [(t, "MAIN", s) for t, s in tabs] + \
                [("Kontakt i zgody", "MAIN", pages["Kontakt i zgody"]),
                 ("Strona końcowa", "FINISH", pages["Strona końcowa"])]
        seen, form_tabs = set(), []
        for title, typ, questions in order:
            base_title, i = title, 2
            while title in seen:  # JSONata finds pages by title
                title, i = f"{base_title} ({i})", i + 1
            seen.add(title)
            form_tabs.append({"id": str(uuid.uuid4()), "title": title, "type": typ, "questions": questions})
        for tab in form_tabs:
            for q in tab["questions"]:
                for ch in children(q):
                    self.where[ch["field"]["techName"]] = (tab["title"], q["field"]["techName"],
                                                           q["field"]["type"] == "REPEATING_SECTION")
        owners = {ch["field"]["techName"]: q["field"]["techName"]
                  for tab in form_tabs for q in tab["questions"] for ch in children(q)}
        for field, cond in conds:
            cond = self.resolve(cond, owners.get(field["techName"], field["techName"]))
            if cond:
                field["states"] = [{"type": "SHOW", "query": self.jsonata(cond)}]
        self.layout(form_tabs)
        title = c["tytul"] or "Wniosek"
        if title.isupper():  # papers shout their titles
            title = title.capitalize()
        if not title.startswith(TITLE_START):
            self.flag(f"Tytuł '{title}' nie zaczyna się od Wniosek/Zgłoszenie/Deklaracja/Oświadczenie.")
        links = [{"id": str(uuid.uuid4()), "source": a["id"], "target": b["id"]}
                 for a, b in zip(form_tabs, form_tabs[1:])]
        return {"title": title[:255], "tabs": form_tabs, "flow": {"links": links}}

    @staticmethod
    def layout(tabs):
        for tab in tabs:
            y = 0
            for q in tab["questions"]:
                kids = children(q)
                q.update(x=0, y=y, rows=len(kids) + 1, cols=3)
                q["field"]["questionOrder"] = y
                q["field"]["id"] = q["field"]["id"] or str(uuid.uuid4())
                for i, ch in enumerate(kids):
                    ch.update(x=0, y=i, rows=1, cols=3)
                    ch["field"].update(questionOrder=i, id=ch["field"]["id"] or str(uuid.uuid4()))
                y += len(kids) + 1


QUERY_REF = re.compile(r"tabs\['(.+?)' in title\]\.questions\['(.+?)' in field\.techName\]\.field\."
                       r"(?:repeatingSectionGrid\['(.+?)' in field\.techName\]|__userAnswer\[\$repeatingSectionIndex\]\['(.+?)' in fieldTechName\])")


def validate(form):
    """Rules of spec 8.4 plus: every JSONata reference points at an existing page, section and field."""
    errors, tabs = [], form["tabs"]
    if len(tabs) < 2 or tabs[-1]["type"] != "FINISH" or any(t["type"] != "MAIN" for t in tabs[:-1]):
        errors.append("strony: co najmniej 2, ostatnia FINISH, pozostałe MAIN")
    titles = [t["title"] for t in tabs]
    if len(set(titles)) != len(titles):
        errors.append("tytuły stron nie są unikalne")
    if not form["title"].startswith(TITLE_START) or len(form["title"]) > 255:
        errors.append("tytuł formularza")
    index, names = {}, []
    for t in tabs:
        if not any(children(q) for q in t["questions"]):
            errors.append(f"strona '{t['title']}' bez pól")
        for q in t["questions"]:
            f = q["field"]
            names.append(f["techName"])
            if f["type"] not in ("SECTION", "REPEATING_SECTION"):
                errors.append(f"pole '{f['techName']}' poza sekcją")
            for ch in children(q):
                g = ch["field"]
                names.append(g["techName"])
                index[(t["title"], f["techName"], g["techName"])] = True
                if g["type"] in ("SECTION", "REPEATING_SECTION"):
                    errors.append(f"sekcja '{g['techName']}' zagnieżdżona w sekcji")
                if g.get("dictionaryName") and g["type"] not in DICT_TYPES:
                    errors.append(f"słownik w polu typu {g['type']}: '{g['techName']}'")
                if t["type"] == "FINISH" and g["type"] in INPUT_TYPES:
                    errors.append(f"pole do wypełnienia na stronie końcowej: '{g['techName']}'")
                if not g["id"]:
                    errors.append(f"brak id: '{g['techName']}'")
                if g["type"] != "TEXT_FIELD" and set(g["validation"]) - {"REQUIRED"}:
                    errors.append(f"walidator formatu w polu typu {g['type']}: '{g['techName']}'")
    reached, links = {tabs[0]["id"]} if tabs else set(), form["flow"]["links"]
    while new := {ln["target"] for ln in links if ln["source"] in reached} - reached:
        reached |= new
    if len(reached) < len(tabs):
        errors.append("flow: nie każda strona jest osiągalna z pierwszej")
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        errors.append(f"powtórzone techName: {sorted(dupes)}")
    for t in tabs:
        for q in t["questions"]:
            for f in [q["field"]] + [ch["field"] for ch in children(q)]:
                for st in f["states"]:
                    for tab, sec, a, b in QUERY_REF.findall(st["query"]):
                        if (tab, sec, a or b) not in index:
                            errors.append(f"JSONata w '{f['techName']}' wskazuje nieistniejące pole {tab}/{sec}/{a or b}")
    return errors


def build(contract, base=None):
    """Returns (FormDefinition, flags for the official to review, validation errors)."""
    b = Builder(contract, base or json.loads(BASE_FILE.read_text(encoding="utf-8")))
    form = b.build()
    return form, b.flags, validate(form)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # Polish messages on a Windows console
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    form, flags, errors = build(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
    out = json.dumps(form, ensure_ascii=False, indent=2)
    if len(sys.argv) == 3:
        Path(sys.argv[2]).write_text(out + "\n", encoding="utf-8")
    else:
        print(out)
    for msg in flags:
        print("DO WERYFIKACJI:", msg, file=sys.stderr)
    for msg in errors:
        print("BŁĄD:", msg, file=sys.stderr)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
