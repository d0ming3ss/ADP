"""Pobieranie, walidacja i lokalne składowanie danych Inside Airbnb.

Wykorzystywane pliki (na tle pozostałych zbiorów Inside Airbnb):

* ``data/listings.csv.gz`` — pełna tabela ofert (cena, typ pokoju, liczba osób, dostępność,
  oceny, szacowane obłożenie); zawiera wszystko, czego potrzebuje analiza;
* ``visualisations/neighbourhoods.geojson`` — granice dzielnic (poligony);
* ``visualisations/neighbourhoods.csv`` — słownik dzielnic i ich grup.

``calendar.csv`` oraz ``reviews.csv`` nie są pobierane: mają setki MB, a analiza ofert na
poziomie dzielnic nie wymaga ich treści (dostępność i liczba opinii są już w ``listings``).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from georent.config import (
    DATA_LICENSE,
    INSIDE_AIRBNB_DATA_URL,
    INSIDE_AIRBNB_INDEX_URL,
    CitySpec,
    Settings,
    get_city,
)

logger = logging.getLogger(__name__)

METADATA_FILE = "metadata.json"

# nazwa lokalna -> ścieżka względem katalogu wydania w serwisie
REMOTE_FILES: dict[str, str] = {
    "listings.csv.gz": "data/listings.csv.gz",
    "neighbourhoods.geojson": "visualisations/neighbourhoods.geojson",
    "neighbourhoods.csv": "visualisations/neighbourhoods.csv",
}

REQUIRED_LISTING_COLUMNS = frozenset(
    {
        "id", "latitude", "longitude", "price", "room_type", "property_type",
        "accommodates", "neighbourhood_cleansed",
    }
)


class DataSourceError(Exception):
    """Błąd pozyskania danych (sieć, brak pliku, nieobsługiwany format)."""


class DataValidationError(DataSourceError):
    """Pobrane dane mają niepoprawny format lub są niekompletne."""


@dataclass
class DatasetMetadata:
    """Informacje o pobranym wydaniu danych (zapisywane obok plików)."""

    city: str
    label: str
    release: str
    source_url: str
    index_url: str
    license: str
    downloaded_at: str
    files: dict[str, dict] = field(default_factory=dict)
    listings_rows: int = 0
    neighbourhood_features: int = 0
    directory: str = ""

    def path(self, name: str) -> Path:
        return Path(self.directory) / name


# --- Sieć -------------------------------------------------------------------------------------

def _session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = "GeoRent-AI/1.0 (projekt akademicki; analiza danych przestrzennych)"
    return session


def release_base_url(city: CitySpec, release: str) -> str:
    return f"{INSIDE_AIRBNB_DATA_URL}/{city.path}/{release}"


def discover_latest_release(
    city: CitySpec, session: requests.Session | None = None, timeout: int = 60
) -> str | None:
    """Odczytaj z indeksu Inside Airbnb najnowszą datę wydania dla miasta (``None`` przy braku)."""
    session = session or _session()
    try:
        resp = session.get(INSIDE_AIRBNB_INDEX_URL, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Nie udało się odczytać indeksu Inside Airbnb: %s", exc)
        return None
    pattern = re.compile(rf"{re.escape(INSIDE_AIRBNB_DATA_URL)}/{re.escape(city.path)}/(\d{{4}}-\d{{2}}-\d{{2}})/")
    dates = pattern.findall(resp.text)
    return max(dates) if dates else None


def _download_file(session: requests.Session, url: str, dest: Path, timeout: int) -> None:
    """Pobierz plik strumieniowo do ``dest`` (nadpisuje)."""
    try:
        with session.get(url, stream=True, timeout=timeout) as resp:
            if resp.status_code == 404:
                raise DataSourceError(f"Plik nie istnieje w serwisie (HTTP 404): {url}")
            if resp.status_code != 200:
                raise DataSourceError(f"Serwis zwrócił HTTP {resp.status_code} dla {url}")
            with open(dest, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    fh.write(chunk)
    except requests.exceptions.Timeout as exc:
        raise DataSourceError(f"Przekroczono limit czasu ({timeout} s) przy pobieraniu {url}") from exc
    except requests.exceptions.ConnectionError as exc:
        raise DataSourceError(f"Brak połączenia z serwerem przy pobieraniu {url}") from exc
    except requests.RequestException as exc:
        raise DataSourceError(f"Błąd pobierania {url}: {exc}") from exc


# --- Walidacja --------------------------------------------------------------------------------

def validate_listings_file(path: Path) -> int:
    """Sprawdź ``listings.csv.gz``; zwróć liczbę wierszy."""
    if not path.exists() or path.stat().st_size == 0:
        raise DataValidationError(f"Brak pliku lub plik pusty: {path.name}")
    try:
        header = pd.read_csv(path, nrows=0)
    except (OSError, EOFError, gzip.BadGzipFile, pd.errors.ParserError, UnicodeDecodeError, ValueError) as exc:
        raise DataValidationError(f"Nie można odczytać {path.name} jako CSV: {exc}") from exc
    missing = REQUIRED_LISTING_COLUMNS - set(header.columns)
    if missing:
        raise DataValidationError(f"W {path.name} brakuje wymaganych kolumn: {sorted(missing)}")
    try:
        rows = sum(len(chunk) for chunk in pd.read_csv(path, usecols=["id"], chunksize=50_000))
    except (OSError, EOFError, pd.errors.ParserError) as exc:
        raise DataValidationError(f"Plik {path.name} jest uszkodzony lub niekompletny: {exc}") from exc
    if rows == 0:
        raise DataValidationError(f"Plik {path.name} nie zawiera żadnych ofert.")
    return rows


def validate_geojson_file(path: Path) -> int:
    """Sprawdź ``neighbourhoods.geojson``; zwróć liczbę obiektów poligonowych."""
    if not path.exists() or path.stat().st_size == 0:
        raise DataValidationError(f"Brak pliku lub plik pusty: {path.name}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DataValidationError(f"{path.name} nie jest poprawnym JSON-em: {exc}") from exc
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        raise DataValidationError(f"{path.name} nie jest FeatureCollection GeoJSON.")
    features = data.get("features") or []
    polygons = [
        f for f in features
        if isinstance(f, dict)
        and (f.get("geometry") or {}).get("type") in {"Polygon", "MultiPolygon"}
        and "neighbourhood" in (f.get("properties") or {})
    ]
    if not polygons:
        raise DataValidationError(f"{path.name} nie zawiera poligonów z atrybutem 'neighbourhood'.")
    return len(polygons)


def validate_neighbourhoods_csv(path: Path) -> None:
    if not path.exists() or path.stat().st_size == 0:
        raise DataValidationError(f"Brak pliku lub plik pusty: {path.name}")
    try:
        header = pd.read_csv(path, nrows=0)
    except (pd.errors.ParserError, UnicodeDecodeError, pd.errors.EmptyDataError) as exc:
        raise DataValidationError(f"Nie można odczytać {path.name}: {exc}") from exc
    if "neighbourhood" not in header.columns:
        raise DataValidationError(f"W {path.name} brakuje kolumny 'neighbourhood'.")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# --- Składowanie lokalne ------------------------------------------------------------------------

def city_dir(settings: Settings, city_slug: str) -> Path:
    return settings.raw_dir / city_slug


def load_metadata(directory: Path) -> DatasetMetadata:
    """Wczytaj ``metadata.json`` z katalogu wydania."""
    meta_path = directory / METADATA_FILE
    try:
        raw = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataSourceError(f"Nie można wczytać metadanych {meta_path}: {exc}") from exc
    raw["directory"] = str(directory)
    return DatasetMetadata(**raw)


def list_local_releases(settings: Settings, city_slug: str) -> list[DatasetMetadata]:
    """Lista kompletnych, lokalnie dostępnych wydań dla miasta (od najnowszego)."""
    base = city_dir(settings, city_slug)
    if not base.is_dir():
        return []
    releases = []
    for sub in sorted(base.iterdir(), reverse=True):
        if (sub / METADATA_FILE).exists() and all((sub / name).exists() for name in REMOTE_FILES):
            try:
                releases.append(load_metadata(sub))
            except (DataSourceError, TypeError) as exc:
                logger.warning("Pomijam uszkodzone wydanie %s: %s", sub, exc)
    return releases


def local_cities(settings: Settings) -> list[str]:
    """Slugi miast, dla których istnieje lokalnie kompletne wydanie."""
    if not settings.raw_dir.is_dir():
        return []
    return sorted(p.name for p in settings.raw_dir.iterdir() if p.is_dir() and list_local_releases(settings, p.name))


def get_local_dataset(settings: Settings, city_slug: str | None = None) -> DatasetMetadata | None:
    """Najnowsze lokalne wydanie wybranego miasta lub ``None``."""
    releases = list_local_releases(settings, city_slug or settings.city)
    return releases[0] if releases else None


def download_dataset(
    settings: Settings,
    city_slug: str | None = None,
    *,
    release: str | None = None,
    force: bool = False,
    session: requests.Session | None = None,
) -> DatasetMetadata:
    """Pobierz (lub zaktualizuj) dane miasta i zapisz je lokalnie.

    * bez ``force`` — jeżeli wskazane wydanie już istnieje lokalnie, nic nie jest pobierane;
    * z ``force`` — wydanie jest pobierane ponownie (nadpisanie po poprawnej walidacji);
    * ``release=None`` — wydanie ustalane z indeksu Inside Airbnb, a przy jego niedostępności
      z ``CitySpec.fallback_release``.
    """
    city = get_city(city_slug or settings.city)
    session = session or _session()
    if release is None:
        release = discover_latest_release(city, session, settings.http_timeout_s)
        if release is None:
            release = city.fallback_release
            logger.warning("Używam zapasowej daty wydania %s dla %s", release, city.slug)

    target = city_dir(settings, city.slug) / release
    if not force and (target / METADATA_FILE).exists() and all((target / n).exists() for n in REMOTE_FILES):
        logger.info("Dane %s (%s) są już zapisane lokalnie: %s", city.slug, release, target)
        return load_metadata(target)

    staging = city_dir(settings, city.slug) / f".{release}.partial"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    base_url = release_base_url(city, release)
    try:
        files: dict[str, dict] = {}
        for local_name, remote_path in REMOTE_FILES.items():
            url = f"{base_url}/{remote_path}"
            logger.info("Pobieranie %s", url)
            dest = staging / local_name
            _download_file(session, url, dest, settings.http_timeout_s)
            files[local_name] = {"url": url, "bytes": dest.stat().st_size, "sha256": _sha256(dest)}

        rows = validate_listings_file(staging / "listings.csv.gz")
        features = validate_geojson_file(staging / "neighbourhoods.geojson")
        validate_neighbourhoods_csv(staging / "neighbourhoods.csv")

        meta = DatasetMetadata(
            city=city.slug,
            label=city.label,
            release=release,
            source_url=base_url + "/",
            index_url=INSIDE_AIRBNB_INDEX_URL,
            license=DATA_LICENSE,
            downloaded_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            files=files,
            listings_rows=rows,
            neighbourhood_features=features,
        )
        payload = asdict(meta)
        payload.pop("directory")
        (staging / METADATA_FILE).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        if target.exists():
            shutil.rmtree(target)
        staging.rename(target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    logger.info("Zapisano %s (%d ofert, %d dzielnic) w %s", city.slug, rows, features, target)
    return load_metadata(target)


def check_for_update(settings: Settings, city_slug: str | None = None) -> tuple[str | None, str | None]:
    """Zwróć (najnowsze wydanie w serwisie, najnowsze wydanie lokalne); wartości mogą być ``None``."""
    city = get_city(city_slug or settings.city)
    remote = discover_latest_release(city, timeout=settings.http_timeout_s)
    local = get_local_dataset(settings, city.slug)
    return remote, (local.release if local else None)
