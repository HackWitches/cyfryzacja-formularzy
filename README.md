# Cyfryzacja formularzy COI

Serwis REST: wysyłasz plik papierowego formularza (PDF, DOC, DOCX, ODS, XLSX, JPG, JPEG, PNG), dostajesz JSON do importu w Generatorze Formularzy COI.
Działa na modelu open-weights Qwen3-VL-30B-A3B. Jak to jest zbudowane: [ARCHITECTURE.md](ARCHITECTURE.md).

## Szybki start

```bash
{ cat .env.example; echo "JWT_SECRET=$(openssl rand -hex 32)"; } > .env && docker compose up --build -d
```

```bash
TOKEN=$(docker compose exec -T api python -m app.api.auth urzednik 60)
```

```bash
curl -s -H "Authorization: Bearer $TOKEN" -F "file=@wniosek.pdf" http://localhost:8000/api/v1/forms/convert -o formularz.json
```

Domyślnie model działa w kontenerze Ollama na karcie NVIDIA tego komputera; pierwszy start pobiera model (~19 GB).
Bez karty graficznej: sekcja „Uruchomienie”, wariant Eden AI.

## Budowanie obrazu Docker

```bash
docker build -t cyfryzacja-formularzy-api:1.0.0 .
```

## Uruchomienie

Wybierz, gdzie działa model, i uruchom jedno polecenie (każde buduje obraz i startuje serwis na porcie 8000):

| Model | Polecenie |
|---|---|
| Ollama w kontenerze na karcie NVIDIA (domyślnie; Docker z obsługą GPU, testowane na RTX 4090) | `docker compose up --build -d` |
| Eden AI: ten sam model przez API, bez karty graficznej (`EDENAI_API_KEY` w `.env`) | `docker compose -f docker-compose.yml -f docker-compose.edenai.yml up --build -d api` |

Na mniejszej karcie ustaw w `.env` `LLM_MODEL=qwen3-vl:8b-instruct` (~6 GB VRAM).

Sprawdzenie: `curl http://localhost:8000/health` zwraca `{"status":"ok"}`.

## Konfiguracja

Zmienne środowiskowe w pliku `.env` (szablon: `.env.example`); sekrety trafiają do kontenera tylko przy uruchomieniu.

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `JWT_SECRET` | (wymagane) | klucz podpisu tokenów JWT, min. 32 znaki |
| `JWT_ALGORITHM` | `HS256` | algorytm podpisu |
| `JWT_AUDIENCE` | (puste) | opcjonalne pole `aud` wymagane w tokenie |
| `AUTH_DISABLED` | `false` | `true` wyłącza JWT (tylko lokalnie) |
| `MAX_FILE_SIZE_MB` | `10` | maksymalny rozmiar pliku |
| `MAX_PAGES` | `40` | maksymalna liczba stron dokumentu |
| `LLM_BASE_URL` | z compose | adres serwera modelu zgodnego z OpenAI (dowolny: Ollama, vLLM, Eden AI) |
| `LLM_MODEL` | `qwen3-vl:30b-a3b-instruct` | nazwa modelu na serwerze |
| `LLM_API_KEY` / `EDENAI_API_KEY` | (puste) | klucz serwera modelu, jeśli go wymaga |
| `LLM_VISION` | `auto` | obraz strony dla modelu: `auto` tylko skany i zdjęcia, `on`, `off` |
| `LLM_TIMEOUT_S` | `1800` | limit czasu odpowiedzi modelu |
| `OCR_LANG` | `pol` | język OCR |

## REST API

Dokumentacja interaktywna z możliwością wysłania pliku: `http://localhost:8000/docs`.
Każde żądanie poza `/health` wymaga nagłówka `Authorization: Bearer <JWT>`; token wystawia `python -m app.api.auth <użytkownik> <minuty>`.

| Endpoint | Co robi |
|---|---|
| `GET /health` | stan serwisu |
| `POST /api/v1/forms/convert` | plik (`multipart/form-data`, pole `file`) → JSON FormDefinition do importu w COI |
| `POST /api/v1/forms/convert?debug=true` | to samo plus miejsca do sprawdzenia przez urzędnika, kontrakt, linie i ramki pól na papierze |
| `POST /api/v1/forms/layout` | układ formularza bez modelu: strony i linie tekstu z ramkami `[x0, top, x1, bottom]` |

| Kod | Kiedy |
|---|---|
| 200 | JSON formularza; nagłówki `X-Form-Flags` (miejsca do sprawdzenia) i `X-Form-Errors` (naruszenia reguł COI) |
| 400 | pusta lub błędna nazwa pliku (ponad 250 znaków albo znaki `; : > < / \ \| ' * % $`), pusty plik |
| 401 | brak lub nieważny token |
| 411 / 413 | brak `Content-Length` / plik za duży albo za dużo stron |
| 415 | nieobsługiwany format albo zawartość niezgodna z rozszerzeniem |
| 422 | nie udało się odczytać dokumentu (uszkodzony plik, konwersja, OCR) |
| 502 | błąd serwera modelu |

## Zgodność z wymaganiami zadania

| Wymaganie | Jak spełnione |
|---|---|
| 3.2 API REST, wszystkie formaty | `POST /api/v1/forms/convert`; DOC/DOCX/ODS/XLSX przez LibreOffice, JPG/PNG i skany przez OCR |
| 3.2 struktura zgodna z aplikacją i wymaganiami projektowania | stała część i układ z referencyjnych JSON-ów COI, strony tematyczne, walidacja reguł technicznych COI w każdym żądaniu |
| 3.2 dokumentacja architektury | [ARCHITECTURE.md](ARCHITECTURE.md) |
| 3.3 I słowniki i dane inicjalne | Kraje, TERYT_4G_DOWODOWY w adresach, dane z rejestru (MLPZ) w stałej części |
| 3.3 II nowy słownik | model proponuje słownik dla długich list (np. dyscypliny sportowe), urzędnik dostaje flagę |
| 3.3 III zależności | warunki widoczności pól, sekcji i stron w JSONata, warianty wnioskodawcy, flow stron |
| 3.3 VI walidacje | PESEL, NIP, REGON, kod pocztowy, e-mail, ePUAP, e-Doręczenia, dowód; podpowiedzi formatu i długości; wymagalność |
| 5 open source, bez API-only | tylko MIT/BSD/Apache; model open-weights uruchamiany lokalnie w kontenerze (Eden AI tylko jako opcja z tym samym modelem) |
| 5 walidacja pliku | MIME i sygnatura zgodne z rozszerzeniem, nazwa, rozmiar (`MAX_FILE_SIZE_MB`) |
| 5 pliki tymczasowe | wyłącznie katalog tymczasowy żądania na tmpfs, usuwany po odpowiedzi |
| 5 Docker | oficjalny obraz z jawną wersją, bez `latest`, użytkownik bez uprawnień, tylko port 8000, sekrety w runtime |
| 5 konfiguracja | rozmiar pliku, JWT i parametry modelu w zmiennych środowiskowych |

## Wyniki

Wynikowe pliki JSON dla 9 formularzy zadania: [RESULTS/qwen3-vl-30b-a3b-instruct/](RESULTS/qwen3-vl-30b-a3b-instruct/) (z tabelą zgodności z oczekiwanymi wynikami).

## Testy

```bash
python3.14 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest -q
```

Formatowanie i lint (ruff, ustawienia w `pyproject.toml`):

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
```
