"""Konfiguracja projektu: katalogi, parametry analizy i katalog obsługiwanych miast.

Wartości domyślne można nadpisać zmiennymi środowiskowymi (prefiks ``GEORENT_``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

INSIDE_AIRBNB_INDEX_URL = "https://insideairbnb.com/get-the-data/"
INSIDE_AIRBNB_DATA_URL = "https://data.insideairbnb.com"
DATA_LICENSE = "Creative Commons Attribution 4.0 International (CC BY 4.0) — Inside Airbnb"


@dataclass(frozen=True)
class CitySpec:
    """Opis miasta dostępnego w Inside Airbnb."""

    slug: str
    label: str
    path: str  # fragment URL: kraj/region/miasto
    fallback_release: str  # data wydania używana, gdy nie uda się odczytać indeksu
    center: tuple[float, float] | None = None  # (lat, lon) centrum miasta; None = środek ciężkości dzielnic


CITIES: dict[str, CitySpec] = {
    c.slug: c
    for c in (
        CitySpec("barcelona", "Barcelona", "spain/catalonia/barcelona", "2026-09-17", (41.3870, 2.1700)),
        CitySpec("lisbon", "Lizbona", "portugal/lisbon/lisbon", "2026-09-19", (38.7110, -9.1390)),
        CitySpec("amsterdam", "Amsterdam", "the-netherlands/north-holland/amsterdam", "2026-09-16", (52.3731, 4.8922)),
        CitySpec("berlin", "Berlin", "germany/be/berlin", "2026-09-19", (52.5200, 13.4050)),
        CitySpec("prague", "Praga", "czech-republic/prague/prague", "2026-09-20", (50.0875, 14.4213)),
        CitySpec("vienna", "Wiedeń", "austria/vienna/vienna", "2026-09-17", (48.2082, 16.3738)),
    )
}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Zmienna środowiskowa {name} musi być liczbą całkowitą, otrzymano: {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    """Ustawienia uruchomieniowe."""

    city: str = field(default_factory=lambda: os.environ.get("GEORENT_CITY", "barcelona").lower())
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("GEORENT_DATA_DIR", PROJECT_ROOT / "data")))
    export_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("GEORENT_EXPORT_DIR", PROJECT_ROOT / "exports"))
    )
    http_timeout_s: int = field(default_factory=lambda: _env_int("GEORENT_HTTP_TIMEOUT", 60))
    permutations: int = field(default_factory=lambda: _env_int("GEORENT_PERMUTATIONS", 999))
    seed: int = field(default_factory=lambda: _env_int("GEORENT_SEED", 42))
    # Minimalna liczba ofert w dzielnicy, by jej statystyki cenowe uczestniczyły w analizie autokorelacji.
    min_listings: int = field(default_factory=lambda: _env_int("GEORENT_MIN_LISTINGS", 10))

    def city_spec(self) -> CitySpec:
        return get_city(self.city)

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"


def get_city(slug: str) -> CitySpec:
    """Zwróć specyfikację miasta lub zgłoś czytelny błąd."""
    try:
        return CITIES[slug.lower()]
    except KeyError:
        raise ValueError(f"Nieznane miasto {slug!r}. Dostępne: {', '.join(sorted(CITIES))}") from None


def get_settings() -> Settings:
    return Settings()
