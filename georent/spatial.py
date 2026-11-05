"""Operacje GIS: warstwa punktowa ofert, poligony dzielnic, Spatial Join i agregacje."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import make_valid
from shapely.geometry import MultiPolygon, Polygon

from georent.data_source import DatasetMetadata

logger = logging.getLogger(__name__)

WGS84 = "EPSG:4326"
OUTSIDE_LABEL = "(poza granicami dzielnic)"


@dataclass(frozen=True)
class Metric:
    """Opis miary wyświetlanej na kartogramach."""

    key: str
    label: str
    unit: str
    description: str
    diverging: bool = False


METRICS: dict[str, Metric] = {m.key: m for m in (
    Metric("n_listings", "Liczba ofert", "oferty", "Bezwzględna liczba ofert w dzielnicy (zależy od jej powierzchni)."),
    Metric("density_km2", "Zagęszczenie ofert", "oferty/km²", "Liczba ofert na km² — porównywalna między dzielnicami."),
    Metric("mean_price", "Średnia cena za noc", "$", "Średnia arytmetyczna cen; wrażliwa na oferty skrajne."),
    Metric("median_price", "Mediana ceny za noc", "$", "Mediana cen; odporna na oferty skrajne."),
    Metric("median_price_pp", "Mediana ceny za osobę", "$/osobę", "Mediana ceny podzielonej przez liczbę gości (accommodates)."),
    Metric("share_entire_home", "Udział całych mieszkań", "%", "Odsetek ofert typu „Entire home/apt”."),
    Metric("mean_rating", "Średnia ocena", "pkt (1–5)", "Średnia z review_scores_rating ofert posiadających ocenę."),
    Metric("mean_occupancy", "Szac. obłożenie roczne", "noce/rok", "Średnia z estimated_occupancy_l365d (szacunek Inside Airbnb)."),
)}


# --- Wczytywanie warstw ---------------------------------------------------------------------------

def project_crs(gdf: gpd.GeoDataFrame):
    """Układ metryczny (UTM) odpowiedni dla danego obszaru — do pól powierzchni i odległości."""
    return gdf.estimate_utm_crs()


def _repair_geometry(geom):
    """Napraw niepoprawną geometrię i pozostaw tylko komponenty poligonowe."""
    if geom is None or geom.is_empty:
        return geom
    if not geom.is_valid:
        geom = make_valid(geom)
    if isinstance(geom, (Polygon, MultiPolygon)):
        return geom
    parts = [g for g in getattr(geom, "geoms", []) if isinstance(g, (Polygon, MultiPolygon))]
    if not parts:
        return None
    flat: list[Polygon] = []
    for part in parts:
        flat.extend(part.geoms if isinstance(part, MultiPolygon) else [part])
    return MultiPolygon(flat) if len(flat) > 1 else flat[0]


def load_neighbourhoods(meta: DatasetMetadata) -> gpd.GeoDataFrame:
    """Wczytaj granice dzielnic z GeoJSON (EPSG:4326), napraw geometrie, dodaj powierzchnię w km²."""
    gdf = gpd.read_file(meta.path("neighbourhoods.geojson"))
    return prepare_neighbourhoods(gdf)


def prepare_neighbourhoods(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Ujednolić CRS, naprawić geometrie i obliczyć powierzchnię (km²) w układzie metrycznym."""
    if "neighbourhood" not in gdf.columns:
        raise ValueError("Warstwa dzielnic musi zawierać kolumnę 'neighbourhood'.")
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)  # GeoJSON (RFC 7946) domyślnie używa WGS84
    gdf = gdf.to_crs(WGS84).copy()
    n_invalid = int((~gdf.geometry.is_valid).sum())
    gdf["geometry"] = gdf.geometry.map(_repair_geometry)
    gdf = gdf[gdf.geometry.map(lambda g: g is not None and not g.is_empty)].copy()
    if n_invalid:
        logger.info("Naprawiono %d niepoprawnych geometrii dzielnic", n_invalid)
    if "neighbourhood_group" not in gdf.columns:
        gdf["neighbourhood_group"] = None
    n_before = len(gdf)
    # Jedna dzielnica może być opisana kilkoma obiektami (np. Vallvidrera w Barcelonie ma 3 poligony);
    # scalamy je (dissolve), aby każda dzielnica była jedną jednostką analizy.
    gdf = gdf.dissolve(by="neighbourhood", aggfunc={"neighbourhood_group": "first"}, as_index=False, sort=False)
    if len(gdf) != n_before:
        logger.info("Scalono %d poligonów o powtórzonej nazwie dzielnicy", n_before - len(gdf))
    gdf["area_km2"] = gdf.to_crs(project_crs(gdf)).area / 1e6
    return gdf[["neighbourhood", "neighbourhood_group", "area_km2", "geometry"]].reset_index(drop=True)


def make_points(listings: pd.DataFrame) -> gpd.GeoDataFrame:
    """Zbuduj warstwę punktową (EPSG:4326) z ofert o poprawnych współrzędnych (``coord_valid``)."""
    valid = listings[listings["coord_valid"]] if "coord_valid" in listings.columns else listings
    valid = valid.dropna(subset=["latitude", "longitude"])
    return gpd.GeoDataFrame(
        valid.copy(),
        geometry=gpd.points_from_xy(valid["longitude"], valid["latitude"]),
        crs=WGS84,
    ).reset_index(drop=True)


# --- Spatial Join -----------------------------------------------------------------------------------

def assign_neighbourhoods(points: gpd.GeoDataFrame, polygons: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Przypisz każdą ofertę do dzielnicy złączeniem przestrzennym (punkt ``within`` poligon).

    Dodaje kolumny: ``neighbourhood`` (wynik Spatial Join; ``OUTSIDE_LABEL`` poza poligonami),
    ``outside_boundaries`` oraz ``label_matches_source`` (zgodność z etykietą
    ``neighbourhood_cleansed`` z pliku źródłowego — kontrola jakości złączenia).
    Oferty leżące dokładnie na granicy dwóch dzielnic trafiają do pierwszej (bez duplikatów).
    """
    if points.crs is None or polygons.crs is None:
        raise ValueError("Obie warstwy muszą mieć zdefiniowany układ współrzędnych.")
    pts = points.copy()
    polys = polygons[["neighbourhood", "geometry"]].to_crs(points.crs)
    if pts.empty:
        pts["neighbourhood"] = pd.Series(dtype="object")
        pts["outside_boundaries"] = pd.Series(dtype="bool")
        return pts
    joined = gpd.sjoin(pts[["geometry"]], polys, how="left", predicate="within")
    joined = joined[~joined.index.duplicated(keep="first")]
    pts["neighbourhood"] = joined["neighbourhood"].reindex(pts.index)
    pts["outside_boundaries"] = pts["neighbourhood"].isna()
    pts["neighbourhood"] = pts["neighbourhood"].fillna(OUTSIDE_LABEL)
    if "neighbourhood_cleansed" in pts.columns:
        pts["label_matches_source"] = pts["neighbourhood"] == pts["neighbourhood_cleansed"]
    return pts


# --- Agregacje -----------------------------------------------------------------------------------------

def aggregate_neighbourhoods(
    points: gpd.GeoDataFrame, polygons: gpd.GeoDataFrame, min_listings: int = 10
) -> gpd.GeoDataFrame:
    """Statystyki dzielnic: liczby, rozkład cen, zagęszczenie i miary dodatkowe.

    Wynik zawiera **wszystkie** dzielnice z warstwy poligonowej — także te bez ofert
    (``n_listings = 0``, statystyki cenowe ``NaN``). ``price_reliable`` jest prawdziwe, gdy
    dzielnica ma co najmniej ``min_listings`` ofert z ceną.
    """
    inside = points[~points["outside_boundaries"]] if "outside_boundaries" in points.columns else points
    priced = inside[inside["price"].notna()]

    counts = inside.groupby("neighbourhood").size().rename("n_listings")
    price_stats = priced.groupby("neighbourhood")["price"].agg(
        n_priced="count", mean_price="mean", median_price="median",
        min_price="min", max_price="max", std_price="std",
        p25_price=lambda s: s.quantile(0.25), p75_price=lambda s: s.quantile(0.75),
    )
    extra = pd.concat(
        [
            priced.groupby("neighbourhood")["price_per_person"].median().rename("median_price_pp"),
            inside.groupby("neighbourhood")["room_type"].apply(lambda s: (s == "Entire home/apt").mean() * 100)
            .rename("share_entire_home"),
            inside.groupby("neighbourhood")["review_scores_rating"].mean().rename("mean_rating")
            if "review_scores_rating" in inside.columns else None,
            inside.groupby("neighbourhood")["estimated_occupancy_l365d"].mean().rename("mean_occupancy")
            if "estimated_occupancy_l365d" in inside.columns else None,
        ],
        axis=1,
    )

    out = polygons.copy().set_index("neighbourhood")
    out = out.join(counts).join(price_stats).join(extra)
    out["n_listings"] = out["n_listings"].fillna(0).astype(int)
    out["n_priced"] = out["n_priced"].fillna(0).astype(int)
    out["density_km2"] = out["n_listings"] / out["area_km2"].where(out["area_km2"] > 0)
    out["price_reliable"] = out["n_priced"] >= min_listings
    return gpd.GeoDataFrame(out.reset_index(), geometry="geometry", crs=polygons.crs)


# --- Odległość od centrum -------------------------------------------------------------------------------------

def add_distance_to_center(
    points: gpd.GeoDataFrame, polygons: gpd.GeoDataFrame, center: tuple[float, float] | None = None
) -> gpd.GeoDataFrame:
    """Dodaj ``dist_center_km`` — odległość euklidesową (w UTM) od centrum miasta.

    ``center`` to (lat, lon); gdy brak, używany jest środek ciężkości sumy dzielnic.
    """
    pts = points.copy()
    crs = project_crs(polygons)
    if center is not None:
        center_pt = gpd.GeoSeries(gpd.points_from_xy([center[1]], [center[0]]), crs=WGS84).to_crs(crs).iloc[0]
    else:
        center_pt = polygons.to_crs(crs).union_all().centroid
    pts["dist_center_km"] = pts.to_crs(crs).distance(center_pt) / 1000.0
    return pts
