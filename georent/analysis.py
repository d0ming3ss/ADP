"""Orkiestracja analizy: filtry użytkownika, pipeline end-to-end i miary wpływu lokalizacji na cenę."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import geopandas as gpd
import numpy as np
import pandas as pd

from georent import spatial
from georent.autocorr import InsufficientDataError, MoranResult, compute_moran
from georent.cleaning import CleaningParams, CleaningReport, clean_listings, load_raw_listings
from georent.config import CitySpec, Settings
from georent.data_source import DatasetMetadata

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Filters:
    """Filtry ofert (wszystkie opcjonalne; ``None`` / pusta krotka = bez ograniczenia)."""

    neighbourhood_groups: tuple[str, ...] = ()
    neighbourhoods: tuple[str, ...] = ()
    price_range: tuple[float, float] | None = None
    room_types: tuple[str, ...] = ()
    property_types: tuple[str, ...] = ()
    accommodates_range: tuple[int, int] | None = None
    only_available: bool = False  # tylko oferty z has_availability = True
    min_availability_365: int | None = None
    min_reviews: int = 0
    exclude_outliers: bool = False
    require_price: bool = False  # True = pomiń oferty bez ceny także w liczbach i zagęszczeniu


def apply_filters(points: gpd.GeoDataFrame, f: Filters) -> gpd.GeoDataFrame:
    """Zastosuj filtry do warstwy ofert (zwraca kopię)."""
    mask = pd.Series(True, index=points.index)
    if f.require_price:
        mask &= points["price"].notna()
    if f.neighbourhood_groups:
        mask &= points["neighbourhood_group_cleansed"].isin(f.neighbourhood_groups)
    if f.neighbourhoods:
        mask &= points["neighbourhood"].isin(f.neighbourhoods)
    if f.price_range is not None:
        lo, hi = f.price_range
        mask &= points["price"].between(lo, hi)
    if f.room_types:
        mask &= points["room_type"].isin(f.room_types)
    if f.property_types:
        mask &= points["property_type"].isin(f.property_types)
    if f.accommodates_range is not None:
        lo, hi = f.accommodates_range
        mask &= points["accommodates"].between(lo, hi)
    if f.only_available and "has_availability" in points:
        mask &= points["has_availability"].fillna(False).astype(bool)
    if f.min_availability_365 is not None and "availability_365" in points:
        mask &= points["availability_365"] >= f.min_availability_365
    if f.min_reviews and "number_of_reviews" in points:
        mask &= points["number_of_reviews"] >= f.min_reviews
    if f.exclude_outliers:
        mask &= ~points["price_outlier"].fillna(False)
    return points[mask].copy()


@dataclass
class Dataset:
    """Dane przygotowane do analizy (niezależne od filtrów)."""

    meta: DatasetMetadata
    city: CitySpec
    polygons: gpd.GeoDataFrame
    points: gpd.GeoDataFrame  # wszystkie oferty z poprawnymi współrzędnymi + dzielnica + odległość od centrum
    cleaning: CleaningReport
    listings_total: int
    join_agreement: float | None  # odsetek zgodności Spatial Join z etykietą źródłową


def prepare_dataset(meta: DatasetMetadata, city: CitySpec) -> Dataset:
    """Wczytaj, oczyść i połącz przestrzennie dane lokalnego wydania (cały pipeline przygotowania)."""
    polygons = spatial.load_neighbourhoods(meta)
    raw = load_raw_listings(meta)
    cleaned, report = clean_listings(raw, CleaningParams(bounds=tuple(polygons.total_bounds)))
    points = spatial.make_points(cleaned)
    points = spatial.assign_neighbourhoods(points, polygons)
    points = spatial.add_distance_to_center(points, polygons, city.center)
    agreement = float(points["label_matches_source"].mean()) if "label_matches_source" in points and len(points) else None
    return Dataset(meta, city, polygons, points, report, len(cleaned), agreement)


@dataclass
class AnalysisResult:
    """Wynik analizy dla wybranego zestawu filtrów."""

    filters: Filters
    points: gpd.GeoDataFrame
    districts: gpd.GeoDataFrame
    moran: MoranResult | None
    moran_error: str | None
    variable: str
    method: str


def run_analysis(
    dataset: Dataset,
    settings: Settings,
    filters: Filters | None = None,
    *,
    variable: str = "median_price",
    method: str = "queen",
) -> AnalysisResult:
    """Zastosuj filtry, zagreguj dzielnice i policz autokorelację (jeśli dane na to pozwalają)."""
    filters = filters or Filters()
    points = apply_filters(dataset.points, filters)
    districts = spatial.aggregate_neighbourhoods(points, dataset.polygons, settings.min_listings)
    moran, error = None, None
    try:
        moran = compute_moran(
            districts, variable, method=method, permutations=settings.permutations,
            seed=settings.seed, min_listings=settings.min_listings,
        )
        districts = moran.districts
    except InsufficientDataError as exc:
        error = str(exc)
        logger.warning("Pominięto autokorelację: %s", error)
    return AnalysisResult(filters, points, districts, moran, error, variable, method)


# --- Wpływ lokalizacji na cenę ---------------------------------------------------------------------------

def _adj_r2(y: np.ndarray, X: np.ndarray) -> float:
    n, k = X.shape
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    ss_res, ss_tot = float(resid @ resid), float(((y - y.mean()) ** 2).sum())
    if ss_tot == 0 or n - k <= 0:
        return float("nan")
    return 1 - (ss_res / (n - k)) / (ss_tot / (n - 1))


@dataclass
class LocationEffect:
    """Ile zmienności ln(ceny) tłumaczy lokalizacja, a ile cechy samej oferty."""

    n: int
    r2_offer: float  # liczba gości + typ pokoju
    r2_district: float  # sama dzielnica
    r2_offer_district: float  # cechy oferty + dzielnica
    r2_offer_distance: float  # cechy oferty + odległość od centrum
    spearman_distance: float  # korelacja rang: cena vs odległość od centrum


def location_effect(points: gpd.GeoDataFrame) -> LocationEffect | None:
    """Oszacuj wpływ lokalizacji na ln(ceny) (skorygowane R² modeli OLS; Spearman cena–odległość)."""
    df = points.dropna(subset=["price", "accommodates", "dist_center_km"])
    df = df[(df["price"] > 0) & (df["neighbourhood"] != spatial.OUTSIDE_LABEL)]
    if len(df) < 50 or df["neighbourhood"].nunique() < 2:
        return None
    y = np.log(df["price"].to_numpy(dtype=float))
    ones = np.ones((len(df), 1))
    offer = np.hstack([
        ones, df[["accommodates"]].to_numpy(dtype=float),
        pd.get_dummies(df["room_type"], drop_first=True).to_numpy(dtype=float),
    ])
    dist = df[["dist_center_km"]].to_numpy(dtype=float)
    dummies = pd.get_dummies(df["neighbourhood"], drop_first=True).to_numpy(dtype=float)
    rho = df["price"].corr(df["dist_center_km"], method="spearman")
    return LocationEffect(
        n=len(df),
        r2_offer=_adj_r2(y, offer),
        r2_district=_adj_r2(y, np.hstack([ones, dummies])),
        r2_offer_district=_adj_r2(y, np.hstack([offer, dummies])),
        r2_offer_distance=_adj_r2(y, np.hstack([offer, dist])),
        spearman_distance=float(rho),
    )


@dataclass
class OutlierImpact:
    """Porównanie wyników z oraz bez ofert odstających."""

    table: pd.DataFrame
    moran_with: MoranResult | None
    moran_without: MoranResult | None
    notes: list[str] = field(default_factory=list)


def outlier_impact(dataset: Dataset, settings: Settings, filters: Filters, variable: str = "median_price",
                   method: str = "queen") -> OutlierImpact:
    """Oceń wpływ filtracji ofert odstających na statystyki i Moran's I (przy pozostałych filtrach bez zmian)."""
    from dataclasses import replace

    results = {}
    for label, exclude in (("z odstającymi", False), ("bez odstających", True)):
        results[label] = run_analysis(dataset, settings, replace(filters, exclude_outliers=exclude),
                                      variable=variable, method=method)
    rows = []
    for label, res in results.items():
        price = res.points["price"]
        rows.append({
            "Wariant": label,
            "Liczba ofert": len(price),
            "Średnia cena": price.mean(),
            "Mediana ceny": price.median(),
            "Odch. std. ceny": price.std(),
            "Maks. cena": price.max(),
            "Moran's I": res.moran.I if res.moran else np.nan,
            "pseudo-p": res.moran.p_sim if res.moran else np.nan,
        })
    return OutlierImpact(pd.DataFrame(rows), results["z odstającymi"].moran, results["bez odstających"].moran)
