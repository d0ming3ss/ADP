# GeoRent-AI — PoC

Proof of Concept aplikacji do analizy danych przestrzennych, przygotowany na kurs
analizy danych przestrzennych. Aplikacja w **Streamlit** pobiera na żywo pozycje
samolotów nad Europą z darmowego API [OpenSky Network](https://opensky-network.org/)
i prezentuje je na interaktywnej mapie.

## Funkcje

- **Mapa** (Folium + streamlit-folium) z punktami samolotów nad Europą,
  kolorowanymi według wysokości lotu; popup z danymi lotu.
- **Filtry** w panelu bocznym: kraj pochodzenia (`origin_country`),
  samoloty na ziemi, limit liczby punktów na mapie.
- **Przycisk odświeżania** — wymusza nowe zapytanie do API (dane są cache'owane przez 60 s,
  aby nie przekraczać limitów OpenSky).
- **Wskaźniki statystyczne** (`st.metric`): liczba samolotów, średnia wysokość
  i prędkość, liczba krajów, najczęstszy kraj pochodzenia.
- **Tabela szczegółów** wszystkich przefiltrowanych samolotów.
- **Obsługa błędów**: HTTP 429 (limit zapytań), timeout, brak połączenia,
  inne kody HTTP i niepoprawny JSON.

## Struktura projektu

```
.
├── app.py            # aplikacja Streamlit (warstwa danych + prezentacji)
├── requirements.txt  # zależności Pythona
├── Dockerfile        # obraz python:3.10-slim, port 8501
└── README.md
```

## Uruchomienie w Dockerze

Zbudowanie obrazu:

```bash
docker build -t georent-ai .
```

Uruchomienie kontenera:

```bash
docker run --rm -p 8501:8501 georent-ai
```

Aplikacja będzie dostępna pod adresem: <http://localhost:8501>

## Uruchomienie lokalne (bez Dockera)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Uwagi

- Anonimowy dostęp do API OpenSky ma ograniczoną liczbę zapytań dziennie
  oraz rozdzielczość czasową ~10 s. Przy przekroczeniu limitu aplikacja
  wyświetli komunikat o błędzie 429.
- Zapytanie jest ograniczone do prostokąta obejmującego Europę
  (szer. 34°–72° N, dł. 25° W–45° E), co zmniejsza transfer i zużycie limitu.
