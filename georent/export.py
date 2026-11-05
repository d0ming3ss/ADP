"""Eksport warstw wynikowych do formatów GIS (GeoPackage, GeoJSON, CSV) dla QGIS."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd

from georent.analysis import AnalysisResult, Dataset
from georent.autocorr import CLUSTER_LABELS
from georent.spatial import WGS84

logger = logging.getLogger(__name__)

GPKG_NAME = "georent.gpkg"

# Nazwy kolumn <= 10 znaków nie są wymagane (GeoPackage), ale pola mają być czytelne.
LISTING_FIELDS = [
    "id", "name", "listing_url", "neighbourhood", "neighbourhood_group_cleansed", "room_type", "property_type",
    "accommodates", "bedrooms", "price", "price_per_person", "price_outlier", "availability_365",
    "number_of_reviews", "review_scores_rating", "estimated_occupancy_l365d", "dist_center_km",
    "latitude", "longitude",
]
DISTRICT_FIELDS = [
    "neighbourhood", "neighbourhood_group", "area_km2", "n_listings", "n_priced", "price_reliable",
    "mean_price", "median_price", "min_price", "max_price", "std_price", "p25_price", "p75_price",
    "density_km2", "median_price_pp", "share_entire_home", "mean_rating", "mean_occupancy",
    "z_value", "z_lag", "lisa_I", "lisa_p", "lisa_z", "lisa_code", "lisa_cluster", "lisa_sig_fdr",
]


@dataclass
class ExportResult:
    directory: Path
    files: list[Path]
    layers: dict[str, int]


def _bool_to_int(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Kolumny logiczne zapisujemy jako 0/1 — najbezpieczniejszy typ dla QGIS i GeoJSON."""
    gdf = gdf.copy()
    for col in gdf.columns:
        if str(gdf[col].dtype) in ("bool", "boolean"):
            gdf[col] = gdf[col].astype("float").astype("Int64")
    return gdf


def build_layers(result: AnalysisResult, dataset: Dataset) -> dict[str, gpd.GeoDataFrame]:
    """Zbuduj warstwy wynikowe (wszystkie w EPSG:4326)."""
    points = result.points
    listings = _bool_to_int(points[[c for c in LISTING_FIELDS if c in points.columns] + ["geometry"]])
    districts = result.districts
    stats = _bool_to_int(districts[[c for c in DISTRICT_FIELDS if c in districts.columns] + ["geometry"]])
    layers: dict[str, gpd.GeoDataFrame] = {
        "oferty_punkty": listings,
        "dzielnice_granice": dataset.polygons[["neighbourhood", "neighbourhood_group", "area_km2", "geometry"]],
        "dzielnice_statystyki": stats,
    }
    if "lisa_code" in districts.columns:
        clusters = stats[stats["lisa_code"].isin(["HH", "LL", "HL", "LH"])]
        layers["lisa_klastry"] = clusters
    return layers


def moran_table(result: AnalysisResult) -> pd.DataFrame | None:
    if result.moran is None:
        return None
    m = result.moran
    return pd.DataFrame([{
        "zmienna": m.variable, "wagi": m.method, "n_jednostek": m.n_obs, "moran_I": m.I, "E_I": m.expected_I,
        "z_sim": m.z_sim, "p_sim_dwustronne": m.p_sim, "p_sim_jednostronne": m.p_sim_one_sided, "p_norm": m.p_norm, "permutacje": m.permutations, "seed": m.seed,
        "alpha": m.alpha, "interpretacja": m.interpretation,
    }])


def export_all(result: AnalysisResult, dataset: Dataset, out_dir: Path) -> ExportResult:
    """Zapisz GeoPackage (wszystkie warstwy), GeoJSON warstw, CSV z koordynatami i opis eksportu.

    Układ współrzędnych wszystkich warstw: EPSG:4326 (WGS 84, długość/szerokość w stopniach).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    layers = build_layers(result, dataset)
    files: list[Path] = []

    gpkg = out_dir / GPKG_NAME
    if gpkg.exists():
        gpkg.unlink()
    for name, gdf in layers.items():
        gdf.to_crs(WGS84).to_file(gpkg, layer=name, driver="GPKG", engine="pyogrio")
    files.append(gpkg)

    geojson_dir = out_dir / "geojson"
    geojson_dir.mkdir(exist_ok=True)
    for name, gdf in layers.items():
        path = geojson_dir / f"{name}.geojson"
        gdf.to_crs(WGS84).to_file(path, driver="GeoJSON", engine="pyogrio")
        files.append(path)

    csv_path = out_dir / "oferty_wspolrzedne.csv"
    pd.DataFrame(layers["oferty_punkty"].drop(columns="geometry")).to_csv(csv_path, index=False, encoding="utf-8")
    files.append(csv_path)
    stats_csv = out_dir / "dzielnice_statystyki.csv"
    pd.DataFrame(layers["dzielnice_statystyki"].drop(columns="geometry")).to_csv(stats_csv, index=False, encoding="utf-8")
    files.append(stats_csv)

    mt = moran_table(result)
    if mt is not None:
        moran_csv = out_dir / "moran_globalny.csv"
        mt.to_csv(moran_csv, index=False, encoding="utf-8")
        files.append(moran_csv)

    meta = {
        "miasto": dataset.city.label,
        "wydanie_danych": dataset.meta.release,
        "zrodlo": dataset.meta.source_url,
        "licencja": dataset.meta.license,
        "pobrano": dataset.meta.downloaded_at,
        "wyeksportowano": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "uklad_wspolrzednych": WGS84,
        "filtry": {k: (list(v) if isinstance(v, tuple) else v) for k, v in result.filters.__dict__.items()},
        "warstwy": {k: len(v) for k, v in layers.items()},
        "klasy_lisa": CLUSTER_LABELS,
    }
    meta_path = out_dir / "eksport_opis.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    files.append(meta_path)
    logger.info("Wyeksportowano %d plików do %s", len(files), out_dir)
    return ExportResult(out_dir, files, meta["warstwy"])
