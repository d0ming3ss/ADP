# GeoRent-AI — instrukcja uruchomieniowa i przewodnik po projekcie

Dokument ma wyjaśnić **jak uruchomić projekt** oraz **co dokładnie dzieje się w środku**: jakie moduły istnieją, skąd
pochodzą dane, jak działa mechanizm pobierania, jakie operacje przestrzenne są wykonywane i z jakich narzędzi korzystam.
Opis metod statystycznych (wzory, uzasadnienia) jest w `METODOLOGIA.md`, a wyniki w `WYNIKI.md`.

---

## Część A. Instrukcja uruchomieniowa

### A.1. Wymagania

| Element | Wymaganie |
|---|---|
| Python | 3.12 lub nowszy (lokalnie testowane na 3.14, w Dockerze 3.12) |
| Internet | przy pierwszym pobraniu danych (~8 MB) oraz dla kafelków mapy OpenStreetMap |
| Docker (opcjonalnie) | Docker Engine + Compose v2 |
| QGIS (opcjonalnie) | do otwarcia wyników; generowanie `.qgz` wymaga PyQGIS (systemowy Python z QGIS) |

### A.2. Uruchomienie lokalne (krok po kroku)

```bash
git clone https://github.com/d0ming3ss/ADP.git && cd ADP
python3 -m venv .venv
source .venv/bin/activate                 # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt        # zależności + pytest
python -m georent download                 # pobranie danych (Barcelona)
streamlit run app.py                       # aplikacja: http://localhost:8501
```

Jeżeli pominiesz krok pobierania, aplikacja sama pokaże przycisk **„Pobierz dane”**.

### A.3. Uruchomienie w Dockerze

```bash
docker compose up --build                  # http://localhost:8501
```

* `./data` → `/app/data` — pobrane dane (przetrwają restart kontenera),
* `./exports` → `/app/exports` — wyniki eksportu GIS.

Przydatne warianty:

```bash
GEORENT_PORT=8600 docker compose up                         # inny port na hoście
GEORENT_CITY=lisbon docker compose up                       # inne miasto (najpierw je pobierz)
docker compose run --rm georent python -m georent download  # pobranie danych w kontenerze
docker compose run --rm georent python -m pytest            # testy w kontenerze
docker compose run --rm georent python -m georent export    # eksport w kontenerze (bez .qgz)
docker compose down                                         # zatrzymanie
```

Bez Compose: `docker build -t georent-ai .` oraz
`docker run --rm -p 8501:8501 -v "$PWD/data:/app/data" -v "$PWD/exports:/app/exports" georent-ai`.

Obraz działa jako użytkownik `uid 1000` (żeby wolumeny z Linuksa były zapisywalne), ma healthcheck na
`/_stcore/health`. Gdy port 8501 jest zajęty, użyj `GEORENT_PORT`.

### A.4. Polecenia CLI (`python -m georent …`)

| Polecenie | Co robi |
|---|---|
| `download [--city X] [--release RRRR-MM-DD] [--force]` | pobiera dane; bez `--force` używa kopii lokalnej |
| `update [--city X]` | sprawdza indeks Inside Airbnb i pobiera nowsze wydanie, jeśli istnieje |
| `analyze [--weights queen\|rook\|knn] [--variable median_price] [--exclude-outliers]` | liczy analizę i wypisuje wyniki w terminalu |
| `export [--output DIR] [--no-qgis] [--weights …] [--variable …]` | zapisuje GPKG, GeoJSON, CSV i buduje `georent.qgz` |

Inne skrypty: `python scripts/generate_results.py` (odtwarza `docs/WYNIKI.md` i mapy w `docs/img/`),
`python -m georent.qgis_project exports/<miasto>` (tylko budowa projektu QGIS).

### A.5. Zmienne środowiskowe (konfiguracja)

| Zmienna | Domyślnie | Znaczenie |
|---|---|---|
| `GEORENT_CITY` | `barcelona` | miasto: barcelona, lisbon, amsterdam, berlin, prague, vienna |
| `GEORENT_DATA_DIR` | `./data` | katalog danych |
| `GEORENT_EXPORT_DIR` | `./exports` | katalog eksportów |
| `GEORENT_PERMUTATIONS` | `999` | liczba permutacji testu Morana |
| `GEORENT_SEED` | `42` | ziarno losowe (powtarzalność wyników) |
| `GEORENT_MIN_LISTINGS` | `10` | min. liczba ofert z ceną, by dzielnica była „wiarygodna” |
| `GEORENT_HTTP_TIMEOUT` | `60` | limit czasu zapytań HTTP [s] |

Zmienne `GEORENT_*` czyta `georent/config.py`; niepoprawna wartość liczbowa kończy się czytelnym błędem.

### A.6. Obsługa aplikacji

Panel boczny: **miasto** (spośród pobranych), **sekcja**, **filtry ofert** i **parametry analizy przestrzennej**
(zmienna oraz macierz wag). Każdy filtr przelicza statystyki dzielnic *i* autokorelację — to nie jest ozdoba. Sekcje:

1. **Dashboard** — KPI i rankingi.
2. **Mapa ofert** — punkty / klastry / heatmapa.
3. **Analiza dzielnic** — dwa kartogramy obok siebie, tabela, eksport CSV.
4. **Analiza cen** — histogram, boxploty, cena a odległość, wpływ lokalizacji.
5. **Analiza przestrzenna** — Moran's I, LISA, wykres rozrzutu, odporność wyniku.
6. **Dane i źródła** — metadane, jakość, aktualizacja, eksport do QGIS.
7. **Metodologia** — treść `docs/METODOLOGIA.md`.

### A.7. Eksport i QGIS

`python -m georent export` → `exports/<miasto>/`. Otwórz `georent.qgz` w QGIS (*Projekt → Otwórz*). Szczegóły i słownik pól:
`docs/QGIS.md`.

### A.8. Rozwiązywanie problemów

| Objaw | Przyczyna / rozwiązanie |
|---|---|
| „Brak lokalnych danych…” | uruchom `python -m georent download` lub użyj przycisku w aplikacji |
| Błąd pobierania (timeout, 404) | sprawdź sieć; `download` nie zostawia niekompletnych plików, można powtórzyć; indeks niedostępny → używana jest zapasowa data wydania |
| „Do analizy kwalifikuje się N dzielnic; wymagane co najmniej 8” | filtry zostawiły za mało dzielnic — poluzuj filtry lub obniż `GEORENT_MIN_LISTINGS` |
| Brak `georent.qgz` po eksporcie | brak PyQGIS (np. w Dockerze); uruchom `/usr/bin/python3 -m georent.qgis_project exports/<miasto>` na maszynie z QGIS |
| Ostrzeżenie libpysal „not fully connected” | graf sąsiedztwa ma kilka składowych/wysp — oczekiwane, opisane w metodologii |
| Aplikacja nie widzi zmian w kodzie | w `.streamlit/config.toml` wyłączono watcher plików; zrestartuj `streamlit` |

---

## Część B. Mapa projektu

```text
app.py                      punkt wejścia Streamlit (woła georent.ui.run_app)
georent/
  config.py                 ustawienia + katalog miast
  data_source.py           pobieranie, walidacja, składowanie lokalne
  cleaning.py              czyszczenie tabeli ofert
  spatial.py               punkty, poligony, Spatial Join, agregacje
  autocorr.py              wagi przestrzenne, Moran's I, LISA
  analysis.py              filtry + pipeline + wpływ lokalizacji/odstających
  visualization.py         mapy Folium i wykresy Plotly
  export.py                eksport GPKG/GeoJSON/CSV
  qgis_project.py          generator projektu .qgz (PyQGIS)
  ui.py                    sekcje Streamlit, filtry, cache
  cli.py, __main__.py      polecenia terminalowe
tests/                     103 testy pytest
scripts/generate_results.py  odtwarzalny raport wyników
docs/                      dokumentacja, wyniki, rysunki
poc/opensky_app.py         dawny prototyp (OpenSky) — niezależny od reszty
data/, exports/            dane i wyniki (poza Git)
```

**Przepływ danych (jedna linia):**
Inside Airbnb → `data_source` (pobranie + walidacja) → `cleaning` → `spatial` (punkty, Spatial Join, agregacje) →
`autocorr` (Moran, LISA) → `analysis` (spina całość z filtrami) → `visualization`/`ui` (aplikacja) oraz `export` →
`qgis_project` (QGIS).

Zasada: moduły domenowe (`cleaning`, `spatial`, `autocorr`, `analysis`) **nie zależą od Streamlit** i można je wołać z
terminala lub testów. `ui.py` jest jedyną warstwą znającą Streamlit.

---

## Część C. Opis modułów (komponentów)

### C.1. `config.py` — konfiguracja
* `Settings` — niemutowalne ustawienia z zmiennych środowiskowych (miasto, katalogi, permutacje, ziarno, próg ofert, timeout).
* `CitySpec` / `CITIES` — katalog miast: ścieżka w Inside Airbnb (`spain/catalonia/barcelona`), zapasowa data wydania,
  współrzędne centrum (do odległości od centrum).
* Stałe: adres indeksu `https://insideairbnb.com/get-the-data/`, baza `https://data.insideairbnb.com`, tekst licencji.
* `get_city(slug)`, `get_settings()`.

### C.2. `data_source.py` — mechanizm danych zewnętrznych
Szczegóły w części D. Najważniejsze funkcje: `discover_latest_release`, `download_dataset`, `check_for_update`,
`get_local_dataset`, `list_local_releases`, `local_cities`, walidatory `validate_listings_file`, `validate_geojson_file`,
`validate_neighbourhoods_csv`. Wyjątki: `DataSourceError`, `DataValidationError`. Klasa `DatasetMetadata` opisuje wydanie.

### C.3. `cleaning.py` — czyszczenie
* `parse_price` — „$1,234.50”, „1.234,50 €”, „85,5” → liczby; puste/nieparsowalne → `NaN`.
* `clean_listings(raw, params)` → `(DataFrame, CleaningReport)`: deduplikacja po `id`, typy, ceny, współrzędne
  (zakres, (0,0), obwiednia miasta), flagi `coord_valid`, `has_price`, `price_outlier`, `price_per_person`.
* `flag_price_outliers` — płotki Tukeya na ln(ceny); oferty są **oznaczane**, nie usuwane.
* `CleaningReport.as_table()` — liczności po krokach (wyświetlane w „Dane i źródła”).
* `load_raw_listings` — czyta tylko potrzebne kolumny z `listings.csv.gz`.

### C.4. `spatial.py` — GIS
* `load_neighbourhoods` / `prepare_neighbourhoods` — wczytanie GeoJSON, ustawienie CRS (WGS 84), `make_valid`,
  `dissolve` powtórzonych nazw, powierzchnia `area_km2` w UTM.
* `make_points` — oferty z poprawnymi współrzędnymi → `GeoDataFrame` punktów (EPSG:4326).
* `assign_neighbourhoods` — **Spatial Join** (`sjoin`, predykat `within`); etykieta „(poza granicami dzielnic)”;
  kolumna kontrolna `label_matches_source`.
* `aggregate_neighbourhoods` — statystyki dzielnic (liczba, średnia, mediana, min, max, std, kwartyle, zagęszczenie,
  cena za osobę, udział całych mieszkań, ocena, obłożenie, `price_reliable`); zachowuje dzielnice bez ofert.
* `add_distance_to_center` — `dist_center_km` w UTM.
* `METRICS` — słownik miar kartogramów (etykieta, jednostka, opis).
* `project_crs` — automatyczny dobór układu UTM (`estimate_utm_crs`).

### C.5. `autocorr.py` — statystyka przestrzenna
* `compute_moran(districts, variable, method, permutations, seed, …)` → `MoranResult`:
  wybór dzielnic wiarygodnych, budowa wag, usunięcie wysp, Global Moran's I, Local Moran's I, klasyfikacja HH/LL/HL/LH,
  korekta FDR, wartości do wykresu rozrzutu (`z_value`, `z_lag`).
* `build_weights_without_islands` — wagi Queen/Rook/KNN, standaryzacja wierszowa, usuwanie jednostek bez sąsiadów.
* `InsufficientDataError` — zgłaszany przy < 8 jednostkach lub zmiennej stałej (zamiast liczenia niewiarygodnego wyniku).
* `MoranResult` — wynik: I, E[I], z, pseudo-p (dwustronne i jednostronne), `excluded` (kto i dlaczego wyłączony),
  `interpretation`, `districts` (wszystkie dzielnice z kolumnami LISA).
* `CLUSTER_LABELS`, `CLUSTER_COLORS`, `cluster_counts`, `interpret_global_moran`.

### C.6. `analysis.py` — orkiestracja
* `Filters` — niemutowalny zestaw filtrów (grupa/dzielnica, cena, typ pokoju i nieruchomości, goście, dostępność,
  opinie, wykluczenie odstających, wymóg ceny); `apply_filters` je stosuje.
* `prepare_dataset(meta, city)` → `Dataset` — cały pipeline przygotowania (wczytanie → czyszczenie → punkty →
  Spatial Join → odległość od centrum); niezależny od filtrów, więc w aplikacji liczony raz.
* `run_analysis(dataset, settings, filters, variable, method)` → `AnalysisResult` — filtry → agregacja → Moran/LISA.
* `location_effect` — OLS (numpy) i Spearman: ile zmienności ln(ceny) tłumaczy lokalizacja.
* `outlier_impact` — porównanie wyników z i bez ofert odstających.

### C.7. `visualization.py` — wizualizacje
* Folium: `offers_map` (punkty/klastry/heatmapa), `choropleth_map` (klasy kwantylowe, legenda), `lisa_map`.
* Plotly: `price_histogram` (log10 w binach), `price_box`, `district_bar`, `price_vs_distance`,
  `price_by_accommodates`, `moran_scatterplot`.

### C.8. `export.py` i `qgis_project.py` — QGIS
* `export_all(result, dataset, dir)` — warstwy `oferty_punkty`, `dzielnice_granice`, `dzielnice_statystyki`,
  `lisa_klastry` w GPKG i GeoJSON (EPSG:4326), CSV z `latitude/longitude`, `moran_globalny.csv`, `eksport_opis.json`.
* `build_project(dir)` (PyQGIS) — buduje `georent.qgz`: kartogramy (graduated, kwantyle), LISA (categorized), oferty
  (kolor = cena), granice z etykietami, podkład OSM; ścieżki **względne**. Moduł nie importuje reszty projektu, więc
  działa pod systemowym Pythonem z QGIS.

### C.9. `ui.py`, `cli.py`
* `ui.py`: `run_app`, funkcje `page_*` (jedna na sekcję), `sidebar_filters`, cache: `load_dataset`
  (`st.cache_resource`) oraz `compute` i `compute_outlier_impact` (`st.cache_data`, klucz = filtry + parametry).
* `cli.py`: parser `argparse` i polecenia `download/update/analyze/export`.

---

## Część D. Dane zewnętrzne — mechanizm pobierania

### D.1. Źródło
**Inside Airbnb** (<https://insideairbnb.com/get-the-data/>) — niezależny projekt publikujący migawki ofert Airbnb dla
miast. Licencja **CC BY 4.0** (wymagane podanie źródła). **Nie ma klucza API ani logowania** — to zwykłe pliki
statyczne pod adresami HTTP. Aplikacja nie używa nieoficjalnego API Airbnb.

### D.2. Pobierane pliki

Adres: `https://data.insideairbnb.com/<kraj>/<region>/<miasto>/<RRRR-MM-DD>/…`

| Plik lokalny | Ścieżka zdalna | Zawartość | Użycie |
|---|---|---|---|
| `listings.csv.gz` | `data/listings.csv.gz` | ~90 kolumn: cena, typ pokoju, goście, współrzędne, dostępność, oceny, obłożenie | tabela ofert |
| `neighbourhoods.geojson` | `visualisations/neighbourhoods.geojson` | poligony dzielnic (`neighbourhood`, `neighbourhood_group`) | warstwa poligonowa |
| `neighbourhoods.csv` | `visualisations/neighbourhoods.csv` | słownik dzielnic | walidacja formatu |

Świadomie **nie** pobieramy `calendar.csv` i `reviews.csv` (setki MB, nie są potrzebne do pytań projektu).

### D.3. Przebieg `download_dataset`
1. **Ustalenie wydania:** `discover_latest_release` pobiera stronę indeksu i wyszukuje w niej adresy miasta z datą
   (regex), bierze najnowszą. Przy niedostępnym indeksie używana jest `fallback_release` z `config.py`.
2. **Cache:** jeśli `data/raw/<miasto>/<wydanie>/` ma komplet plików i `metadata.json`, nic nie jest pobierane
   (chyba że `--force`).
3. **Pobieranie strumieniowe** każdego pliku do katalogu tymczasowego `.<wydanie>.partial` (limit czasu z
   `GEORENT_HTTP_TIMEOUT`); błędy HTTP/timeout/połączenia zamieniane na `DataSourceError` z czytelnym komunikatem.
4. **Walidacja:** gzip i CSV czytelne, wymagane kolumny (`id, latitude, longitude, price, room_type, property_type,
   accommodates, neighbourhood_cleansed`), niepusta liczba wierszy; GeoJSON to `FeatureCollection` z poligonami i
   atrybutem `neighbourhood`.
5. **Metadane:** `metadata.json` — miasto, wydanie, URL źródła, licencja, **data pobrania (UTC)**, rozmiar i **SHA-256**
   każdego pliku, liczba ofert i dzielnic.
6. **Atomowość:** dopiero po poprawnej walidacji katalog tymczasowy jest przemianowywany na docelowy. Przerwane lub
   błędne pobieranie nie zostawia niekompletnych danych.

### D.4. Struktura lokalna
```text
data/raw/barcelona/2026-09-17/
  listings.csv.gz  neighbourhoods.geojson  neighbourhoods.csv  metadata.json
```
Aplikacja zawsze używa najnowszego **kompletnego** wydania wybranego miasta; starsze wydania mogą współistnieć.

### D.5. Aktualizacja
`python -m georent update` lub przycisk „Sprawdź i pobierz nowsze wydanie” (porównuje datę z indeksu z lokalną);
„Pobierz ponownie (wymuś)” odświeża bieżące wydanie.

### D.6. Zewnętrzne usługi w czasie działania
* **Kafelki OpenStreetMap** — podkład map Folium i QGIS (wymaga Internetu; dane analityczne od niego nie zależą).
* Poza tym brak innych usług sieciowych. (Prototyp `poc/opensky_app.py` korzysta z API OpenSky Network — to osobna aplikacja.)

---

## Część E. Dane i mechanizmy przestrzenne

### E.1. Warstwy
| Warstwa | Typ | CRS | Źródło |
|---|---|---|---|
| Oferty | punkty | EPSG:4326 | kolumny `longitude`, `latitude` z `listings` |
| Dzielnice | (multi)poligony | EPSG:4326 | `neighbourhoods.geojson` |

### E.2. Układy współrzędnych
Dane i eksport są w **EPSG:4326** (WGS 84, stopnie). Do wszystkiego, co wymaga metrów (powierzchnia, odległość, centroidy
KNN), geometrie są przeliczane do metrycznego układu **UTM** dobranego automatycznie (`estimate_utm_crs`; dla Barcelony
EPSG:32631). Dzięki temu pola i odległości nie są zniekształcone, a dane źródłowe pozostają w standardowym układzie.

### E.3. Spatial Join
Dla każdej oferty szukany jest poligon, który ją **zawiera** (`within`). Wynik to kolumna `neighbourhood`. Oferta bez
dopasowania dostaje etykietę „(poza granicami dzielnic)” i nie wchodzi do statystyk. Punkt leżący dokładnie na granicy nie
jest zduplikowany (predykat `within` go nie obejmie, więc trafia do „poza granicami”). Kontrola: odsetek zgodności z polem
`neighbourhood_cleansed` z pliku źródłowego (dla Barcelony 100%).

### E.4. Naprawa i scalanie geometrii
Niepoprawne poligony są naprawiane (`shapely.make_valid`). Dzielnice opisane kilkoma obiektami o tej samej nazwie
(Vallvidrera) są scalane (`dissolve`), żeby jedna dzielnica = jedna jednostka analizy.

### E.5. Agregacja i kartogramy
Statystyki liczone są `groupby` po dzielnicy i łączone z poligonami (left join — dzielnice bez ofert zostają). Kartogramy
mają 5 klas kwantylowych (`mapclassify.Quantiles`), legendę z jednostką, szare „brak danych”. Zagęszczenie
(oferty/km²) służy do porównań między dzielnicami o różnej powierzchni.

### E.6. Sąsiedztwo i autokorelacja
Macierz wag (libpysal): **Queen** (domyślnie), **Rook** lub **KNN k = 5**, standaryzacja wierszowa. Wagi budowane są
tylko dla dzielnic z wiarygodną ceną; wyspy (bez sąsiadów) są usuwane i raportowane. Następnie `esda.Moran` (globalny) i
`esda.Moran_Local` (lokalny, LISA) z testem permutacyjnym. Kluczowe zasady: ziarno losowe ustala powtarzalność;
decyzje opierają się na pseudo-p **dwustronnym**; klastry mają dodatkową flagę FDR. Pełne uzasadnienie — `METODOLOGIA.md`.

### E.7. Eksport GIS
GeoPackage (`georent.gpkg`) zbiera 4 warstwy; te same warstwy są w GeoJSON; CSV zawiera współrzędne do wczytania jako
warstwa tekstowa. Pola mają czytelne nazwy (słownik w `QGIS.md`); wartości logiczne zapisywane są jako 0/1.

---

## Część F. Narzędzia i biblioteki (po co każda)

| Narzędzie | Rola w projekcie |
|---|---|
| **Streamlit** | interfejs webowy: sekcje, filtry, cache (`st.cache_data`, `st.cache_resource`), przyciski |
| **streamlit-folium / Folium** | interaktywne mapy Leaflet: punkty, klastry, heatmapa, kartogramy, LISA |
| **Plotly** | wykresy: histogramy, boxploty, wykres rozrzutu Morana |
| **pandas / NumPy / SciPy** | tabele, obliczenia (OLS przez `lstsq`), korelacja Spearmana |
| **GeoPandas** | wektory: `GeoDataFrame`, `sjoin`, `dissolve`, reprojekcje, zapis GPKG/GeoJSON |
| **Shapely** | geometrie, `make_valid`, predykaty |
| **pyogrio / pyproj** | odczyt/zapis GDAL/OGR (GPKG), transformacje układów współrzędnych |
| **libpysal** | macierze wag przestrzennych (Queen, Rook, KNN), lag przestrzenny |
| **esda** | Moran's I, Local Moran's I, korekta FDR |
| **mapclassify** | klasyfikacja kwantylowa dla kartogramów |
| **requests** | pobieranie plików i indeksu Inside Airbnb |
| **pytest** | testy (103); sieć w testach zastępują atrapy |
| **QGIS / PyQGIS** | wizualizacja wyników; programowe budowanie `georent.qgz` ze stylami |
| **Docker / Compose** | powtarzalne środowisko, wolumeny na dane i eksport, healthcheck |
| **Matplotlib** | statyczne mapy do dokumentacji (`scripts/generate_results.py`) |
| **Playwright (poza repo)** | użyty jednorazowo do wykonania zrzutów ekranu; nie jest zależnością projektu |

Wersje bibliotek są przypięte w `requirements.txt`.

---

## Część G. Testy

`python -m pytest` uruchamia 103 testy (`tests/`):

| Plik | Zakres |
|---|---|
| `test_data_source.py` | pobieranie z atrapą sieci: sukces, cache, `--force`, 404, 5xx, timeout, uszkodzony gzip, brak kolumn, zły GeoJSON, aktualizacja |
| `test_cleaning.py` | formaty cen, braki, duplikaty, współrzędne, odstające, pusty zbiór |
| `test_spatial.py` | punkty, Spatial Join (poza granicami, granica, CRS), agregacje (dokładne wartości, dzielnice bez ofert), naprawa geometrii, `dissolve` |
| `test_autocorr.py` | Moran I zgodny z ręcznym wzorem, dodatnia/ujemna/brak autokorelacji, LISA, wyspy, za mało jednostek, powtarzalność |
| `test_analysis_export.py` | filtry, pipeline, wpływ lokalizacji, eksport (CRS, pola), CLI, projekt QGIS |
| `test_real_data.py` | test integracyjny na pobranych danych Barcelony (pomijany, gdy danych brak) |

Dane w testach jednostkowych są małymi syntetycznymi siatkami (to wyłącznie fixture’y testowe); wyniki analizy w
dokumentacji pochodzą z rzeczywistych danych.

---

## Część H. Jak to się łączy — przykład jednego kliknięcia

Użytkownik wybiera filtr „Typ pokoju = Private room” w aplikacji:
1. `ui.sidebar_filters` buduje `Filters(room_types=("Private room",), …)`.
2. `ui.compute` (cache po filtrach i parametrach) woła `analysis.run_analysis`.
3. `apply_filters` zawęża punkty → `aggregate_neighbourhoods` liczy statystyki wszystkich dzielnic →
   `compute_moran` liczy I i LISA na nowych wartościach.
4. Wynik (`AnalysisResult`) trafia do sekcji: KPI, kartogramy, mapa LISA, tabele.
5. Eksport z sekcji „Dane i źródła” zapisuje dokładnie ten przefiltrowany stan do GPKG/GeoJSON/CSV.

---

## Część I. Gdzie szukać dalej

* Metody i ograniczenia: `docs/METODOLOGIA.md`
* Wyniki liczbowe i mapy: `docs/WYNIKI.md`
* QGIS i słownik pól: `docs/QGIS.md`
* Opis projektu, architektura, ograniczenia: `PROJEKT.md`
