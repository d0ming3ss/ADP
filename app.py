"""GeoRent-AI PoC: live air traffic over Europe from the OpenSky Network API."""

from __future__ import annotations

from datetime import datetime, timezone

import folium
import pandas as pd
import requests
import streamlit as st
from streamlit_folium import st_folium

# --- Configuration -----------------------------------------------------------

OPENSKY_URL = "https://opensky-network.org/api/states/all"
REQUEST_TIMEOUT_S = 15
CACHE_TTL_S = 60

# Bounding box roughly covering Europe (lat/lon in degrees).
EUROPE_BBOX = {"lamin": 34.0, "lomin": -25.0, "lamax": 72.0, "lomax": 45.0}
MAP_CENTER = (52.0, 15.0)

# Column order of a state vector as documented by OpenSky.
STATE_COLUMNS = [
    "icao24", "callsign", "origin_country", "time_position", "last_contact",
    "longitude", "latitude", "baro_altitude", "on_ground", "velocity",
    "true_track", "vertical_rate", "sensors", "geo_altitude", "squawk",
    "spi", "position_source", "category",
]


class OpenSkyError(Exception):
    """Raised when the OpenSky API cannot deliver usable data."""


# --- Data layer --------------------------------------------------------------

@st.cache_data(ttl=CACHE_TTL_S, show_spinner=False)
def fetch_states(bbox: dict[str, float]) -> tuple[pd.DataFrame, int]:
    """Fetch aircraft state vectors within ``bbox``; return (DataFrame, API timestamp)."""
    try:
        response = requests.get(OPENSKY_URL, params=bbox, timeout=REQUEST_TIMEOUT_S)
    except requests.exceptions.Timeout as exc:
        raise OpenSkyError(
            f"Przekroczono limit czasu ({REQUEST_TIMEOUT_S} s) oczekiwania na odpowiedź OpenSky."
        ) from exc
    except requests.exceptions.ConnectionError as exc:
        raise OpenSkyError("Brak połączenia z API OpenSky. Sprawdź dostęp do internetu.") from exc
    except requests.exceptions.RequestException as exc:
        raise OpenSkyError(f"Nieoczekiwany błąd zapytania: {exc}") from exc

    if response.status_code == 429:
        retry_after = response.headers.get("X-Rate-Limit-Retry-After-Seconds")
        hint = f" Spróbuj ponownie za ok. {retry_after} s." if retry_after else ""
        raise OpenSkyError(
            "Przekroczono limit zapytań do API OpenSky (HTTP 429)." + hint
        )
    if response.status_code != 200:
        raise OpenSkyError(f"API OpenSky zwróciło błąd HTTP {response.status_code}.")

    try:
        payload = response.json()
    except ValueError as exc:
        raise OpenSkyError("API OpenSky zwróciło niepoprawny JSON.") from exc

    return states_to_dataframe(payload.get("states") or []), payload.get("time", 0)


def states_to_dataframe(states: list[list]) -> pd.DataFrame:
    """Convert raw OpenSky state vectors into a clean DataFrame."""
    if not states:
        return pd.DataFrame(columns=STATE_COLUMNS)

    # Responses have 17 or 18 fields depending on whether `category` is included.
    width = len(states[0])
    df = pd.DataFrame(states, columns=STATE_COLUMNS[:width])

    df = df.dropna(subset=["latitude", "longitude"])
    df["callsign"] = df["callsign"].fillna("").str.strip()
    df["on_ground"] = df["on_ground"].fillna(False).astype(bool)
    df["altitude_m"] = df["geo_altitude"].fillna(df["baro_altitude"])
    df["velocity_kmh"] = df["velocity"] * 3.6
    return df.reset_index(drop=True)


def filter_states(df: pd.DataFrame, countries: list[str], include_ground: bool) -> pd.DataFrame:
    """Apply UI filters to the state DataFrame."""
    if countries:
        df = df[df["origin_country"].isin(countries)]
    if not include_ground:
        df = df[~df["on_ground"]]
    return df


# --- Presentation layer ------------------------------------------------------

def altitude_color(altitude_m: float | None, on_ground: bool) -> str:
    """Pick a marker colour based on flight altitude."""
    if on_ground or pd.isna(altitude_m):
        return "gray"
    if altitude_m < 3000:
        return "green"
    if altitude_m < 9000:
        return "orange"
    return "red"


def build_map(df: pd.DataFrame, max_markers: int) -> folium.Map:
    """Build a Folium map with one circle marker per aircraft."""
    fmap = folium.Map(location=MAP_CENTER, zoom_start=4, tiles="OpenStreetMap")
    folium.Rectangle(
        bounds=[[EUROPE_BBOX["lamin"], EUROPE_BBOX["lomin"]],
                [EUROPE_BBOX["lamax"], EUROPE_BBOX["lomax"]]],
        color="#3388ff", weight=1, fill=False, dash_array="5",
        tooltip="Obszar zapytania (Europa)",
    ).add_to(fmap)

    for row in df.head(max_markers).itertuples(index=False):
        alt = "brak" if pd.isna(row.altitude_m) else f"{row.altitude_m:,.0f} m"
        spd = "brak" if pd.isna(row.velocity_kmh) else f"{row.velocity_kmh:,.0f} km/h"
        popup = (
            f"<b>{row.callsign or row.icao24}</b><br>"
            f"Kraj: {row.origin_country}<br>"
            f"Wysokość: {alt}<br>Prędkość: {spd}"
        )
        folium.CircleMarker(
            location=(row.latitude, row.longitude),
            radius=3,
            color=altitude_color(row.altitude_m, row.on_ground),
            fill=True,
            fill_opacity=0.8,
            weight=1,
            tooltip=row.callsign or row.icao24,
            popup=folium.Popup(popup, max_width=220),
        ).add_to(fmap)

    return fmap


def render_sidebar(all_countries: list[str]) -> tuple[list[str], bool, int]:
    """Render filter widgets and return the selected values."""
    st.sidebar.header("Filtry")
    countries = st.sidebar.multiselect(
        "Kraj pochodzenia (origin_country)", options=all_countries,
        help="Pozostaw puste, aby pokazać wszystkie kraje.",
    )
    include_ground = st.sidebar.checkbox("Pokaż samoloty na ziemi", value=False)
    max_markers = st.sidebar.slider(
        "Maks. liczba punktów na mapie", min_value=100, max_value=5000,
        value=1500, step=100,
        help="Ogranicza liczbę markerów dla płynności działania mapy.",
    )
    st.sidebar.markdown(
        "**Legenda wysokości**  \n"
        "🟢 < 3 000 m · 🟠 3 000–9 000 m · 🔴 > 9 000 m · ⚪ na ziemi / brak"
    )
    return countries, include_ground, max_markers


def render_metrics(df: pd.DataFrame) -> None:
    """Show summary statistics for the filtered data."""
    airborne = df[~df["on_ground"]]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Liczba samolotów", f"{len(df):,}")
    c2.metric("W powietrzu", f"{len(airborne):,}")
    c3.metric(
        "Śr. wysokość [m]",
        f"{airborne['altitude_m'].mean():,.0f}" if airborne["altitude_m"].notna().any() else "—",
    )
    c4.metric(
        "Śr. prędkość [km/h]",
        f"{airborne['velocity_kmh'].mean():,.0f}" if airborne["velocity_kmh"].notna().any() else "—",
    )

    c5, c6 = st.columns(2)
    c5.metric("Liczba krajów pochodzenia", df["origin_country"].nunique())
    top = df["origin_country"].value_counts()
    c6.metric("Najczęstszy kraj", top.index[0] if not top.empty else "—",
              f"{top.iloc[0]} samolotów" if not top.empty else None, delta_color="off")


def render_table(df: pd.DataFrame) -> None:
    """Show a detail table of the filtered aircraft."""
    table = df[[
        "icao24", "callsign", "origin_country", "latitude", "longitude",
        "altitude_m", "velocity_kmh", "true_track", "vertical_rate", "on_ground",
    ]].rename(columns={
        "icao24": "ICAO24", "callsign": "Znak wywoławczy", "origin_country": "Kraj",
        "latitude": "Szer. geogr.", "longitude": "Dł. geogr.",
        "altitude_m": "Wysokość [m]", "velocity_kmh": "Prędkość [km/h]",
        "true_track": "Kurs [°]", "vertical_rate": "Pr. pionowa [m/s]",
        "on_ground": "Na ziemi",
    })
    st.dataframe(table, width="stretch", hide_index=True)


# --- App ---------------------------------------------------------------------

def main() -> None:
    st.set_page_config(page_title="GeoRent-AI", page_icon="✈️", layout="wide")
    st.title("✈️ GeoRent-AI — ruch lotniczy nad Europą (PoC)")
    st.caption("Dane na żywo z OpenSky Network API · analiza danych przestrzennych")

    if st.sidebar.button("🔄 Odśwież dane z API", width="stretch"):
        fetch_states.clear()

    try:
        with st.spinner("Pobieranie danych z OpenSky..."):
            states, api_time = fetch_states(EUROPE_BBOX)
    except OpenSkyError as exc:
        st.error(f"Nie udało się pobrać danych: {exc}")
        st.info("Dane są cache'owane przez 60 s. Odczekaj chwilę i kliknij „Odśwież dane z API”.")
        st.stop()

    if states.empty:
        st.warning("API nie zwróciło żadnych samolotów w wybranym obszarze.")
        st.stop()

    countries, include_ground, max_markers = render_sidebar(
        sorted(states["origin_country"].dropna().unique())
    )
    filtered = filter_states(states, countries, include_ground)

    if api_time:
        ts = datetime.fromtimestamp(api_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        st.caption(f"Stan danych: {ts}")

    if len(filtered) > max_markers:
        st.info(f"Na mapie pokazano {max_markers:,} z {len(filtered):,} samolotów (zmień limit w panelu bocznym).")

    st_folium(build_map(filtered, max_markers), height=550, use_container_width=True,
              returned_objects=[])

    st.subheader("Statystyki")
    render_metrics(filtered)

    st.subheader("Szczegóły samolotów")
    render_table(filtered)


if __name__ == "__main__":
    main()
