"""Contract -> COI form: conditions as JSONata, macros, dictionaries, and the checks on what the model returned."""
from app.domain import builder
from app.domain.builder import build
from app.domain.contract import APPLICANT_DATA, normalize

SECTION = "Dane kandydata"  # not the applicant: the base-field guard keeps every field


def contract(*fields, powtarzalna=False, **top):
    raw = {"tytul": "Wniosek o test", "adresat": {"nazwa": "Urząd Gminy", "rodzaj": "urzad"},
           "strony": [{"tytul": "Wniosek", "sekcje": [{"tytul": SECTION, "powtarzalna": powtarzalna, "pola": [
               dict({"techName": f"p{i}", "tytul": f"Pole {i}", "typ": "TEXT_FIELD"}, **f)
               for i, f in enumerate(fields)]}]}]}
    raw.update(top)
    return raw


def built(*fields, **top):
    form, flags, errors = build(contract(*fields, **top))
    assert errors == [], errors
    section = form["tabs"][1]["questions"][0]["field"]
    return {ch["field"]["techName"]: ch["field"] for ch in section["repeatingSectionGrid"]}, flags, section


RADIO = {"typ": "RADIO", "opcje": ["Tak", "Nie"]}
PATH = "tabs['Wniosek' in title].questions['dane_kandydata' in field.techName].field"


def query(field):
    return field["states"][0]["query"]


def leaf(value):
    return f'("{value}" in {PATH}.repeatingSectionGrid[\'p0\' in field.techName].field.__userAnswer)'


def test_condition_becomes_jsonata():
    yes, no = {"pole": "p0", "wartosc": "Tak"}, {"pole": "p0", "wartosc": "Nie"}
    f, flags, _ = built(RADIO, {"warunek": yes}, {"warunek": {"nie": yes}},
                        {"warunek": {"i": [yes, {"lub": [no, yes]}]}})
    assert query(f["p1"]) == leaf("Tak")
    assert query(f["p2"]) == "$not" + leaf("Tak")
    assert query(f["p3"]) == f'{leaf("Tak")} and ( {leaf("Nie")} or {leaf("Tak")} )'
    assert flags == []


def test_condition_inside_a_repeating_section_uses_the_row_index():
    f, _, section = built(RADIO, {"warunek": {"pole": "p0", "wartosc": "Tak"}}, powtarzalna=True)
    assert section["type"] == "REPEATING_SECTION"
    assert query(f["p1"]) == f'("Tak" in {PATH}.__userAnswer[$repeatingSectionIndex][\'p0\' in fieldTechName].answers)'


def test_conditions_that_cannot_work_are_removed_and_flagged():
    f, flags, _ = built(
        RADIO,
        {"warunek": {"pole": "nie_ma", "wartosc": "Tak"}},                # unknown field
        {"warunek": {"pole": "p0", "wartosc": "Może"}},                   # not an option
        {"warunek": {"pole": "p0", "wartosc": "tak"}},                    # near miss: corrected
        {"warunek": {"pole": "p5", "wartosc": "Burek"}},                  # the target is not a choice field
        {"tytul": "Imię psa"},
        dict(RADIO, warunek={"pole": "p6", "wartosc": "Tak"}))            # depends on itself
    assert [bool(f[n]["states"]) for n in ("p1", "p2", "p3", "p4", "p6")] == [False, False, True, False, False]
    assert '"Tak" in' in query(f["p3"])
    assert len(flags) == 5


def test_section_cannot_depend_on_its_own_field():
    raw = contract(RADIO)
    raw["strony"][0]["sekcje"][0]["warunek"] = {"pole": "p0", "wartosc": "Tak"}
    form, flags, errors = build(raw)
    assert form["tabs"][1]["questions"][0]["field"]["states"] == [] and len(flags) == 1 and errors == []


def test_page_title_with_an_apostrophe_keeps_jsonata_valid():
    raw = contract(RADIO, {"warunek": {"pole": "p0", "wartosc": "Tak"}})
    raw["strony"][0]["tytul"] = "Pies 'agresywny'"
    form, _, errors = build(raw)
    assert form["tabs"][1]["title"] == "Pies ’agresywny’" and errors == []


def test_dictionary_the_generator_does_not_have_becomes_options():
    f, flags, _ = built({"typ": "SELECT", "slownik": "DYSCYPLINY", "opcje": []},
                        {"typ": "SELECT", "slownik": "Kraje"},
                        nowe_slowniki=[{"nazwa": "DYSCYPLINY", "wartosci": ["Judo", "Szermierka"]}])
    assert "dictionaryName" not in f["p0"] and [o["name"] for o in f["p0"]["options"]] == ["Judo", "Szermierka"]
    assert f["p1"]["dictionaryName"] == "Kraje" and f["p1"]["options"] == []
    assert len(flags) == 1 and "DYSCYPLINY" in flags[0]


def test_macros_expand_to_plain_fields():
    f, _, _ = built({"wzorzec": "ADRES", "techName": "nieruchomosc", "tytul": "Adres nieruchomości"},
                    {"wzorzec": "OSOBA", "techName": "wspolmalzonek", "tytul": "Współmałżonek"})
    assert f["kraj_nieruchomosc"]["dictionaryName"] == "Kraje"
    assert f["kod_pocztowy_nieruchomosc"]["validation"] == ["REQUIRED", "ZIP_CODE"]
    assert "$not" in query(f["miejscowosc_nieruchomosc_zagranica"])  # shown when the country is not Poland
    assert f["imie_wspolmalzonek"]["validation"] == f["nazwisko_wspolmalzonek"]["validation"] == ["REQUIRED"]


def test_normalize_is_idempotent_and_the_base_guard_runs_once():
    """The builder normalizes again after the rules cleaned the titles: nothing more may disappear then."""
    raw = contract({"tytul": "3. Data"}, {"tytul": "Nazwisko"}, {"tytul": "Data zdarzenia"})
    raw["strony"][0]["sekcje"][0]["tytul"] = "Zdarzenie"
    once = normalize(raw)
    assert once["_pola_bazy"] == ["3. Data", "Nazwisko"]  # paper numbering does not hide a base field
    once["strony"][0]["sekcje"][0]["pola"][0]["tytul"] = "Data"  # what the 'tytuly' rule could leave behind
    twice = normalize(once)
    assert twice == once and [f["tytul"] for f in twice["strony"][0]["sekcje"][0]["pola"]] == ["Data"]
    _, flags, _ = build(once)
    assert len([m for m in flags if "Pominięto" in m]) == 2


def test_every_applicant_datum_of_the_contract_is_known_to_the_builder():
    assert set(builder.APPLICANT_FIELDS) == APPLICANT_DATA
