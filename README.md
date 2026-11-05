# GeoRent-AI

Przestrzenna analiza rynku najmu krótkoterminowego (Inside Airbnb) — projekt z przedmiotu *Analiza Danych Przestrzennych*.
Dane rzeczywiste (Barcelona, wydanie 2026-09-17), operacje GIS (Spatial Join, kartogramy), autokorelacja przestrzenna
(Global Moran's I, LISA — PySAL), aplikacja Streamlit i eksport do QGIS.

**Pełna dokumentacja: [PROJEKT.md](PROJEKT.md)** · **przewodnik po projekcie: [docs/INSTRUKCJA.md](docs/INSTRUKCJA.md)** · metodologia: [docs/METODOLOGIA.md](docs/METODOLOGIA.md) ·
QGIS: [docs/QGIS.md](docs/QGIS.md) · wyniki: [docs/WYNIKI.md](docs/WYNIKI.md)

## Szybki start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m georent download      # pobranie danych Inside Airbnb (~8 MB)
streamlit run app.py            # http://localhost:8501
```

## Docker

```bash
docker compose up --build       # http://localhost:8501 (dane w ./data, eksport w ./exports)
docker compose run --rm georent python -m georent download   # pobranie danych w kontenerze (albo przyciskiem w aplikacji)
```

## Przydatne polecenia

```bash
python -m georent update        # sprawdź i pobierz nowsze wydanie danych
python -m georent analyze       # wyniki analizy w terminalu
python -m georent export        # GPKG + GeoJSON + CSV + projekt QGIS (exports/<miasto>/)
python -m pytest                # testy
```

Prototyp z etapu 1 (OpenSky Network) znajduje się w `poc/opensky_app.py`.
