# Wyniki w QGIS — instrukcja

Eksport wyników tworzy katalog `exports/<miasto>/` (np. `exports/barcelona/`). Wszystkie warstwy mają układ współrzędnych
**EPSG:4326 (WGS 84)**; projekt QGIS wyświetla je w EPSG:3857 (przeliczenie „w locie”).

## 1. Jak wygenerować eksport

```bash
# z poziomu środowiska wirtualnego (po pobraniu danych):
python -m georent export --city barcelona
# opcje: --weights queen|rook|knn   --variable median_price   --exclude-outliers   --output <katalog>
```

Eksport można też wykonać przyciskiem **„Wygeneruj eksport”** w sekcji „Dane i źródła” aplikacji (uwzględnia aktualne
filtry; plik `.qgz` jest wtedy pomijany — generuje go polecenie CLI).

Polecenie CLI wywołuje dodatkowo `python3 -m georent.qgis_project`, które na podstawie GeoPackage buduje gotowy projekt
`georent.qgz` (wymaga PyQGIS, tzn. zainstalowanego QGIS z systemowym Pythonem). Gdy PyQGIS jest niedostępny (np. w Dockerze),
pozostałe pliki powstają normalnie, a projekt można zbudować osobno na maszynie z QGIS:

```bash
QT_QPA_PLATFORM=offscreen /usr/bin/python3 -m georent.qgis_project exports/barcelona
```

## 2. Zawartość katalogu eksportu

| Plik | Opis |
|---|---|
| `georent.gpkg` | GeoPackage ze wszystkimi warstwami (zalecany format) |
| `georent.qgz` | gotowy projekt QGIS (ścieżki względne — katalog można przenosić) |
| `geojson/*.geojson` | te same warstwy w GeoJSON |
| `oferty_wspolrzedne.csv` | oferty z kolumnami `latitude`, `longitude` (do „Warstwa z tekstu rozdzielanego”) |
| `dzielnice_statystyki.csv` | tabela statystyk dzielnic (bez geometrii) |
| `moran_globalny.csv` | wynik Global Moran's I (I, E[I], z, p, parametry, interpretacja) |
| `eksport_opis.json` | źródło, wydanie danych, licencja, filtry i liczności warstw |

## 3. Otwarcie projektu

1. Uruchom QGIS (projekt wygenerowano i sprawdzono w QGIS 3.34) i wybierz **Projekt → Otwórz…**, wskaż `exports/<miasto>/georent.qgz`.
2. Podkład OpenStreetMap wczytuje się z Internetu; bez sieci warstwy są nadal widoczne.
3. Widoczne domyślnie: **Granice dzielnic** i **Kartogram — mediana ceny**. Pozostałe warstwy włączysz w panelu *Warstwy*.

Bez pliku `.qgz`: **Warstwa → Dodaj warstwę → Dodaj warstwę wektorową…**, wybierz `georent.gpkg` i zaznacz warstwy.

## 4. Warstwy i interpretacja

| Warstwa | Geometria | Znaczenie |
|---|---|---|
| `oferty_punkty` (Oferty najmu) | punkty | oferta; kolor = cena za noc (kwantyle) |
| `dzielnice_granice` (Granice dzielnic) | poligony | granice dzielnic z powierzchnią `area_km2` |
| `dzielnice_statystyki` (Kartogramy, LISA) | poligony | statystyki cenowe, zagęszczenie, Moran lokalny i klaster LISA |
| `lisa_klastry` | poligony | wyłącznie dzielnice w klastrach istotnych (HH, LL, HL, LH) |

W projekcie warstwa `dzielnice_statystyki` występuje kilka razy z różną symbolizacją: *Klastry LISA*, *Kartogram — mediana
ceny*, *Kartogram — zagęszczenie ofert*, *Kartogram — liczba ofert*.

### Pola warstwy `dzielnice_statystyki`

| Pole | Znaczenie |
|---|---|
| `neighbourhood`, `neighbourhood_group` | dzielnica i jej grupa (dystrykt) |
| `area_km2` | powierzchnia [km²] (UTM) |
| `n_listings`, `n_priced` | liczba ofert / ofert z ceną |
| `price_reliable` | 1, gdy `n_priced` ≥ próg wiarygodności (domyślnie 10) |
| `mean_price`, `median_price`, `min_price`, `max_price`, `std_price`, `p25_price`, `p75_price` | statystyki cen za noc |
| `density_km2` | oferty na km² |
| `median_price_pp`, `share_entire_home`, `mean_rating`, `mean_occupancy` | mediana ceny za osobę, % całych mieszkań, średnia ocena, szac. obłożenie roczne |
| `z_value`, `z_lag` | wartość standaryzowana i średnia sąsiadów (oś X i Y wykresu rozrzutu Morana) |
| `lisa_I`, `lisa_z`, `lisa_p` | Local Moran's I, z i pseudo-p z permutacji |
| `lisa_code` | `HH`, `LL`, `HL`, `LH`, `ns` (nieistotne), `n/a` (poza analizą) |
| `lisa_cluster` | pełna nazwa klasy |
| `lisa_sig_fdr` | 1, gdy klaster pozostaje istotny po korekcie FDR |

Interpretacja: **HH** — gorący punkt (drogie dzielnice wśród drogich); **LL** — zimny punkt; **HL / LH** — dzielnice
odstające od otoczenia; `n/a` — dzielnica wyłączona (za mało ofert z ceną lub brak sąsiadów). Szczegóły i ostrzeżenia
(wielokrotne testowanie, MAUP) — `docs/METODOLOGIA.md`.

## 5. Przydatne operacje w QGIS

* **Tabela atrybutów** → sortowanie po `lisa_p` lub `median_price`.
* **Filtr warstwy** (`Ctrl+F`): `"lisa_code" = 'HH'`.
* **Etykiety:** warstwa *Granice dzielnic* → Właściwości → Etykiety (pole `neighbourhood`; włącz etykietowanie).
* **Weryfikacja Spatial Join w QGIS:** *Wektor → Narzędzia do zarządzania danymi → Złącz atrybuty według lokalizacji*
  (oferty ⟶ granice); wynik powinien odpowiadać polu `neighbourhood` w `oferty_punkty`.
* **Weryfikacja Moran's I poza aplikacją:** `dzielnice_statystyki.csv` lub `georent.gpkg` można wczytać w GeoDa i porównać
  wynik z `moran_globalny.csv` (przy tych samych wagach Queen i standaryzacji wierszowej).
