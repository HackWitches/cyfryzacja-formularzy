"""Polish paper rules (app/rules) and the COI compliance checks they rely on, without a model or network."""

import json

from app.domain.builder import build, validate
from app.domain.contract import normalize
from app.llm.base import parse_json
from app.rules import Context, apply


def contract(*fields, lines="", section="Dane kandydata", **top):  # not the applicant: base guard keeps all
    raw = {
        "tytul": "Wniosek o test",
        "strony": [
            {
                "tytul": "Wniosek",
                "sekcje": [
                    {
                        "tytul": section,
                        "pola": [dict({"techName": f"p{i}", "typ": "TEXT_FIELD"}, **f) for i, f in enumerate(fields)],
                    }
                ],
            }
        ],
    }
    raw.update(top)
    return normalize(raw), Context.of(lines)


def run(rule, *fields, lines="", **top):
    c, ctx = contract(*fields, lines=lines, **top)
    apply(c, ctx, only={rule})
    return c, c["strony"][0]["sekcje"][0]["pola"]


def test_validator_outside_text_field_is_fixed_by_normalize():
    c, _ = contract(
        {"tytul": "Numer", "typ": "NUMBER", "walidatory": ["PESEL"]},
        {"tytul": "Kiedy", "typ": "DATEPICKER", "walidatory": ["EMAIL"]},
    )
    a, b = c["strony"][0]["sekcje"][0]["pola"]
    assert (a["typ"], a["walidatory"]) == ("TEXT_FIELD", ["PESEL"])
    assert (b["typ"], b["walidatory"]) == ("DATEPICKER", [])


def test_validate_reports_format_validator_in_wrong_type_and_unreachable_page():
    c, _ = contract({"tytul": "Kwota", "typ": "NUMBER"})
    form, _, errors = build(c)
    assert errors == []
    form["tabs"][1]["questions"][0]["field"]["repeatingSectionGrid"][0]["field"].update(
        type="DATEPICKER", validation=["EMAIL"]
    )
    form["flow"]["links"] = form["flow"]["links"][:1]
    errors = validate(form)
    assert any("walidator formatu" in e for e in errors) and any("osiągalna" in e for e in errors)


def test_type_from_label():
    _, (email, kod_pkd, konto, data, kwota, number_id, phone) = run(
        "type_from_label",
        {"tytul": "Adres e-mail"},
        {"tytul": "Kod PKD"},
        {"tytul": "Nr rachunku bankowego"},
        {"tytul": "Data urodzenia"},
        {"tytul": "Kwota nadpłaty"},
        {"tytul": "PESEL", "typ": "NUMBER"},
        {"tytul": "Telefon", "walidatory": ["EMAIL"]},
    )
    assert (email["typ"], email["walidatory"]) == ("TEXT_FIELD", ["EMAIL"])
    assert (kod_pkd["typ"], kod_pkd["walidatory"]) == ("TEXT_FIELD", [])
    assert konto["tooltip"] == "26 cyfr numeru rachunku"
    assert data["typ"] == "DATEPICKER" and kwota["typ"] == "NUMBER"
    assert (number_id["typ"], number_id["walidatory"]) == ("TEXT_FIELD", ["PESEL"])
    assert phone["walidatory"] == [] and phone["tooltip"]


def test_label_never_overrides_a_choice_or_number_into_text():
    _, (radio, number) = run(
        "type_from_label",
        {"tytul": "Data", "typ": "RADIO", "opcje": ["A", "B"]},
        {"tytul": "Uzasadnienie", "typ": "NUMBER"},
    )
    assert radio["typ"] == "RADIO" and number["typ"] == "NUMBER"


def test_type_from_cells():
    lines = "L001 Urodzony [#2] - [#2] - [#4]\nL002 Numer [#11]\nL003 A [#11] B [#10]\nL004 Kod [#2] - [#3]"
    _, (date, pesel, two, zip_) = run(
        "type_from_cells",
        {"tytul": "Kiedy", "linie": ["L001"]},
        {"tytul": "Numer", "linie": ["L002"]},
        {"tytul": "X", "linie": ["L003"]},
        {"tytul": "Kod", "linie": ["L004"]},
        lines=lines,
    )
    assert date["typ"] == "DATEPICKER" and pesel["walidatory"] == ["PESEL"]
    assert two["walidatory"] == [] and zip_["walidatory"] == ["ZIP_CODE"]


def test_country_dictionary_and_multiple_choice():
    _, (kraj,) = run("country_dictionary", {"tytul": "Obywatelstwo"})
    assert (kraj["typ"], kraj["slownik"]) == ("SELECT", "Kraje")
    _, (lista,) = run(
        "multiple_choice",
        {"tytul": "Cel", "typ": "RADIO", "opcje": ["A", "B"], "linie": ["L002"]},
        lines="L001 (można zaznaczyć kilka odpowiedzi)\nL002 ☐ A ☐ B",
    )
    assert lista["typ"] == "CHECKBOX"


def test_condition_after_other_or_yes():
    _, (rodzaj, inne, czy, jesli) = run(
        "condition_other_or_yes",
        {"tytul": "Rodzaj", "typ": "RADIO", "opcje": ["Dom", "Inne"]},
        {"tytul": "Inne:"},
        {"tytul": "Czy posiadasz psa?", "typ": "RADIO", "opcje": ["Tak", "Nie"]},
        {"tytul": "Jeżeli tak, podaj rasę"},
    )
    assert inne["warunek"] == {"pole": "p0", "wartosc": "Inne"}
    assert jesli["warunek"] == {"pole": "p2", "wartosc": "Tak"}


def test_condition_only_right_after_the_list():
    _, (rodzaj, adres, inne, jakie) = run(
        "condition_other_or_yes",
        {"tytul": "Rodzaj", "typ": "RADIO", "opcje": ["Dom", "Inne"]},
        {"tytul": "Adres budowy"},
        {"tytul": "Inne uwagi"},
        {"tytul": "Jakie dokumenty załączasz"},
    )
    assert adres["warunek"] is None and inne["warunek"] is None and jakie["warunek"] is None
    _, (_, right_after) = run(
        "condition_other_or_yes",
        {"tytul": "Rodzaj", "typ": "RADIO", "opcje": ["Dom", "Inne"]},
        {"tytul": "Jakie dokumenty załączasz"},
    )
    assert right_after["warunek"] is None


def test_required():
    _, (opt, cond) = run(
        "required",
        {"tytul": "Telefon (nieobowiązkowe)"},
        {"tytul": "Rasa", "wymagane": False, "warunek": {"pole": "x", "wartosc": "Tak"}},
    )
    assert opt["wymagane"] is False and cond["wymagane"] is True


def test_addressee_from_header_and_kind():
    lines = "L001 (imię i nazwisko) Wójt Gminy Elbląg\nL002 …… ul. Browarna 85\nL003 WNIOSEK"
    c, _ = run("addressee", {"tytul": "X"}, lines=lines, adresat={"nazwa": "", "rodzaj": "urzad"})
    assert c["adresat"] == {"nazwa": "Wójt Gminy Elbląg", "rodzaj": "organ"}
    c, _ = run("addressee", {"tytul": "X"}, adresat={"nazwa": "Urząd Gminy Ulan-Majorat", "rodzaj": "organ"})
    assert c["adresat"]["rodzaj"] == "urzad"


def test_titles_office_fields_duplicates_and_statements():
    c, (num, kw) = run(
        "clean_titles",
        {"tytul": "3.1. Nazwisko¹"},
        {"tytul": "KWOTA PODATKU (NIP)"},
        tytul="Wniosek o zwrot (Dz. U. z 2025 r. poz. 111)",
    )
    assert (num["tytul"], kw["tytul"], c["tytul"]) == ("Nazwisko", "Kwota podatku (NIP)", "Wniosek o zwrot")
    _, pola = run("drop_office_fields", {"tytul": "Data wpływu"}, {"tytul": "Kwota"})
    assert [f["tytul"] for f in pola] == ["Kwota"]
    _, pola = run("drop_duplicates", {"tytul": "Kwota"}, {"tytul": "Kwota"})
    assert len(pola) == 1
    _, (st,) = run("statements_to_consents", {"tytul": "Oświadczam, że dane są prawdziwe", "typ": "CHECKBOX"})
    assert st["typ"] == "CONSENT_SECTION"


def test_truncated_answer_keeps_complete_fields():
    full = json.dumps(
        {
            "tytul": "W",
            "strony": [
                {
                    "tytul": "S",
                    "sekcje": [
                        {
                            "tytul": "A",
                            "pola": [
                                {"techName": "a", "tytul": "Pierwsze"},
                                {"techName": "b", "tytul": "Drugie pole z długą nazwą"},
                            ],
                        }
                    ],
                }
            ],
        }
    )
    cut = full[: full.index("Drugie") + 3]
    data = parse_json(cut, repair=True)
    assert [f["techName"] for f in data["strony"][0]["sekcje"][0]["pola"]] == ["a"] and data["_ucieta_odpowiedz"]


def test_label_with_two_meanings_or_a_place_keeps_the_models_type():
    _, pola = run(
        "type_from_label",
        {"tytul": "Kraj urodzenia"},
        {"tytul": "Województwo urodzenia"},
        {"tytul": "Data i kwota wpłaty"},
        {"tytul": "Rodzaj sztuki walki"},
        {"tytul": "Liczba sztuk"},
    )
    assert [f["typ"] for f in pola] == ["TEXT_FIELD"] * 4 + ["NUMBER"]
    c, (kraj,) = run("country_dictionary", {"tytul": "Kraj urodzenia"})
    assert apply(c, Context.of("")) is not None and (kraj["typ"], kraj["slownik"]) == ("SELECT", "Kraje")  # all rules


def test_title_cleaning_keeps_units_and_counts():
    _, pola = run(
        "clean_titles",
        {"tytul": "Powierzchnia (m2)"},
        {"tytul": "Stawka (zł/m2)"},
        {"tytul": "2 fotografie"},
        {"tytul": "Nazwisko1)"},
        {"tytul": "B.1.2 Rodzaj budynku"},
        {"tytul": "4) Cel²"},
    )
    assert [f["tytul"] for f in pola] == [
        "Powierzchnia (m2)",
        "Stawka (zł/m2)",
        "2 fotografie",
        "Nazwisko",
        "Rodzaj budynku",
        "Cel",
    ]


def test_optional_note_in_the_title_survives_title_cleaning():
    """An optional-field note in brackets survives 'clean_titles' and reaches the 'required' rule."""
    c, pola = contract(
        {"tytul": "Telefon (opcjonalnie)"},
        {"tytul": "2. Numer lokalu (wypełnić, jeśli dotyczy)"},
        {"tytul": "Cel (zaznaczyć właściwe)"},
    )
    apply(c, Context.of(""))
    pola = c["strony"][0]["sekcje"][0]["pola"]
    assert [(f["tytul"], f["wymagane"]) for f in pola] == [("Telefon", False), ("Numer lokalu", False), ("Cel", True)]


def test_note_above_a_list_is_found_past_line_999():
    lines = "L999 (można zaznaczyć kilka odpowiedzi)\nL1000 ☐ A ☐ B"
    _, (lista,) = run(
        "multiple_choice", {"tytul": "Cel", "typ": "RADIO", "opcje": ["A", "B"], "linie": ["L1000"]}, lines=lines
    )
    assert lista["typ"] == "CHECKBOX"


def test_ocr_words_of_one_text_line_stay_in_one_line(monkeypatch):
    """OCR words with uneven glyph boxes, like 'oraz' below 'Imię', stay in one text line."""
    import subprocess
    from pathlib import Path

    from app.ingest import ocr

    head = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    rows = [
        "4\t1\t1\t1\t1\t0\t100\t100\t500\t40\t-1\t",
        "5\t1\t1\t1\t1\t1\t100\t100\t90\t40\t96\tImię",
        "5\t1\t1\t1\t1\t2\t200\t112\t80\t28\t96\toraz",
        "5\t1\t1\t1\t1\t3\t290\t100\t200\t40\t96\tnazwisko",
        "4\t1\t1\t1\t2\t0\t100\t160\t300\t40\t-1\t",
        "5\t1\t1\t1\t2\t1\t100\t170\t300\t30\t96\twnioskodawcy",
    ]
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0, "\n".join([head] + rows)))
    lines = ocr.lines_from_images([Path("skan.png")], "pol")
    assert [(ln["id"], ln["text"]) for ln in lines] == [("L001", "Imię oraz nazwisko"), ("L002", "wnioskodawcy")]


def test_twin_number_sections_are_alternatives():
    def table():
        return [{"tytul": t, "typ": "NUMBER"} for t in ("Liczba osób", "Stawka", "Kwota")]

    relief = {"pole": "ulga", "wartosc": "Tak"}
    c = normalize(
        {
            "tytul": "Deklaracja",
            "strony": [
                {
                    "tytul": "Opłata",
                    "sekcje": [
                        {"tytul": "Opłata", "pola": table()},
                        {"tytul": "Opłata z ulgą", "warunek": relief, "pola": table()},
                        {
                            "tytul": "Adres nieruchomości",
                            "pola": [{"tytul": t} for t in ("Ulica", "Numer", "Miejscowość")],
                        },
                        {
                            "tytul": "Adres korespondencji",
                            "warunek": relief,
                            "pola": [{"tytul": t} for t in ("Ulica", "Numer", "Miejscowość")],
                        },
                    ],
                }
            ],
        }
    )
    apply(c, Context.of(""), only={"twin_sections"})
    plain, _, address, _ = c["strony"][0]["sekcje"]
    assert plain["warunek"] == {"nie": relief}
    assert address["warunek"] is None  # twin text sections: one always shown, the other on demand


def test_condition_from_a_note_in_the_heading():
    lines = (
        "L001 A. DANE\nL002 1. Kto składa: ☐ 1. właściciel ☐ 2. współwłaściciel\n"
        "L003 B. DANE WSPÓŁMAŁŻONKA (wypełnić tylko w przypadku zaznaczenia kratki w poz. A 1.2)\n"
        "L004 Nazwisko ……\nL005 Numer dowodu ……\nL006 C. UWAGI\nL007 Uwagi ……"
    )
    c = normalize(
        {
            "tytul": "Deklaracja",
            "strony": [
                {
                    "tytul": "Dane",
                    "sekcje": [
                        {
                            "tytul": "Dane",
                            "pola": [
                                {
                                    "techName": "kto",
                                    "tytul": "Kto składa",
                                    "typ": "RADIO",
                                    "opcje": ["właściciel", "współwłaściciel"],
                                    "linie": ["L002"],
                                }
                            ],
                        },
                        {
                            "tytul": "Współmałżonek",
                            "pola": [
                                {"tytul": "Nazwisko", "linie": ["L004"]},
                                {"tytul": "Numer dowodu", "linie": ["L005"]},
                            ],
                        },
                        {"tytul": "Uwagi", "pola": [{"tytul": "Uwagi", "linie": ["L007"]}]},
                    ],
                }
            ],
        }
    )
    apply(c, Context.of(lines), only={"condition_from_cue"})
    _, spouse, notes = c["strony"][0]["sekcje"]
    assert spouse["warunek"] == {"pole": "kto", "wartosc": "współwłaściciel"} and notes["warunek"] is None


def test_proxy_signature_and_data_consent_fields_belong_to_the_base():
    c, pola = run(
        "drop_base_person_fields",
        {"tytul": "Imię pełnomocnika klubu"},
        {"tytul": "Data i podpis kandydata"},
        {"tytul": "Wyrażam zgodę na przetwarzanie moich danych osobowych", "typ": "CHECKBOX"},
        {"tytul": "Oryginał pełnomocnictwa", "typ": "FILE_UPLOADER"},
        {"tytul": "PESEL współmałżonka"},
        {"tytul": "Oświadczam, że zapoznałem się z regulaminem", "typ": "CHECKBOX"},
    )
    assert [f["tytul"] for f in pola] == ["PESEL współmałżonka", "Oświadczam, że zapoznałem się z regulaminem"]
    assert len(c["_pola_bazy"]) == 4  # the builder reports these to the official as flags
