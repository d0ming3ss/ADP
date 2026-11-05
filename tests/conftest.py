"""Wspólne fabryki danych testowych (małe, syntetyczne siatki — wyłącznie do testów jednostkowych)."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box

from georent.config import Settings
from georent.spatial import prepare_neighbourhoods

LON0, LAT0, STEP = 2.10, 41.35, 0.01  # okolice Barcelony; komórka ≈ 0,93 km²


def make_grid(n: int = 4) -> gpd.GeoDataFrame:
    """Siatka n×n kwadratowych dzielnic o nazwach ``D<row><col>``."""
    rows = []
    for r in range(n):
        for c in range(n):
            rows.append({
                "neighbourhood": f"D{r}{c}",
                "neighbourhood_group": "Północ" if r < n // 2 else "Południe",
                "geometry": box(LON0 + c * STEP, LAT0 + r * STEP, LON0 + (c + 1) * STEP, LAT0 + (r + 1) * STEP),
            })
    return prepare_neighbourhoods(gpd.GeoDataFrame(rows, crs="EPSG:4326"))


def make_listings(grid: gpd.GeoDataFrame, per_district: int = 12, price_fn=None, seed: int = 1) -> pd.DataFrame:
    """Oferty rozmieszczone w każdej dzielnicy; ``price_fn(row, col)`` zwraca bazową cenę."""
    rng = np.random.default_rng(seed)
    price_fn = price_fn or (lambda r, c: 100.0)
    rows, i = [], 1
    n = int(round(len(grid) ** 0.5))
    for k, g in enumerate(grid.itertuples()):
        r, c = divmod(k, n)
        minx, miny, maxx, maxy = g.geometry.bounds
        for _ in range(per_district):
            base = price_fn(r, c)
            rows.append({
                "id": i, "name": f"Oferta {i}", "listing_url": f"https://example.org/{i}",
                "neighbourhood_cleansed": g.neighbourhood, "neighbourhood_group_cleansed": g.neighbourhood_group,
                "latitude": rng.uniform(miny + 0.001, maxy - 0.001), "longitude": rng.uniform(minx + 0.001, maxx - 0.001),
                "property_type": "Entire rental unit" if i % 5 else "Room in hotel", "room_type": "Entire home/apt" if i % 3 else "Private room",
                "accommodates": int(rng.integers(1, 7)), "bedrooms": 1.0, "beds": 1.0,
                "price": f"${base * rng.uniform(0.9, 1.1):,.2f}", "minimum_nights": 1, "has_availability": "t",
                "availability_365": 200, "number_of_reviews": 5, "review_scores_rating": 4.5,
                "estimated_occupancy_l365d": 80, "estimated_revenue_l365d": 8000, "instant_bookable": "f",
                "host_id": i, "host_is_superhost": "f",
            })
            i += 1
    return pd.DataFrame(rows)


@pytest.fixture
def grid() -> gpd.GeoDataFrame:
    return make_grid(4)


@pytest.fixture
def listings(grid) -> pd.DataFrame:
    # gradient zachód–wschód: dodatnia autokorelacja przestrzenna cen
    return make_listings(grid, price_fn=lambda r, c: 60 + 60 * c)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(city="barcelona", data_dir=tmp_path / "data", export_dir=tmp_path / "exports",
                    http_timeout_s=5, permutations=199, seed=7, min_listings=5)


def write_listings_gz(path: Path, df: pd.DataFrame) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        df.to_csv(fh, index=False)


def write_geojson(path: Path, grid: gpd.GeoDataFrame) -> None:
    path.write_text(grid.to_json(), encoding="utf-8")
