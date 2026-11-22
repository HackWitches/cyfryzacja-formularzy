# Wyniki: Qwen3-VL-30B-A3B-Instruct

Pliki JSON FormDefinition do importu w Generatorze Formularzy COI, wygenerowane przez serwis z 9 formularzy zadania (wersje PDF albo DOCX z materiałów COI).
Konfiguracja: Ollama 0.40.17, model `qwen3-vl:30b-a3b-instruct` (Q4), kontekst 32k, obraz strony tylko dla skanów, reguły PL włączone.
Uruchomione 22.11.2026 przez kontener API na karcie NVIDIA, po 3 formularze naraz, z promptem bez przykładów z tych formularzy.

Każdy plik przechodzi walidację reguł COI z `app/domain/builder.py` bez błędów.
Formularz 08 przeliczyliśmy trzy razy, bo model raz rozpisuje jego tabele obliczeń na pola, a raz je ściska (26, 51 i 52 z 57 pól); tu jest przebieg środkowy, 51 pól.

## Zgodność z oczekiwanymi kontraktami z anotacji zespołu

Porównanie pól przedmiotu wniosku (bez stałej części e-usługi, którą builder dokłada zawsze).
„Bez błędów” znaczy: typ, wymagalność, opcje, powtarzalność i warunek widoczności zgodne naraz.

| Formularz | Znalezione pola | Bez błędów | Nadmiarowe | Poprawne warunki | Czas |
|---|---|---|---|---|---|
| 01 numer porządkowy | 4/4 | 3 | 0 | - | 43 s |
| 02 zwrot opłaty skarbowej | 7/7 | 7 | 12 | 2/2 | 103 s |
| 03 wydanie zaświadczenia | 4/6 | 1 | 1 | 1/1 | 49 s |
| 04 wynajem świetlicy | 7/10 | 4 | 3 | - | 65 s |
| 05 nadpłata podatku | 7/7 | 3 | 1 | 0/2 | 55 s |
| 06 zajęcie pasa drogowego | 21/23 | 10 | 7 | 0/2 | 151 s |
| 07 psy ras agresywnych | 11/17 | 4 | 2 | 0/2 | 100 s |
| 08 deklaracja o opłacie za odpady | 51/57 | 37 | 9 | 39/43 | 278 s |
| 09 stypendium sportowe (3 w 1) | 19/23 | 4 | 8 | 2/13 | 252 s |
| **Razem** | **131/154 (85%)** | **73 (47%)** | **43** | **44/65** | **43-278 s** |
