"""Polish paper rules (app/rules) and the COI compliance checks they rely on. No model, no network."""
import json

from app.domain.builder import build, validate
from app.domain.contract import json_schema, normalize
from app.llm.base import parse_json
from app.rules import Context, apply


def contract(*fields, lines="", section="Dane kandydata", **top):  # not the applicant: base guard keeps all
    raw = {"tytul": "Wniosek o test", "strony": [{"tytul": "Wniosek", "sekcje": [
        {"tytul": section, "pola": [dict({"techName": f"p{i}", "typ": "TEXT_FIELD"}, **f) for i, f in enumerate(fields)]}]}]}
    raw.update(top)
    return normalize(raw), Context.of(lines)


def run(rule, *fields, lines="", **top):
    c, ctx = contract(*fields, lines=lines, **top)
    apply(c, ctx, only={rule})
    return c, c["strony"][0]["sekcje"][0]["pola"]


def test_validator_outside_text_field_is_fixed_by_normalize():
    c, _ = contract({"tytul": "Numer", "typ": "NUMBER", "walidatory": ["PESEL"]},
                    {"tytul": "Kiedy", "typ": "DATEPICKER", "walidatory": ["EMAIL"]})
    a, b = c["strony"][0]["sekcje"][0]["pola"]
    assert (a["typ"], a["walidatory"]) == ("TEXT_FIELD", ["PESEL"])
    assert (b["typ"], b["walidatory"]) == ("DATEPICKER", [])


def test_validate_reports_format_validator_in_wrong_type_and_unreachable_page():
    c, _ = contract({"tytul": "Kwota", "typ": "NUMBER"})
    form, _, errors = build(c)
    assert errors == []
    form["tabs"][1]["questions"][0]["field"]["repeatingSectionGrid"][0]["field"].update(type="DATEPICKER",
                                                                                         validation=["EMAIL"])
    form["flow"]["links"] = form["flow"]["links"][:1]
    errors = validate(form)
    assert any("walidator formatu" in e for e in errors) and any("osiągalna" in e for e in errors)


def test_type_from_label():
    _, (email, kod_pkd, konto, data, kwota, number_id, phone) = run(
        "typ_z_etykiety", {"tytul": "Adres e-mail"}, {"tytul": "Kod PKD"}, {"tytul": "Nr rachunku bankowego"},
        {"tytul": "Data urodzenia"}, {"tytul": "Kwota nadpłaty"}, {"tytul": "PESEL", "typ": "NUMBER"},
        {"tytul": "Telefon", "walidatory": ["EMAIL"]})
    assert (email["typ"], email["walidatory"]) == ("TEXT_FIELD", ["EMAIL"])
    assert (kod_pkd["typ"], kod_pkd["walidatory"]) == ("TEXT_FIELD", [])
    assert konto["tooltip"] == "26 cyfr numeru rachunku"
    assert data["typ"] == "DATEPICKER" and kwota["typ"] == "NUMBER"
    assert (number_id["typ"], number_id["walidatory"]) == ("TEXT_FIELD", ["PESEL"])
    assert phone["walidatory"] == [] and phone["tooltip"]


def test_label_never_overrides_a_choice_or_number_into_text():
    _, (radio, number) = run("typ_z_etykiety", {"tytul": "Data", "typ": "RADIO", "opcje": ["A", "B"]},
                             {"tytul": "Uzasadnienie", "typ": "NUMBER"})
    assert radio["typ"] == "RADIO" and number["typ"] == "NUMBER"


def test_type_from_cells():
    lines = "L001 Urodzony [#2] - [#2] - [#4]\nL002 Numer [#11]\nL003 A [#11] B [#10]\nL004 Kod [#2] - [#3]"
    _, (date, pesel, two, zip_) = run("typ_z_kratek", {"tytul": "Kiedy", "linie": ["L001"]},
                                      {"tytul": "Numer", "linie": ["L002"]}, {"tytul": "X", "linie": ["L003"]},
                                      {"tytul": "Kod", "linie": ["L004"]}, lines=lines)
    assert date["typ"] == "DATEPICKER" and pesel["walidatory"] == ["PESEL"]
    assert two["walidatory"] == [] and zip_["walidatory"] == ["ZIP_CODE"]


def test_country_dictionary_and_multiple_choice():
    _, (kraj,) = run("slownik_kraje", {"tytul": "Obywatelstwo"})
    assert (kraj["typ"], kraj["slownik"]) == ("SELECT", "Kraje")
    _, (lista,) = run("wielokrotny_wybor", {"tytul": "Cel", "typ": "RADIO", "opcje": ["A", "B"], "linie": ["L002"]},
                      lines="L001 (można zaznaczyć kilka odpowiedzi)\nL002 ☐ A ☐ B")
    assert lista["typ"] == "CHECKBOX"


def test_condition_after_other_or_yes():
    _, (rodzaj, inne, czy, jesli) = run(
        "warunek_inne_tak", {"tytul": "Rodzaj", "typ": "RADIO", "opcje": ["Dom", "Inne"]}, {"tytul": "Inne:"},
        {"tytul": "Czy posiadasz psa?", "typ": "RADIO", "opcje": ["Tak", "Nie"]}, {"tytul": "Jeżeli tak, podaj rasę"})
    assert inne["warunek"] == {"pole": "p0", "wartosc": "Inne"}
    assert jesli["warunek"] == {"pole": "p2", "wartosc": "Tak"}


def test_condition_only_right_after_the_list():
    _, (rodzaj, adres, inne, jakie) = run(
        "warunek_inne_tak", {"tytul": "Rodzaj", "typ": "RADIO", "opcje": ["Dom", "Inne"]}, {"tytul": "Adres budowy"},
        {"tytul": "Inne uwagi"}, {"tytul": "Jakie dokumenty załączasz"})
    assert adres["warunek"] is None and inne["warunek"] is None and jakie["warunek"] is None
    _, (_, right_after) = run("warunek_inne_tak", {"tytul": "Rodzaj", "typ": "RADIO", "opcje": ["Dom", "Inne"]},
                              {"tytul": "Jakie dokumenty załączasz"})
    assert right_after["warunek"] is None


def test_required():
    _, (opt, cond) = run("wymagane", {"tytul": "Telefon (nieobowiązkowe)"},
                         {"tytul": "Rasa", "wymagane": False, "warunek": {"pole": "x", "wartosc": "Tak"}})
    assert opt["wymagane"] is False and cond["wymagane"] is True


def test_addressee_from_header_and_kind():
    lines = "L001 (imię i nazwisko) Wójt Gminy Elbląg\nL002 …… ul. Browarna 85\nL003 WNIOSEK"
    c, _ = run("adresat", {"tytul": "X"}, lines=lines, adresat={"nazwa": "", "rodzaj": "urzad"})
    assert c["adresat"] == {"nazwa": "Wójt Gminy Elbląg", "rodzaj": "organ"}
    c, _ = run("adresat", {"tytul": "X"}, adresat={"nazwa": "Urząd Gminy Ulan-Majorat", "rodzaj": "organ"})
    assert c["adresat"]["rodzaj"] == "urzad"


def test_titles_office_fields_duplicates_and_statements():
    c, (num, kw) = run("tytuly", {"tytul": "3.1. Nazwisko¹"}, {"tytul": "KWOTA PODATKU (NIP)"},
                       tytul="Wniosek o zwrot (Dz. U. z 2025 r. poz. 111)")
    assert (num["tytul"], kw["tytul"], c["tytul"]) == ("Nazwisko", "Kwota podatku (NIP)", "Wniosek o zwrot")
    _, pola = run("pola_urzedu", {"tytul": "Data wpływu"}, {"tytul": "Kwota"})
    assert [f["tytul"] for f in pola] == ["Kwota"]
    _, pola = run("duplikaty", {"tytul": "Kwota"}, {"tytul": "Kwota"})
    assert len(pola) == 1
    _, (st,) = run("oswiadczenia", {"tytul": "Oświadczam, że dane są prawdziwe", "typ": "CHECKBOX"})
    assert st["typ"] == "CONSENT_SECTION"


def test_truncated_answer_keeps_complete_fields():
    full = json.dumps({"tytul": "W", "strony": [{"tytul": "S", "sekcje": [{"tytul": "A", "pola": [
        {"techName": "a", "tytul": "Pierwsze"}, {"techName": "b", "tytul": "Drugie pole z długą nazwą"}]}]}]})
    cut = full[:full.index("Drugie") + 3]
    data = parse_json(cut, repair=True)
    assert [f["techName"] for f in data["strony"][0]["sekcje"][0]["pola"]] == ["a"] and data["_ucieta_odpowiedz"]


def test_decisions_become_conditions_and_missing_question_is_added():
    raw = {"decyzje": [{"pole": "rodzaj", "pytanie": "Jakiego wniosku dotyczy?", "linie": ["L1"], "opcje": [
        {"opcja": "Stypendium", "pokaz": ["osiagniecia", "dyscyplina"]}, {"opcja": "Nagroda", "pokaz": ["osiagniecia"]}]},
        {"pole": "czy_klub", "pytanie": "Czy należysz do klubu?", "linie": [], "opcje": [
            {"opcja": "Tak", "pokaz": ["dyscyplina"]}, {"opcja": "Nie", "pokaz": []}]}],
           "strony": [{"tytul": "Wniosek", "sekcje": [{"tytul": "Osiągnięcia", "techName": "osiagniecia", "pola": [
               {"techName": "czy_klub", "tytul": "Czy należysz do klubu?", "typ": "RADIO", "opcje": ["Tak", "Nie"]},
               {"techName": "dyscyplina", "tytul": "Dyscyplina"}]}]}]}
    c = normalize(raw)
    assert normalize(c)["strony"] == c["strony"]  # idempotent
    first, sec = c["strony"][0]["sekcje"]
    assert first["pola"][0]["techName"] == "rodzaj" and first["pola"][0]["opcje"] == ["Stypendium", "Nagroda"]
    assert sec["warunek"] == {"lub": [{"pole": "rodzaj", "wartosc": "Stypendium"}, {"pole": "rodzaj", "wartosc": "Nagroda"}]}
    assert sec["pola"][0]["warunek"] is None  # the question itself never depends on itself
    assert sec["pola"][1]["warunek"] == {"i": [{"pole": "rodzaj", "wartosc": "Stypendium"}, {"pole": "czy_klub", "wartosc": "Tak"}]}
    form, _, errors = build(c)
    assert errors == []
    assert "decyzje" in json_schema()["required"]

