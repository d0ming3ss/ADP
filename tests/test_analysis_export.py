"""Testy filtrów, pipeline'u analizy, eksportu warstw GIS i modułu CLI."""

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import pytest

from georent import analysis, cli, data_source as dsrc, export
from georent.analysis import Filters, apply_filters, prepare_dataset, run_analysis
from georent.config import get_city
from tests.conftest import make_grid, make_listings, write_geojson, write_listings_gz


@pytest.fixture
def dataset(settings, grid, listings):
    """Kompletne lokalne wydanie danych zapisane w katalogu tymczasowym."""
    d = settings.raw_dir / "barcelona" / "2026-09-17"
    d.mkdir(parents=True)
    write_listings_gz(d / "listings.csv.gz", listings)
    write_geojson(d / "neighbourhoods.geojson", grid)
    (d / "neighbourhoods.csv").write_text("neighbourhood_group,neighbourhood\nA,D00\n", encoding="utf-8")
    meta = {"city": "barcelona", "label": "Barcelona", "release": "2026-09-17", "source_url": "https://x/", "index_url": "https://i/",
            "license": "CC BY 4.0", "downloaded_at": "2026-11-01T10:00:00+00:00", "files": {}, "listings_rows": len(listings),
            "neighbourhood_features": 16}
    (d / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    return prepare_dataset(dsrc.get_local_dataset(settings), get_city("barcelona"))


def test_prepare_dataset(dataset, listings):
    assert len(dataset.points) == len(listings) and dataset.join_agreement == 1.0
    assert {"neighbourhood", "dist_center_km", "price_outlier"} <= set(dataset.points.columns)
    assert dataset.polygons.crs.to_epsg() == 4326


@pytest.mark.parametrize("flt, check", [
    (Filters(neighbourhoods=("D00", "D01")), lambda d: set(d["neighbourhood"]) == {"D00", "D01"}),
    (Filters(neighbourhood_groups=("Północ",)), lambda d: set(d["neighbourhood_group_cleansed"]) == {"Północ"}),
    (Filters(price_range=(100, 150)), lambda d: d["price"].between(100, 150).all()),
    (Filters(room_types=("Private room",)), lambda d: (d["room_type"] == "Private room").all()),
    (Filters(property_types=("Entire rental unit",)), lambda d: (d["property_type"] == "Entire rental unit").all()),
    (Filters(accommodates_range=(2, 3)), lambda d: d["accommodates"].between(2, 3).all()),
    (Filters(min_reviews=5), lambda d: (d["number_of_reviews"] >= 5).all()),
    (Filters(min_availability_365=100), lambda d: (d["availability_365"] >= 100).all()),
])
def test_filters_restrict_rows(dataset, flt, check):
    out = apply_filters(dataset.points, flt)
    assert 0 < len(out) < len(dataset.points) or flt.min_reviews or flt.min_availability_365
    assert check(out)


def test_filters_availability_outliers_and_price_requirement(dataset):
    pts = dataset.points.copy()
    pts.loc[pts.index[:10], "has_availability"] = False
    pts.loc[pts.index[10:20], "price"] = np.nan
    pts.loc[pts.index[20:25], "price_outlier"] = True
    assert len(apply_filters(pts, Filters(only_available=True))) == len(pts) - 10
    assert len(apply_filters(pts, Filters(require_price=True))) == len(pts) - 10
    assert len(apply_filters(pts, Filters(exclude_outliers=True))) == len(pts) - 5
    assert len(apply_filters(pts, Filters())) == len(pts)


def test_filters_empty_result(dataset):
    out = apply_filters(dataset.points, Filters(price_range=(1e6, 2e6)))
    assert out.empty
    res = run_analysis(dataset, dataset_settings(dataset), Filters(price_range=(1e6, 2e6)))
    assert res.points.empty and res.moran is None and res.moran_error


def dataset_settings(dataset):
    from georent.config import Settings
    return Settings(city="barcelona", permutations=99, seed=1, min_listings=5)


def test_run_analysis_changes_with_filters(dataset):
    s = dataset_settings(dataset)
    full = run_analysis(dataset, s)
    sub = run_analysis(dataset, s, Filters(room_types=("Private room",)))
    assert full.moran and sub.points.shape[0] < full.points.shape[0]
    assert full.districts["n_listings"].sum() == len(dataset.points)
    assert sub.districts["n_listings"].sum() == len(sub.points)
    assert full.moran.I > 0 and full.moran.significant


def test_run_analysis_too_few_districts_gives_message(dataset):
    res = run_analysis(dataset, dataset_settings(dataset), Filters(neighbourhoods=("D00", "D01", "D02")))
    assert res.moran is None and "wymagane co najmniej" in res.moran_error
    assert len(res.districts) == 16  # agregacja nadal obejmuje wszystkie dzielnice


def test_location_effect(dataset):
    eff = analysis.location_effect(dataset.points)
    assert eff is not None and eff.n == len(dataset.points)
    assert eff.r2_offer_district >= eff.r2_offer - 1e-9
    assert eff.r2_district > 0.5  # w danych testowych cena zależy od kolumny dzielnic
    assert analysis.location_effect(dataset.points.head(10)) is None


def test_outlier_impact(dataset):
    imp = analysis.outlier_impact(dataset, dataset_settings(dataset), Filters())
    assert list(imp.table["Wariant"]) == ["z odstającymi", "bez odstających"]
    assert imp.table["Liczba ofert"].iloc[0] >= imp.table["Liczba ofert"].iloc[1]


# --- eksport -----------------------------------------------------------------------------------------------

def test_export_all_layers(dataset, tmp_path):
    res = run_analysis(dataset, dataset_settings(dataset))
    out = export.export_all(res, dataset, tmp_path / "out")
    gpkg = out.directory / "georent.gpkg"
    layers = {name for name, _ in pyogrio.list_layers(gpkg)}
    assert layers == {"oferty_punkty", "dzielnice_granice", "dzielnice_statystyki", "lisa_klastry"}
    for name in layers:
        info = pyogrio.read_info(gpkg, layer=name)
        assert "4326" in info["crs"], name
    pts = gpd.read_file(gpkg, layer="oferty_punkty")
    stats = gpd.read_file(gpkg, layer="dzielnice_statystyki")
    assert len(pts) == len(dataset.points) and pts.geometry.geom_type.eq("Point").all()
    assert len(stats) == 16 and stats.geometry.geom_type.isin(["Polygon", "MultiPolygon"]).all()
    assert {"median_price", "density_km2", "lisa_I", "lisa_p", "lisa_code", "lisa_cluster"} <= set(stats.columns)
    clusters = gpd.read_file(gpkg, layer="lisa_klastry")
    assert set(clusters["lisa_code"]) <= {"HH", "LL", "HL", "LH"} and len(clusters) == (stats["lisa_code"].isin(["HH", "LL", "HL", "LH"])).sum()
    # GeoJSON — ten sam układ i liczba obiektów
    gj = gpd.read_file(out.directory / "geojson" / "dzielnice_statystyki.geojson")
    assert len(gj) == 16 and gj.crs.to_epsg() == 4326
    # CSV z koordynatami
    csv = pd.read_csv(out.directory / "oferty_wspolrzedne.csv")
    assert {"latitude", "longitude", "price"} <= set(csv.columns) and len(csv) == len(pts)
    moran = pd.read_csv(out.directory / "moran_globalny.csv")
    assert moran["moran_I"].iloc[0] == pytest.approx(res.moran.I)
    assert moran["p_sim_dwustronne"].iloc[0] == pytest.approx(min(1.0, 2 * moran["p_sim_jednostronne"].iloc[0]))
    meta = json.loads((out.directory / "eksport_opis.json").read_text(encoding="utf-8"))
    assert meta["uklad_wspolrzednych"] == "EPSG:4326" and meta["wydanie_danych"] == "2026-09-17"


def test_export_is_overwritable_and_handles_missing_moran(dataset, tmp_path):
    s = dataset_settings(dataset)
    res = run_analysis(dataset, s, Filters(neighbourhoods=("D00", "D01")))
    assert res.moran is None
    out = export.export_all(res, dataset, tmp_path / "o")
    export.export_all(res, dataset, tmp_path / "o")  # ponowny zapis nie zgłasza błędu
    assert not (out.directory / "moran_globalny.csv").exists()
    layers = {n for n, _ in pyogrio.list_layers(out.directory / "georent.gpkg")}
    assert "lisa_klastry" not in layers  # brak klastrów, gdy nie policzono LISA
    assert "dzielnice_statystyki" in layers


# --- CLI ---------------------------------------------------------------------------------------------------

def test_cli_parser_and_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GEORENT_DATA_DIR", str(tmp_path / "pusto"))
    with pytest.raises(SystemExit) as exc:
        cli.main(["analyze", "--city", "barcelona"])
    assert "Brak lokalnych danych" in str(exc.value)
    with pytest.raises(SystemExit):
        cli.main(["analyze", "--city", "atlantyda"])


def test_cli_analyze_and_export_on_local_data(dataset, settings, monkeypatch, capsys):
    monkeypatch.setenv("GEORENT_DATA_DIR", str(settings.data_dir))
    monkeypatch.setenv("GEORENT_EXPORT_DIR", str(settings.export_dir))
    monkeypatch.setenv("GEORENT_PERMUTATIONS", "99")
    monkeypatch.setenv("GEORENT_MIN_LISTINGS", "5")
    assert cli.main(["analyze", "--city", "barcelona"]) == 0
    out = capsys.readouterr().out
    assert "Moran's I" in out and "High-High" in out
    assert cli.main(["export", "--city", "barcelona", "--no-qgis"]) == 0
    assert (settings.export_dir / "barcelona" / "georent.gpkg").exists()


def test_settings_env_validation(monkeypatch):
    from georent.config import Settings
    monkeypatch.setenv("GEORENT_PERMUTATIONS", "abc")
    with pytest.raises(ValueError, match="GEORENT_PERMUTATIONS"):
        Settings()


# --- projekt QGIS --------------------------------------------------------------------------------------------

def _pyqgis_available() -> bool:
    try:
        r = subprocess.run(["/usr/bin/python3", "-c", "import qgis.core"], capture_output=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


@pytest.mark.skipif(not _pyqgis_available(), reason="PyQGIS niedostępny w systemie")
def test_qgis_project_is_built_from_export(dataset, tmp_path):
    res = run_analysis(dataset, dataset_settings(dataset))
    out = export.export_all(res, dataset, tmp_path / "q")
    root = Path(__file__).resolve().parent.parent
    proc = subprocess.run(["/usr/bin/python3", "-m", "georent.qgis_project", str(out.directory)], cwd=root,
                          capture_output=True, text=True, env={"QT_QPA_PLATFORM": "offscreen", "PATH": "/usr/bin:/bin"}, timeout=300)
    assert proc.returncode == 0, proc.stderr
    qgz = out.directory / "georent.qgz"
    assert zipfile.is_zipfile(qgz)
    with zipfile.ZipFile(qgz) as zf:
        qgs = [n for n in zf.namelist() if n.endswith(".qgs")][0]
        xml = zf.read(qgs).decode("utf-8")
    assert "dzielnice_statystyki" in xml and "lisa_code" in xml and "./georent.gpkg" in xml  # ścieżki względne
