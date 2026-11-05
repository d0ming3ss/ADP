## Metodologia

Poniższy opis dotyczy zastosowanych metod, przyjętych parametrów oraz ograniczeń interpretacyjnych. Wartości liczbowe
uzyskane na rzeczywistych danych znajdują się w `docs/WYNIKI.md` (generowanym automatycznie) i w sekcji
„Analiza przestrzenna” aplikacji.

### 1. Dane

* **Źródło:** Inside Airbnb (<https://insideairbnb.com/get-the-data/>), licencja CC BY 4.0. Dane są migawką ofert z dnia
  zbierania danych (data wydania jest zapisana w `data/raw/<miasto>/<wydanie>/metadata.json` i wyświetlana w aplikacji).
* **Wykorzystane pliki:** `listings.csv.gz` (pełna tabela ofert), `neighbourhoods.geojson` (granice dzielnic — poligony),
  `neighbourhoods.csv` (słownik dzielnic).
* **Pominięte pliki:** `calendar.csv` i `reviews.csv` mają rozmiar rzędu setek MB, a pytania projektu dotyczą rozkładu cen
  i ofert w przestrzeni. Dostępność (`availability_365`, `has_availability`), liczba opinii i szacowane obłożenie są już w
  `listings`, więc dodatkowe pliki nie wniosłyby wartości.
* **Wybór miasta (Barcelona):** miasto ma komplet plików, ok. 15 tys. ofert oraz granice dzielnic w układzie WGS 84
  (73 dzielnice po scaleniu; ~50–70 jednostek jest wystarczające dla statystyk autokorelacji, w odróżnieniu np. od Paryża
  z 20 arrondissements). Miasto można zmienić zmienną `GEORENT_CITY` lub parametrem `--city`.
* **Walidacja pobranych plików:** czytelność gzip/CSV, obecność wymaganych kolumn, niepustość, poprawność GeoJSON
  (`FeatureCollection` z poligonami i atrybutem `neighbourhood`). Pliki trafiają do katalogu tymczasowego i są
  przenoszone do docelowego dopiero po przejściu walidacji — przerwane pobieranie nie zostawia niekompletnych danych.

### 2. Czyszczenie danych

Kolejne kroki (liczności po każdym kroku wyświetla sekcja „Dane i źródła”):

1. usunięcie duplikatów (po `id`);
2. konwersja typów (liczby, wartości logiczne `t/f`);
3. **konwersja cen:** usunięcie symboli walut i spacji (także niełamliwych), obsługa separatora tysięcy (`1,234.50`,
   `1.234,50`) i przecinka dziesiętnego; wartość pusta lub nieparsowalna daje `NaN` (nigdy wartość domyślna),
   cena ≤ 0 jest uznawana za niepoprawną;
4. **współrzędne:** zakres (−90…90, −180…180), odrzucenie (0, 0) oraz punktów poza obwiedniarką dzielnic powiększoną o
   0,1°; rekordy niepoprawne są oznaczane (`coord_valid`), a nie kasowane;
5. **oferty odstające cenowo:** reguła Tukeya na logarytmie ceny (granice Q1 − 1,5·IQR i Q3 + 1,5·IQR dla ln ceny). Oferty
   są tylko **oznaczane** (`price_outlier`). Filtr „Wyklucz oferty o cenach odstających” pozwala ocenić wpływ ich usunięcia;
   sekcja „Analiza przestrzenna” zestawia wyniki z i bez nich;
6. cena za osobę = cena / `accommodates`.

Waluta: dane Inside Airbnb zapisują ceny z symbolem `$`. Cen nie przeliczano — wszystkie wnioski są względne
(porównania między dzielnicami, autokorelacja), więc nie zależą od kursu.

### 3. Operacje GIS

* Oferty tworzą warstwę punktową (`geopandas.points_from_xy`, EPSG:4326). Dzielnice tworzą warstwę poligonową (EPSG:4326).
* **Naprawa geometrii:** poligony niepoprawne (`is_valid = False`) są naprawiane funkcją `shapely.make_valid`. W danych
  Barcelony część dzielnicy (Vallvidrera) jest opisana kilkoma obiektami o tej samej nazwie — są scalane (`dissolve`),
  aby każda dzielnica była jedną jednostką analizy (bez tego liczby ofert byłyby zawyżone przy złączeniu po nazwie).
* **Spatial Join:** `geopandas.sjoin(punkty, poligony, predicate="within")`. Oferta bez dzielnicy (poza poligonami)
  otrzymuje etykietę „(poza granicami dzielnic)” i nie wchodzi do statystyk dzielnic. Poprawność złączenia jest sprawdzana
  względem etykiety `neighbourhood_cleansed` z pliku źródłowego (odsetek zgodności w sekcji „Dane i źródła”).
* **Powierzchnia i zagęszczenie:** powierzchnie liczone w metrycznym układzie UTM dobranym automatycznie do obszaru
  (`estimate_utm_crs`, dla Barcelony EPSG:32631 — WGS 84 / UTM strefa 31N); zagęszczenie = liczba ofert / powierzchnia [km²].
* **Odległość od centrum** — odległość euklidesowa w UTM od punktu centralnego zdefiniowanego w konfiguracji miasta
  (Barcelona: Plaça de Catalunya) lub od środka ciężkości dzielnic.

### 4. Statystyki dzielnic i kartogramy

Dla każdej dzielnicy liczone są: liczba ofert, liczba ofert z ceną, średnia, mediana, minimum, maksimum, odchylenie
standardowe i kwartyle ceny, zagęszczenie, mediana ceny za osobę, udział całych mieszkań, średnia ocena i szacowane
obłożenie. Dzielnice bez ofert pozostają w wyniku (liczba = 0, statystyki cenowe puste).

**Próg wiarygodności:** statystyki cenowe dzielnicy uznaje się za wiarygodne od 10 ofert z ceną (`GEORENT_MIN_LISTINGS`).
Dzielnice poniżej progu są wyłączone z rankingów cen i z autokorelacji — mediana z kilku ofert jest zbyt niestabilna.

**Kartogramy** używają klas kwantylowych (5 klas; `mapclassify.Quantiles`), co jest właściwe dla rozkładów silnie
skośnych. Liczba ofert zależy od powierzchni dzielnicy, dlatego obok niej prezentowane jest zagęszczenie (oferty/km²).

### 5. Macierz wag przestrzennych — uzasadnienie wyboru

Domyślnie stosowane jest **sąsiedztwo Queen** (wspólny bok lub wierzchołek) ze standaryzacją **wierszową**:

* jednostkami analizy są poligony administracyjne, więc sąsiedztwo konturowe (ang. *contiguity*) odpowiada intuicji
  „sąsiednie dzielnice”; nie wymaga arbitralnego progu odległości (w odróżnieniu od wag odległościowych), a dzielnice o
  bardzo różnej powierzchni nie mają sztucznie różnej liczby sąsiadów (co dotyczyłoby okręgu o stałym promieniu);
* Queen jest standardem w analizie obszarowej; różnica względem Rook (wspólny bok) bywa niewielka — w danych
  Barcelony oba warianty dały identyczną liczbę sąsiadów i wynik;
* standaryzacja wierszowa sprawia, że lag przestrzenny jest średnią wartości sąsiadów;
* **odporność:** aplikacja pozwala przełączyć macierz na Rook lub **k najbliższych sąsiadów** (k = 5, centroidy w UTM), a
  sekcja „Analiza przestrzenna” zestawia wszystkie trzy warianty.

**Dzielnice bez sąsiadów (wyspy):** Moran's I nie jest dla nich zdefiniowany. Wagi budowane są dla dzielnic z wiarygodną
ceną; jednostki bez sąsiadów w tym zbiorze są usuwane i wyświetlane z powodem wyłączenia. Graf sąsiedztwa może składać się
z kilku rozłącznych składowych (biblioteka libpysal zgłasza to ostrzeżeniem) — statystyka pozostaje poprawna, jednak
składowe nie wymieniają informacji. **Brakujące wartości:** dzielnice bez wartości zmiennej są wyłączone przed budową wag.
Minimalna liczba jednostek wymagana do analizy: 8; poniżej aplikacja zwraca komunikat zamiast liczyć niewiarygodny wynik.

### 6. Global Moran's I

Statystyka:

$$ I = \frac{n}{\sum_i\sum_j w_{ij}} \cdot \frac{\sum_i\sum_j w_{ij}(x_i-\bar x)(x_j-\bar x)}{\sum_i (x_i-\bar x)^2} $$

Wartość oczekiwana przy braku autokorelacji: E[I] = −1/(n−1). Istotność jest wyznaczana **testem permutacyjnym**
(`esda.Moran`, 999 permutacji): wartości zmiennej są losowo przestawiane między jednostkami, a pseudo-p =
(M + 1)/(N + 1), gdzie M to liczba permutacji o statystyce co najmniej tak skrajnej jak obserwowana, a N liczba permutacji.
`esda.Moran` zwraca pseudo-p jednostronne (w kierunku znaku I); aplikacja raportuje i stosuje do decyzji
**wartość dwustronną** (2 × jednostronne, nie więcej niż 1), co jest podejściem ostrożniejszym. Przy 999 permutacjach
najmniejsza osiągalna wartość dwustronna wynosi 0,002. Ziarno losowe (domyślnie 42) zapewnia powtarzalność. Dodatkowo
podawane jest p z aproksymacji normalnej (`p_norm`).

Interpretacja: **I > E[I]** i p < 0,05 — dodatnia autokorelacja (podobne wartości sąsiadują: skupiska); **I < E[I]** i
p < 0,05 — ujemna (szachownica); p ≥ 0,05 — brak podstaw do odrzucenia losowego rozmieszczenia.

### 7. Lokalne wskaźniki LISA (Local Moran's I)

Dla każdej dzielnicy liczone jest lokalne I (`esda.Moran_Local`, permutacje warunkowe, 999, hipoteza alternatywna dwustronna — `alternative="two-sided"`, zgodnie z
zaleceniem autorów biblioteki). Klasy wyznacza położenie
wartości standaryzowanej względem średniej oraz średniej sąsiadów (kwadranty wykresu rozrzutu Morana), pod warunkiem
istotności lokalnej pseudo-p < 0,05:

| Klasa | Znaczenie |
|---|---|
| High-High | wysoka wartość otoczona wysokimi (gorący punkt) |
| Low-Low | niska wśród niskich (zimny punkt) |
| High-Low | wysoka wśród niskich (odstająca) |
| Low-High | niska wśród wysokich (odstająca) |
| Brak istotności | brak lokalnej autokorelacji istotnej statystycznie |

**Wielokrotne testowanie:** przy ok. 50 jednostkach test 5% daje średnio kilka fałszywych alarmów. Dlatego do wyników
dołączana jest flaga `lisa_sig_fdr` (korekta Benjaminiego–Hochberga, `esda.fdr`). Klastry istotne tylko w teście
nieskorygowanym należy traktować jako wskazówki eksploracyjne.

### 8. Wpływ lokalizacji na cenę

Oszacowano modele OLS dla ln(ceny): (a) cechy oferty (liczba gości, typ pokoju), (b) sama dzielnica (zmienne
zero-jedynkowe), (c) cechy oferty + odległość od centrum, (d) cechy oferty + dzielnica; porównywane jest skorygowane R².
Dodatkowo liczony jest współczynnik korelacji rang Spearmana między ceną a odległością od centrum. Wynik opisuje związek
statystyczny — **nie wskazuje przyczynowości**.

### 9. Ograniczenia i możliwe błędy interpretacyjne

* **Cena oferty ≠ cena transakcji.** Inside Airbnb publikuje cenę wywoławczą z dnia zbierania danych; oferty bez ceny
  (w Barcelonie 17% rekordów) są pomijane w statystykach cen, ale liczone w liczbie ofert.
* **Migawka**, nie szereg czasowy: sezonowość i zmiany regulacji nie są modelowane.
* **MAUP (problem zmiennej jednostki powierzchniowej):** wyniki zależą od przyjętego podziału na dzielnice; inna granica
  mogłaby zmienić wartość I.
* **Błąd ekologiczny:** wniosek o dzielnicy nie musi być prawdziwy dla pojedynczej oferty. Sama dzielnica tłumaczy
  niewielką część zmienności cen na poziomie ofert (patrz `docs/WYNIKI.md`) — większość zmienności wynika z cech oferty.
* **Lokalizacja przybliżona:** zgodnie z informacją Inside Airbnb współrzędne ofert są celowo przesuwane o rząd 150 m;
  oferty przy granicy dzielnic mogą być przypisane do sąsiedniej jednostki.
* **Małe próby:** mediany dzielnic z kilkunastu ofert są niestabilne — stąd próg wiarygodności; klastry oparte na
  dzielnicach o małej liczbie ofert należy interpretować ostrożnie.
* **Wyłączone dzielnice:** dzielnice peryferyjne o małej liczbie ofert nie biorą udziału w analizie autokorelacji, więc wynik
  dotyczy głównie zurbanizowanej części miasta.
* **Autokorelacja ≠ przyczynowość.** Skupisko wysokich cen nie wyjaśnia, dlaczego powstało (centralność, turystyka, regulacje).
* **Reprezentatywność:** dane obejmują wyłącznie oferty na jednej platformie.

### 10. Powtarzalność

Wynik jest deterministyczny przy ustalonych: wydaniu danych (hash SHA-256 plików w `metadata.json`), wersjach
bibliotek (`requirements.txt`), progach czyszczenia, macierzy wag, liczbie permutacji i ziarnie (`GEORENT_SEED`).
