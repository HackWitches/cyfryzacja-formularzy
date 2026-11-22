# Cyfryzacja formularzy COI: serwis REST

Serwis przyjmuje jeden plik formularza i zwraca JSON z konfiguracją e-formularza, gotowy do importu w Generatorze Formularzy COI.
Obsługiwane formaty: PDF, DOC, DOCX, ODS, XLSX, JPG, JPEG, PNG.

## Jak to działa

```
plik ──► walidacja (nazwa, rozmiar, zawartość zgodna z rozszerzeniem, JWT)
     ──► DOC/DOCX/ODS/XLSX → PDF (LibreOffice) │ JPG/PNG/skan → OCR (Tesseract, pol)
     ──► ponumerowane linie tekstu z oznaczeniami pól ([___], [#N], ☐, tabele „ | ”)
     ──► model językowy open source (Qwen3-VL): przedmiot wniosku, warianty, adresat, warunki widoczności (kontrakt v2)
     ──► reguły PL: typ i walidator z etykiety i kratek, pola nieobowiązkowe, warunki „inne / jeżeli tak”, adresat
     ──► builder: baza z referencji COI + makra + JSONata + UUID + układ + flow
     ──► walidacja reguł COI ──► JSON FormDefinition
```

Moduły (`app/`):

| Moduł | Odpowiedzialność |
|---|---|
| `main.py` | fabryka aplikacji; limit rozmiaru żądania sprawdzany przed odczytem treści |
| `config.py` | ustawienia ze zmiennych środowiskowych (jedno pole = jedna zmienna) |
| `api/routes.py` | endpointy REST, mapowanie błędów na kody HTTP |
| `api/auth.py` | uwierzytelnianie JWT (Bearer), wystawianie tokenu do testów |
| `ingest/upload.py` | nazwa pliku, rozmiar, rozpoznanie zawartości po sygnaturze |
| `ingest/convert.py` | konwersja dokumentów biurowych do PDF |
| `ingest/lines.py` | linie tekstu z PDF, ramki, kratki, tabele, kolumny |
| `ingest/ocr.py` | OCR skanów do tego samego formatu linii |
| `ingest/render.py` | strona PDF jako obraz (pod blokadą, bo pdfium nie jest bezpieczny wątkowo) |
| `llm/` | odczyt formularza przez model open source na serwerze zgodnym z OpenAI (vLLM, Ollama); ratuje uciętą odpowiedź |
| `rules/` | reguły PL po modelu: typ, walidator i podpowiedź formatu z etykiety i kratek, wymagalność, warunki, adresat, porządek tytułów |
| `domain/contract.py`, `domain/builder.py` | kontrakt v2 i składanie JSON-a COI z walidacją |
| `pipeline.py` | spina kroki; pliki tylko w katalogu tymczasowym na czas żądania |
| `resources/baza_formularza.json` | stałe sekcje (dane, adres, pełnomocnik, kontakt, zgody) z referencji COI |

Nazwy reguł PL (`tytuly`, `typ_z_etykiety`, `wymagane`...) są po polsku celowo: te same nazwy widzi urzędnik w odpowiedzi `debug=true` pod kluczem `reguly`.
Kontrakt od modelu też ma polskie klucze, bo model czyta polski formularz i polski prompt.

> **Uwaga! Strona końcowa jest zbudowana według przykładów COI nr 1 i 5.**
> Reguły techniczne mówią, że na stronie FINISH może być wyłącznie pole `INFO_SECTION`.
> Oba referencyjne pliki JSON od COI (formularze 1 i 5) mają tam jednak sekcję `SECTION` z jednym polem `TEXT_BLOCK` (podgląd wniosku).
> Trzymamy się przykładów, bo to one są sprawdzonym wzorem importu; walidator pilnuje, żeby na stronie końcowej nie było żadnego pola do wypełnienia.

## Budowanie obrazu

```bash
docker build -t cyfryzacja-formularzy-api:1.0.0 .
```

Polecenie uruchamia się w katalogu `service/`.
`docker compose up --build` (niżej) buduje ten sam obraz i od razu go uruchamia.

## Uruchomienie (Docker)

```bash
cp .env.example .env
```

Uzupełnij w `.env` `JWT_SECRET` (np. `openssl rand -hex 32`).
Bez niego serwis się nie uruchomi: uwierzytelnianie JWT jest zawsze włączone, chyba że jawnie ustawisz `AUTH_DISABLED=true` (tylko do pracy lokalnej).
Serwis korzysta wyłącznie z modelu open source (Qwen3-VL-30B-A3B) na serwerze zgodnym z OpenAI.
Są trzy gotowe konfiguracje:

| Wariant | Gdzie działa model | Polecenie |
|---|---|---|
| domyślny | Ollama (Q4) na serwerze GPU, np. DGX Spark przez tunel SSH na porcie 18001 | `docker compose up --build -d` |
| lokalny stack | Ollama w kontenerze obok API, na karcie NVIDIA tego komputera (testowane: RTX 4090, 24 GB) | `docker compose -f docker-compose.yml -f docker-compose.local.yml up --build -d` |
| vLLM | vLLM (FP8) na serwerze GPU przez tunel SSH na porcie 18000 | `docker compose -f docker-compose.yml -f docker-compose.vllm.yml up --build -d` |

Wariant domyślny wymaga tunelu na komputerze z Dockerem:

```bash
ssh -N -L 18001:127.0.0.1:11434 <uzytkownik>@<serwer-gpu>
```

Lokalny stack wymaga Dockera z obsługą GPU (Linux: NVIDIA Container Toolkit, Windows: Docker Desktop z WSL2).
Przy pierwszym starcie pobiera model (~19 GB) do wolumenu `ollama`; na mniejszej karcie ustaw `LLM_MODEL=qwen3-vl:8b-instruct` (~6 GB VRAM, nie mierzony na naszych formularzach).
Serwer Ollama ma kontekst 32k (długie formularze), flash attention i nie wystawia portu na zewnątrz: łączy się z nim tylko API.

```bash
curl http://localhost:8000/health
```

Kontener działa jako użytkownik bez uprawnień roota, z systemem plików tylko do odczytu, a pliki trzyma wyłącznie w `/tmp` (tmpfs, limit 1 GB) na czas żądania.
Ma limit pamięci (`API_MEM_LIMIT`, domyślnie 4 GB) i limit procesów, żeby złośliwy plik nie zajął całego hosta.
Obraz bazowy ma jawną wersję (`python:3.14.10-slim-trixie`; Debian slim zamiast Alpine, bo LibreOffice i Tesseract z językiem polskim nie mają dla Alpine pełnych pakietów), zależności Pythona są przypięte w `requirements.txt`, wystawiony jest tylko port 8000.
Sekrety trafiają do kontenera wyłącznie w czasie uruchomienia (`env_file`).
LibreOffice (MPL-2.0) i Tesseract (Apache-2.0) działają jako osobne programy wywoływane z wiersza poleceń, a nie jako biblioteki dołączone do kodu.

## Konfiguracja (zmienne środowiskowe)

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `MAX_FILE_SIZE_MB` | `10` | maksymalny rozmiar pliku |
| `MAX_PAGES` | `40` | maksymalna liczba stron dokumentu |
| `JWT_SECRET` | (wymagane) | klucz podpisu tokenów; bez niego serwis się nie uruchomi |
| `JWT_ALGORITHM` | `HS256` | algorytm podpisu |
| `JWT_AUDIENCE` | (puste) | opcjonalne pole `aud` wymagane w tokenie |
| `AUTH_DISABLED` | `false` | `true` wyłącza uwierzytelnianie; tylko do pracy lokalnej |
| `LLM_BASE_URL` | (wymagane) | adres serwera modelu zgodnego z OpenAI, np. `http://host.docker.internal:18000/v1` (vLLM) |
| `LLM_MODEL` | `qwen3-vl` | nazwa modelu na serwerze, np. `qwen3-vl` (vLLM) albo `qwen3-vl:30b-a3b-instruct` (Ollama) |
| `LLM_API_KEY` | (puste) | klucz, jeśli serwer modelu go wymaga |
| `LLM_JSON_SCHEMA` | `true` | wymusza na serwerze odpowiedź zgodną ze schematem kontraktu (vLLM) |
| `LLM_MAX_IMAGES` | `4` | maksymalna liczba obrazów stron w jednym zapytaniu |
| `LLM_TIMEOUT_S` | `300` (compose: `1800`) | limit czasu wywołania modelu; najdłuższy zmierzony formularz ~1400 s |
| `LLM_VISION` | `auto` | czy wysyłać modelowi także obraz strony: `auto` tylko dla zdjęć i skanów (tekst z OCR), `on` zawsze, `off` nigdy |
| `OCR_LANG` | `pol` | język Tesseract |
| `CONVERT_TIMEOUT_S` | `120` | limit czasu konwersji LibreOffice i OCR jednej strony |
| `SOFFICE_BIN` | `soffice` | program LibreOffice (ścieżka, gdy nie ma go w `PATH`) |

Zmiana modelu lub serwera to tylko zmiana `LLM_BASE_URL` i `LLM_MODEL`, bez zmian w kodzie.

## REST API

Dokumentacja interaktywna: `http://localhost:8000/docs` (OpenAPI).

### `GET /health`

Zwraca `{"status": "ok"}`.

### `POST /api/v1/forms/convert`

- Nagłówek: `Authorization: Bearer <JWT>` (token musi mieć pole `exp`; w `/docs` jest przycisk "Authorize").
- Treść: `multipart/form-data` z jednym polem `file`.
- Parametr `debug=true`: zamiast samego formularza zwraca `{form, do_weryfikacji, bledy, kontrakt, linie, reguly}` (`reguly` = co poprawiły reguły PL).
- Odpowiedź `200`: JSON FormDefinition do importu w COI; nagłówki `X-Form-Flags` (liczba miejsc do weryfikacji przez urzędnika) i `X-Form-Errors` (liczba naruszeń reguł COI).
- Komunikaty błędów nie zawierają szczegółów wewnętrznych (ścieżek, adresu serwera modelu); te trafiają do logu serwisu.

| Kod | Kiedy |
|---|---|
| 400 | pusta lub błędna nazwa pliku (ponad 250 znaków albo znaki `; : > < / \ \| ' * % $`), pusty plik |
| 401 | brak lub nieważny token JWT |
| 411 | żądanie bez nagłówka `Content-Length` |
| 413 | plik większy niż `MAX_FILE_SIZE_MB` (sprawdzane przed odczytem treści żądania) albo dokument dłuższy niż `MAX_PAGES` stron |
| 415 | nieobsługiwany format albo zawartość niezgodna z rozszerzeniem |
| 422 | nie udało się odczytać dokumentu: konwersja, uszkodzony albo zaszyfrowany plik, OCR |
| 502 | błąd lub niedostępność modelu językowego |

Przykład:

```bash
TOKEN=$(docker compose exec api python -m app.api.auth urzednik 480)
```

```bash
curl -s -H "Authorization: Bearer $TOKEN" -F "file=@wniosek.pdf" http://localhost:8000/api/v1/forms/convert -o formularz.json
```

## Testy

```bash
python3.14 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
```

```bash
.venv/bin/python -m pytest -q
```

Testy używają atrapy modelu, więc nie wymagają sieci ani klucza API.

## Reguły PL

Model open source myli się tam, gdzie papier mówi wprost, co jest w polu, więc po modelu działają deterministyczne reguły (`app/rules/`).
Reguła zostaje na liście tylko wtedy, gdy poprawia porównanie zapisanych odpowiedzi modelu z oczekiwanymi kontraktami naszych 9 formularzy.
Na najlepszej konfiguracji (Ollama Q4) reguły zmniejszają liczbę pól ze złym typem z 48 do 26, a ze złą wymagalnością z 23 do 12, i żadnej kolumny nie pogarszają.

## Znane ograniczenia

- Pole o gołym tytule "Nazwa", "Data" albo "Adres" jest traktowane jak dane wnioskodawcy ze stałej części i pomijane (z flagą dla urzędnika), także w sekcji o przedmiocie wniosku.
- Wariant "firma lub instytucja" włącza się, gdy na papierze jest NIP, REGON, KRS albo "siedziba"; zawinięta klauzula RODO z "siedzibą" urzędu potrafi go włączyć niepotrzebnie.
- Etykieta łącząca dwie rzeczy, z których reguły znają jedną ("Numer i data decyzji"), dostaje typ tej znanej.
- OCR skanów czyta tekst, kolumny i kolejność; ramek i kratek na obrazach jeszcze nie wykrywa, dlatego przy skanach model dostaje też obraz strony.
- Słownik zaproponowany przez model, którego nie ma w Generatorze, trafia do formularza jako zwykła lista opcji z flagą "utwórz słownik".

## Podziękowania

Część reguł PL (typ pola z etykiety i liczby kratek, wyrażenia dla pól nieobowiązkowych i warunków „inne / jeżeli tak”, rozpoznanie adresata z nagłówka) pochodzi z naszego drugiego projektu, form2json (ekstrakcja geometryczna OpenCV); tutaj działają po modelu językowym w `app/rules/`.

