import numpy as np
import pandas as pd
import pytest
from libpysal import weights as lpw
from shapely.geometry import box

from georent import autocorr, spatial
from georent.cleaning import clean_listings
from tests.conftest import LAT0, LON0, STEP, make_grid, make_listings


def districts_from(grid, price_fn, per_district=12, **kw):
    cleaned, _ = clean_listings(make_listings(grid, per_district=per_district, price_fn=price_fn, **kw))
    pts = spatial.assign_neighbourhoods(spatial.make_points(cleaned), grid)
    return spatial.aggregate_neighbourhoods(pts, grid, min_listings=5)


def test_global_moran_positive_for_spatial_gradient(grid):
    d = districts_from(grid, lambda r, c: 60 + 60 * c)
    res = autocorr.compute_moran(d, "median_price", permutations=499, seed=1, min_listings=5)
    assert res.I > 0.4 and res.p_sim < 0.05 and res.significant
    assert res.n_obs == 16 and "dodatnia" in res.interpretation
    assert res.expected_I == pytest.approx(-1 / 15)
    assert res.p_sim == pytest.approx(min(1.0, 2 * res.p_sim_one_sided))  # decyzje na podstawie wartości dwustronnej


def test_global_moran_matches_manual_formula(grid):
    d = districts_from(grid, lambda r, c: 60 + 60 * c + 25 * (r % 2))
    res = autocorr.compute_moran(d, "median_price", permutations=99, seed=1, min_listings=5)
    sub = d.copy().to_crs(spatial.project_crs(d)).reset_index(drop=True)
    w = lpw.Queen.from_dataframe(sub, use_index=False)
    W = np.zeros((len(sub), len(sub)))
    for i, nbrs in w.neighbors.items():
        for j in nbrs:
            W[i, j] = 1.0 / len(nbrs)  # standaryzacja wierszowa
    z = sub["median_price"].to_numpy() - sub["median_price"].mean()
    manual = len(z) / W.sum() * (z @ W @ z) / (z @ z)
    assert res.I == pytest.approx(manual, rel=1e-9)


def test_global_moran_negative_for_checkerboard(grid):
    d = districts_from(grid, lambda r, c: 250 if (r + c) % 2 == 0 else 50)
    res = autocorr.compute_moran(d, "median_price", method="rook", permutations=499, seed=1, min_listings=5)
    assert res.I < -0.8 and res.p_sim < 0.05 and "ujemna" in res.interpretation


def test_global_moran_not_significant_for_random_values():
    grid = make_grid(6)
    rng = np.random.default_rng(3)
    values = rng.permutation(np.linspace(50, 300, 36)).reshape(6, 6)
    d = districts_from(grid, lambda r, c: float(values[r, c]))
    res = autocorr.compute_moran(d, "median_price", permutations=499, seed=1, min_listings=5)
    assert res.p_sim > 0.05 and not res.significant and "Brak istotnej" in res.interpretation


def test_lisa_clusters_on_gradient(grid):
    d = districts_from(grid, lambda r, c: 60 + 60 * c)
    res = autocorr.compute_moran(d, "median_price", permutations=499, seed=1, min_listings=5)
    out = res.districts.set_index("neighbourhood")
    assert set(out["lisa_code"]) <= {"HH", "LL", "HL", "LH", "ns", "n/a"}
    # skrajne kolumny (c = 0 oraz c = 3) tworzą klastry Low-Low i High-High
    assert (out.loc[[f"D{r}0" for r in range(4)], "lisa_code"].isin(["LL", "ns"])).all()
    assert (out.loc[[f"D{r}3" for r in range(4)], "lisa_code"].isin(["HH", "ns"])).all()
    assert (out["lisa_code"] == "LL").any() and (out["lisa_code"] == "HH").any()
    assert out["lisa_cluster"].notna().all()
    # kod klastra jest spójny z p-wartością i kwadrantem
    sig = out[out["lisa_code"].isin(["HH", "LL", "HL", "LH"])]
    assert (sig["lisa_p"] < 0.05).all()
    assert ((sig["lisa_code"].isin(["HH", "HL"])) == (sig["z_value"] > 0)).all()
    assert ((sig["lisa_code"].isin(["HH", "LH"])) == (sig["z_lag"] > 0)).all()


def test_lisa_hl_and_lh_outlier_districts():
    grid = make_grid(5)
    # jeden bardzo drogi „wyspowy” obszar w środku tanich dzielnic -> High-Low; reszta tania
    d = districts_from(grid, lambda r, c: 400 if (r, c) == (2, 2) else 50 + 2 * (r + c))
    res = autocorr.compute_moran(d, "median_price", permutations=999, seed=2, min_listings=5, alpha=0.1)
    assert res.districts.set_index("neighbourhood").loc["D22", "lisa_code"] in {"HL", "ns"}
    assert (res.districts["lisa_code"] != "n/a").all()


def test_lisa_has_all_quadrant_types_possible(grid):
    d = districts_from(grid, lambda r, c: 60 + 60 * c)
    res = autocorr.compute_moran(d, "median_price", permutations=99, seed=1, min_listings=5)
    counts = autocorr.cluster_counts(res.districts)
    assert counts["Liczba dzielnic"].sum() == len(grid) and list(counts["Kod"]) == list(autocorr.CLUSTER_LABELS)


def test_islands_are_excluded_and_reported():
    grid = make_grid(4)
    import geopandas as gpd
    far = gpd.GeoDataFrame({"neighbourhood": ["WYSPA"], "neighbourhood_group": ["X"]},
                           geometry=[box(LON0 + 0.5, LAT0 + 0.5, LON0 + 0.51, LAT0 + 0.51)], crs="EPSG:4326")
    grid = spatial.prepare_neighbourhoods(
        gpd.GeoDataFrame(pd.concat([grid.drop(columns="area_km2"), far], ignore_index=True), crs="EPSG:4326"))
    d = districts_from(grid, lambda r, c: 60 + 60 * c if r < 4 else 100)  # WYSPA = pozycja 16 -> r = 4 w make_listings
    res = autocorr.compute_moran(d, "median_price", permutations=99, seed=1, min_listings=5)
    assert "WYSPA" in res.excluded and "wyspa" in res.excluded["WYSPA"]
    assert res.n_obs == 16
    assert res.districts.set_index("neighbourhood").loc["WYSPA", "lisa_code"] == "n/a"


def test_districts_without_offers_or_unreliable_are_excluded(grid, listings):
    d = districts_from(grid, lambda r, c: 60 + 60 * c, per_district=12)
    d.loc[d["neighbourhood"] == "D00", ["n_priced", "price_reliable", "median_price"]] = [0, False, np.nan]
    res = autocorr.compute_moran(d, "median_price", permutations=99, seed=1, min_listings=5)
    assert "D00" in res.excluded and res.n_obs == 15
    assert np.isnan(res.districts.set_index("neighbourhood").loc["D00", "lisa_I"])


def test_too_few_districts_raises(grid):
    d = districts_from(grid, lambda r, c: 60 + 60 * c)
    with pytest.raises(autocorr.InsufficientDataError, match="wymagane co najmniej"):
        autocorr.compute_moran(d.head(5), "median_price", min_listings=5)


def test_constant_variable_raises(grid):
    d = districts_from(grid, lambda r, c: 100.0)
    d["median_price"] = 100.0
    with pytest.raises(autocorr.InsufficientDataError, match="stała"):
        autocorr.compute_moran(d, "median_price", permutations=99, min_listings=5)


def test_unknown_inputs_raise(grid):
    d = districts_from(grid, lambda r, c: 60 + 60 * c)
    with pytest.raises(ValueError, match="Brak zmiennej"):
        autocorr.compute_moran(d, "nie_ma_takiej")
    with pytest.raises(ValueError, match="Nieznana metoda"):
        autocorr.compute_moran(d, "median_price", method="xyz")


def test_results_are_reproducible_with_seed(grid):
    d = districts_from(grid, lambda r, c: 60 + 60 * c + 15 * ((r * 7 + c * 3) % 4))
    a = autocorr.compute_moran(d, "median_price", permutations=199, seed=11, min_listings=5)
    b = autocorr.compute_moran(d, "median_price", permutations=199, seed=11, min_listings=5)
    assert a.I == b.I and a.p_sim == b.p_sim
    pd.testing.assert_series_equal(a.districts["lisa_p"], b.districts["lisa_p"])


@pytest.mark.parametrize("method", ["queen", "rook", "knn"])
def test_all_weight_methods_work(grid, method):
    d = districts_from(grid, lambda r, c: 60 + 60 * c)
    res = autocorr.compute_moran(d, "median_price", method=method, permutations=99, seed=1, min_listings=5)
    assert res.I > 0 and res.method == method
    assert not res.summary_table().empty


def test_queen_has_more_neighbors_than_rook(grid):
    d = districts_from(grid, lambda r, c: 60 + 60 * c)
    queen = autocorr.compute_moran(d, "median_price", method="queen", permutations=9, min_listings=5)
    rook = autocorr.compute_moran(d, "median_price", method="rook", permutations=9, min_listings=5)
    assert queen.mean_neighbors > rook.mean_neighbors


def test_interpretation_texts():
    assert "dodatnia" in autocorr.interpret_global_moran(0.4, 0.001)
    assert "ujemna" in autocorr.interpret_global_moran(-0.4, 0.001)
    assert "Brak istotnej" in autocorr.interpret_global_moran(0.05, 0.4)
