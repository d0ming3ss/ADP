import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point, Polygon, box

from georent import spatial
from georent.cleaning import clean_listings
from tests.conftest import LAT0, LON0, STEP, make_grid, make_listings


@pytest.fixture
def points(listings):
    cleaned, _ = clean_listings(listings)
    return spatial.make_points(cleaned)


def test_make_points_crs_and_only_valid(listings):
    listings = listings.copy()
    listings.loc[0, "latitude"] = 200.0
    cleaned, _ = clean_listings(listings)
    pts = spatial.make_points(cleaned)
    assert pts.crs.to_epsg() == 4326
    assert len(pts) == len(listings) - 1
    assert (pts.geometry.x == pts["longitude"]).all() and (pts.geometry.y == pts["latitude"]).all()


def test_make_points_empty(listings):
    cleaned, _ = clean_listings(listings.iloc[0:0])
    pts = spatial.make_points(cleaned)
    assert pts.empty and pts.crs.to_epsg() == 4326


def test_spatial_join_assigns_correct_district(points, grid):
    joined = spatial.assign_neighbourhoods(points, grid)
    assert not joined["outside_boundaries"].any()
    assert joined["label_matches_source"].all()  # etykieta źródłowa zgodna ze złączeniem
    assert len(joined) == len(points)


def test_spatial_join_outside_and_boundary(grid):
    pts = gpd.GeoDataFrame(
        {"id": [1, 2, 3]},
        geometry=[Point(LON0 + 0.5 * STEP, LAT0 + 0.5 * STEP),   # wewnątrz D00
                  Point(LON0 - 1.0, LAT0),                      # poza wszystkimi
                  Point(LON0 + STEP, LAT0 + 0.5 * STEP)],       # dokładnie na granicy D00/D01
        crs="EPSG:4326")
    out = spatial.assign_neighbourhoods(pts, grid)
    assert out.loc[0, "neighbourhood"] == "D00"
    assert out.loc[1, "neighbourhood"] == spatial.OUTSIDE_LABEL and out.loc[1, "outside_boundaries"]
    assert len(out) == 3  # punkt na granicy nie jest zduplikowany
    # predykat "within" nie obejmuje punktów leżących dokładnie na granicy -> traktowane jako poza granicami
    assert out.loc[2, "neighbourhood"] == spatial.OUTSIDE_LABEL


def test_spatial_join_requires_crs(points, grid):
    with pytest.raises(ValueError, match="układ współrzędnych"):
        spatial.assign_neighbourhoods(points.set_crs(None, allow_override=True), grid)


def test_spatial_join_reprojects_polygons(points, grid):
    joined = spatial.assign_neighbourhoods(points, grid.to_crs(25831))
    assert joined["label_matches_source"].all()


def test_spatial_join_empty_points(grid, listings):
    cleaned, _ = clean_listings(listings.iloc[0:0])
    out = spatial.assign_neighbourhoods(spatial.make_points(cleaned), grid)
    assert out.empty and "neighbourhood" in out


def test_aggregate_known_values(grid):
    listings = make_listings(grid, per_district=4)
    cleaned, _ = clean_listings(listings)
    pts = spatial.assign_neighbourhoods(spatial.make_points(cleaned), grid)
    # ustaw dokładne ceny w D00
    idx = pts.index[pts["neighbourhood"] == "D00"]
    pts.loc[idx, "price"] = [10.0, 20.0, 30.0, 40.0]
    agg = spatial.aggregate_neighbourhoods(pts, grid, min_listings=4).set_index("neighbourhood")
    d = agg.loc["D00"]
    assert d["n_listings"] == 4 and d["n_priced"] == 4
    assert d["mean_price"] == pytest.approx(25.0) and d["median_price"] == pytest.approx(25.0)
    assert d["min_price"] == 10 and d["max_price"] == 40
    assert d["std_price"] == pytest.approx(np.std([10, 20, 30, 40], ddof=1))
    assert d["density_km2"] == pytest.approx(4 / d["area_km2"])
    assert 0.8 < d["area_km2"] < 1.1  # 0,01° × 0,01° na szer. 41,35° ≈ 0,93 km²
    assert bool(d["price_reliable"])


def test_aggregate_includes_districts_without_listings(grid, listings):
    cleaned, _ = clean_listings(listings)
    pts = spatial.assign_neighbourhoods(spatial.make_points(cleaned), grid)
    pts = pts[pts["neighbourhood"] != "D33"]
    agg = spatial.aggregate_neighbourhoods(pts, grid, min_listings=5).set_index("neighbourhood")
    assert len(agg) == len(grid)
    assert agg.loc["D33", "n_listings"] == 0 and agg.loc["D33", "density_km2"] == 0
    assert np.isnan(agg.loc["D33", "median_price"]) and not agg.loc["D33", "price_reliable"]


def test_aggregate_unpriced_listings_counted_but_not_in_price_stats(grid, listings):
    listings = listings.copy()
    listings.loc[listings["neighbourhood_cleansed"] == "D01", "price"] = None
    cleaned, _ = clean_listings(listings)
    pts = spatial.assign_neighbourhoods(spatial.make_points(cleaned), grid)
    agg = spatial.aggregate_neighbourhoods(pts, grid, min_listings=5).set_index("neighbourhood")
    assert agg.loc["D01", "n_listings"] == 12 and agg.loc["D01", "n_priced"] == 0
    assert np.isnan(agg.loc["D01", "mean_price"]) and not agg.loc["D01", "price_reliable"]


def test_aggregate_ignores_points_outside_boundaries(grid, listings):
    cleaned, _ = clean_listings(listings)
    pts = spatial.assign_neighbourhoods(spatial.make_points(cleaned), grid)
    pts.loc[pts.index[:3], ["neighbourhood", "outside_boundaries"]] = [spatial.OUTSIDE_LABEL, True]
    agg = spatial.aggregate_neighbourhoods(pts, grid, min_listings=5)
    assert agg["n_listings"].sum() == len(pts) - 3


def test_aggregate_empty_points(grid, listings):
    cleaned, _ = clean_listings(listings.iloc[0:0])
    pts = spatial.assign_neighbourhoods(spatial.make_points(cleaned), grid)
    agg = spatial.aggregate_neighbourhoods(pts, grid)
    assert len(agg) == len(grid) and (agg["n_listings"] == 0).all()


def test_prepare_neighbourhoods_repairs_invalid_geometry():
    bowtie = Polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])  # samoprzecinający się „motylek”
    assert not bowtie.is_valid
    gdf = gpd.GeoDataFrame({"neighbourhood": ["X", "Y"]}, geometry=[bowtie, box(2, 0, 3, 1)], crs="EPSG:4326")
    out = spatial.prepare_neighbourhoods(gdf)
    assert out.geometry.is_valid.all() and len(out) == 2
    assert (out["area_km2"] > 0).all()


def test_prepare_neighbourhoods_assumes_wgs84_without_crs_and_requires_name():
    gdf = gpd.GeoDataFrame({"neighbourhood": ["X"]}, geometry=[box(2, 41, 2.01, 41.01)])
    assert spatial.prepare_neighbourhoods(gdf).crs.to_epsg() == 4326
    with pytest.raises(ValueError, match="neighbourhood"):
        spatial.prepare_neighbourhoods(gpd.GeoDataFrame({"nazwa": ["X"]}, geometry=[box(0, 0, 1, 1)], crs=4326))


def test_prepare_neighbourhoods_drops_empty_geometries():
    gdf = gpd.GeoDataFrame({"neighbourhood": ["X", "Y"]}, geometry=[box(2, 41, 2.01, 41.01), Polygon()], crs="EPSG:4326")
    assert list(spatial.prepare_neighbourhoods(gdf)["neighbourhood"]) == ["X"]


def test_distance_to_center(points, grid):
    out = spatial.add_distance_to_center(points, grid, center=(LAT0 + 2 * STEP, LON0 + 2 * STEP))
    assert (out["dist_center_km"] >= 0).all() and out["dist_center_km"].max() < 5
    out2 = spatial.add_distance_to_center(points, grid, center=None)
    assert out2["dist_center_km"].notna().all()


def test_prepare_neighbourhoods_dissolves_duplicate_names():
    parts = [box(2.00, 41.0, 2.01, 41.01), box(2.01, 41.0, 2.02, 41.01), box(2.10, 41.1, 2.11, 41.11)]
    gdf = gpd.GeoDataFrame({"neighbourhood": ["A", "A", "B"], "neighbourhood_group": ["G", "G", "H"]}, geometry=parts, crs=4326)
    out = spatial.prepare_neighbourhoods(gdf).set_index("neighbourhood")
    assert list(out.index) == ["A", "B"]
    single = spatial.prepare_neighbourhoods(gdf.iloc[[0]]).iloc[0]["area_km2"]
    assert out.loc["A", "area_km2"] == pytest.approx(2 * single, rel=1e-3)  # powierzchnie części sumują się


def test_aggregate_counts_each_listing_once_with_split_district():
    parts = [box(2.00, 41.0, 2.01, 41.01), box(2.20, 41.0, 2.21, 41.01)]
    gdf = gpd.GeoDataFrame({"neighbourhood": ["A", "A"], "neighbourhood_group": ["G", "G"]}, geometry=parts, crs=4326)
    polys = spatial.prepare_neighbourhoods(gdf)
    pts = gpd.GeoDataFrame({"price": [10.0, 20.0], "price_per_person": [5.0, 10.0], "room_type": ["Private room"] * 2},
                           geometry=[Point(2.005, 41.005), Point(2.205, 41.005)], crs=4326)
    agg = spatial.aggregate_neighbourhoods(spatial.assign_neighbourhoods(pts, polys), polys, min_listings=1)
    assert len(agg) == 1 and agg.loc[0, "n_listings"] == 2 and agg.loc[0, "median_price"] == 15.0
