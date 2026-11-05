"""Test integracyjny na rzeczywistych danych Inside Airbnb (pomijany, jeśli dane nie zostały pobrane)."""

import pytest

from georent import analysis, autocorr, data_source
from georent.config import get_settings

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def real():
    settings = get_settings()
    meta = data_source.get_local_dataset(settings)
    if meta is None:
        pytest.skip("Brak pobranych danych (python -m georent download)")
    return settings, analysis.prepare_dataset(meta, settings.city_spec())


def test_real_pipeline_quality(real):
    settings, ds = real
    assert ds.cleaning.rows_raw == ds.meta.listings_rows
    assert ds.cleaning.invalid_coordinates == 0
    assert ds.polygons["neighbourhood"].is_unique  # powtórzone nazwy zostały scalone (dissolve)
    assert 0.9 * ds.meta.neighbourhood_features <= len(ds.polygons) <= ds.meta.neighbourhood_features
    assert ds.points["outside_boundaries"].mean() < 0.01
    assert ds.join_agreement > 0.99  # Spatial Join zgodny z etykietą źródłową


def test_real_moran_is_positive_and_significant(real):
    settings, ds = real
    res = analysis.run_analysis(ds, settings)
    assert res.moran is not None
    assert res.moran.I > 0.2 and res.moran.p_sim < 0.05
    assert (res.districts["lisa_code"] == "HH").any() and (res.districts["lisa_code"] == "LL").any()
    assert res.districts["n_listings"].sum() == len(res.points)
