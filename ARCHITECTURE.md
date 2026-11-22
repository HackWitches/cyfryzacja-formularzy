# Architektura

Dokumentacja architektury rozwiązania (zakres obowiązkowy zadania, punkt 3.2).
Jak uruchomić i jak używać API: [README.md](README.md).

## Przepływ

```
plik ──► walidacja: nazwa, rozmiar, zawartość zgodna z rozszerzeniem (sygnatura), JWT
     ──► DOC/DOCX/ODS/XLSX → PDF (LibreOffice)  │  JPG/PNG/skan PDF → OCR (Tesseract, pol)
     ──► ponumerowane linie tekstu z oznaczeniami pól: …… [___] [___↵] [#N] ☐ ☒, tabele „ | ”, kolumny
     ──► model open-weights Qwen3-VL-30B-A3B (serwer zgodny z OpenAI); obraz strony tylko dla zdjęć i skanów
     ──► kontrakt v2: mały JSON (strony, sekcje, pola, typy, wymagalność, walidatory, warunki, warianty, adresat),
         wymuszony schematem JSON na serwerze modelu
     ──► normalizacja + strażnik pól stałej części ──► reguły PL (app/rules)
     ──► builder: stała część z referencji COI + makra ADRES/OSOBA + JSONata + UUID + układ + flow
     ──► walidacja reguł COI ──► JSON FormDefinition
```

Najważniejsza decyzja: **model rozumie formularz, kod buduje JSON.**
Model zwraca tylko mały kontrakt, a deterministyczny builder składa pełną konfigurację COI.
Dzięki temu każdy wynik jest poprawny strukturalnie (0 błędów walidacji COI na wszystkich formularzach i modelach), a model nie musi znać formatu Generatora Formularzy.
Linie są ponumerowane, model wskazuje numery linii każdego pola, więc każde pole ma miejsce na papierze (endpoint `layout`, wizualizacja).

## Moduły (`app/`)

| Moduł | Odpowiedzialność |
|---|---|
| `main.py` | fabryka aplikacji; limit rozmiaru żądania sprawdzany przed odczytem treści |
| `config.py` | ustawienia ze zmiennych środowiskowych (jedno pole = jedna zmienna) |
| `api/routes.py` | endpointy REST, mapowanie błędów na kody HTTP |
| `api/auth.py` | uwierzytelnianie JWT (Bearer), generator tokenu |
| `ingest/upload.py` | nazwa pliku, rozmiar, rozpoznanie zawartości po sygnaturze |
| `ingest/convert.py` | konwersja dokumentów biurowych do PDF |
| `ingest/lines.py` | linie tekstu z PDF, ramki, kratki, tabele, kolumny |
| `ingest/ocr.py` | OCR skanów do tego samego formatu linii |
| `ingest/render.py` | strona PDF jako obraz (pod blokadą, bo pdfium nie jest bezpieczny wątkowo) |
| `llm/` | klient serwera zgodnego z OpenAI (Ollama, vLLM, Eden AI), prompt, domykanie uciętej odpowiedzi |
| `rules/` | reguły PL po modelu |
| `domain/contract.py` | kontrakt v2, schemat JSON, normalizacja, makra |
| `domain/builder.py` | składanie JSON-a COI i walidacja reguł COI |
| `layout.py` | układ na papierze: rozmiary stron, linie i ramki pól |
| `pipeline.py` | spina kroki; pliki tylko w katalogu tymczasowym na czas żądania |
| `resources/baza_formularza.json` | stała część e-usługi (dane, adres, pełnomocnik, kontakt, zgody) z referencji COI |

Kod jest po angielsku.
Kontrakt od modelu ma polskie klucze, bo model czyta polski formularz i polski prompt; notatki reguł i flagi dla urzędnika są po polsku.

## Gdzie działa model

| Konfiguracja | Model | Uwagi |
|---|---|---|
| `docker-compose.yml` (domyślna) | Ollama 0.40.17 w kontenerze obok API, Qwen3-VL-30B-A3B Q4, kontekst 32k, karta NVIDIA (RTX 4090, 24 GB) | KV cache q8_0, 2 zapytania naraz; Ollama bez portu na zewnątrz |
| `docker-compose.edenai.yml` | ten sam model open-weights przez API Eden AI | bez własnego GPU; prompt w wiadomości użytkownika |

Klient modelu działa z każdym serwerem zgodnym z OpenAI (Ollama, vLLM, llama.cpp), więc zmiana miejsca modelu to tylko `LLM_BASE_URL` i `LLM_MODEL`.
Mierzyliśmy też vLLM z tym samym modelem w FP8: ta sama jakość na 8 z 9 formularzy, ale wolniej, dlatego domyślna jest Ollama.

## Reguły PL

Model z 3 mld aktywnych parametrów myli się tam, gdzie papier mówi wprost, co jest w polu, więc po modelu działają deterministyczne reguły (`app/rules/`).
Reguła zostaje tylko wtedy, gdy poprawia porównanie zapisanych odpowiedzi modelu z oczekiwanymi kontraktami 9 formularzy zadania i niczego nie psuje.

| Reguła | Co robi |
|---|---|
| `clean_titles` | usuwa z tytułów numerację, przypisy, „(zaznaczyć…)”, wielkie litery i odwołania do przepisów |
| `drop_office_fields`, `drop_duplicates` | usuwa pola „wypełnia urząd” i powtórzenia |
| `statements_to_consents` | samodzielny checkbox „Oświadczam…” zamienia na sekcję zgód |
| `type_from_label`, `type_from_cells` | typ, walidator i podpowiedź formatu z etykiety i z liczby kratek (PESEL, NIP, REGON, kod, data, rachunek) |
| `country_dictionary`, `multiple_choice` | słownik Kraje; „można zaznaczyć kilka” = CHECKBOX |
| `condition_other_or_yes`, `condition_from_cue`, `twin_sections` | warunki widoczności: „Inne: ……”, „jeżeli tak”, dopisek „wypełnić tylko w przypadku … w poz. …”, dwie tabele obliczeń |
| `required` | „nieobowiązkowe”, „jeśli posiada” = pole nieobowiązkowe; pole z warunkiem wymagane, gdy widoczne |
| `entity_variant`, `addressee` | wariant dla firmy z papieru (NIP, REGON, „nazwa firmy”); adresat i rodzaj (urząd albo organ) |
| `drop_base_person_fields` | usuwa dane reprezentanta i pełnomocnika, podpisy i zgody, które są w stałej części |

| Konfiguracja domyślna, 154 oczekiwane pola | Pola bez błędów | Nadmiarowe | Poprawne warunki | Zły typ | Zła wymagalność |
|---|---|---|---|---|---|
| sam model | 68 | 46 | 27 z 66 | 48 | 23 |
| model + reguły | 81 | 24 | 46 z 65 | 25 | 12 |

## Strona końcowa

Reguły techniczne mówią, że na stronie FINISH może być wyłącznie pole `INFO_SECTION`.
Oba referencyjne pliki JSON od COI (formularze 1 i 5) mają tam jednak sekcję `SECTION` z jednym polem `TEXT_BLOCK` (podgląd wniosku).
Trzymamy się przykładów, bo to one są sprawdzonym wzorem importu; walidator pilnuje, żeby na stronie końcowej nie było żadnego pola do wypełnienia.

## Bezpieczeństwo i kontener

- Obraz `python:3.14.10-slim-trixie` (Debian slim zamiast Alpine, bo LibreOffice i Tesseract z językiem polskim nie mają dla Alpine pełnych pakietów), zależności przypięte.
- Użytkownik bez uprawnień (uid 10001), system plików tylko do odczytu, pliki tylko w `/tmp` (tmpfs, 1 GB) na czas żądania, limit pamięci i procesów, `no-new-privileges`, bez capabilities, wystawiony tylko port 8000.
- Sekrety tylko w runtime (`env_file`); serwis nie startuje bez `JWT_SECRET`.
- Zbyt duży plik odrzucany po `Content-Length`, zanim treść trafi na dysk; limit stron dokumentu.
- Komunikaty błędów bez ścieżek i adresów wewnętrznych (szczegóły w logu).
- Licencje: wyłącznie MIT, BSD, Apache-2.0; LibreOffice (MPL-2.0) i Tesseract (Apache-2.0) działają jako osobne programy wywoływane z wiersza poleceń.

## Znane ograniczenia

- Warunki widoczności w długich formularzach to najsłabsze miejsce małego modelu; reguły poprawiają je tam, gdzie papier ma jawny dopisek.
- Ramka pola w `layout` ma dokładność jednej linii tekstu.
- Dane „reprezentanta” i „pełnomocnika” zawsze trafiają do stałej części; jeśli na papierze to inna osoba (np. przedstawiciel ustawowy), urzędnik dostaje flagę.
- Pole o gołym tytule „Nazwa”, „Data” albo „Adres” jest traktowane jak dane wnioskodawcy ze stałej części i pomijane (z flagą).
- Wariant „firma lub instytucja” włącza się przy NIP, REGON, KRS albo „siedzibie” na papierze; zawinięta klauzula RODO potrafi go włączyć niepotrzebnie.
- OCR skanów czyta tekst, kolumny i kolejność, ale nie wykrywa ramek i kratek, dlatego przy skanach model dostaje też obraz strony.
- Zdjęcia i skany są najwolniejsze: OCR plus obraz strony dla modelu (formularz JPG ~10 min przy kilku żądaniach naraz; PDF z tekstem 1-5 min).
- Słownik zaproponowany przez model, którego nie ma w Generatorze, trafia do formularza jako lista opcji z flagą „utwórz słownik”.

## Podziękowania

Część reguł PL pochodzi z naszego drugiego projektu, form2json (ekstrakcja geometryczna OpenCV).
