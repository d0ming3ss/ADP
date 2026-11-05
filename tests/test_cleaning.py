import numpy as np
import pandas as pd
import pytest

from georent.cleaning import CleaningParams, clean_listings, detect_currency_symbols, flag_price_outliers, parse_price
from tests.conftest import make_grid, make_listings


@pytest.mark.parametrize("raw, expected", [
    ("$1,234.50", 1234.5), ("$274.00", 274.0), ("1.234,50 €", 1234.5), ("€ 85", 85.0), ("85,5", 85.5),
    ("1,234", 1234.0), ("1.234.567", 1234567.0), ("  $ 99.99  ", 99.99), ("$0.00", 0.0), (120, 120.0),
    (None, np.nan), (np.nan, np.nan), ("", np.nan), ("abc", np.nan), ("$", np.nan),
])
def test_parse_price(raw, expected):
    result = parse_price(pd.Series([raw], dtype=object)).iloc[0]
    if np.isnan(expected):
        assert np.isnan(result)
    else:
        assert result == pytest.approx(expected)


def test_parse_price_nbsp_and_negative():
    out = parse_price(pd.Series(["1 234,50 zł", "-$5.00"], dtype=object))
    assert out.iloc[0] == pytest.approx(1234.5)
    assert out.iloc[1] == pytest.approx(-5.0)


def test_detect_currency():
    assert detect_currency_symbols(pd.Series(["$10.00", "$20.00", None])) == ["$"]


def test_clean_basic_columns_and_types(listings):
    df, rep = clean_listings(listings)
    assert rep.rows_raw == len(listings) == len(df)
    assert df["price"].dtype == "float64"
    assert df["has_availability"].dtype == "boolean"
    assert df["coord_valid"].all() and df["has_price"].all()
    assert (df["price_per_person"] == df["price"] / df["accommodates"]).all()
    assert rep.currency_symbols == ["$"]


def test_clean_removes_duplicates(listings):
    dup = pd.concat([listings, listings.head(5)])
    df, rep = clean_listings(dup)
    assert rep.duplicates_removed == 5
    assert df["id"].is_unique


def test_clean_missing_and_nonpositive_prices(listings):
    listings = listings.copy()
    listings.loc[0, "price"] = None
    listings.loc[1, "price"] = ""
    listings.loc[2, "price"] = "$0.00"
    listings.loc[3, "price"] = "brak"
    df, rep = clean_listings(listings)
    assert rep.price_missing == 2
    assert rep.price_nonpositive == 2
    assert rep.price_valid == len(listings) - 4
    assert df.loc[:3, "price"].isna().all()
    assert not df.loc[:3, "has_price"].any()
    assert len(df) == len(listings)  # rekordy nie są kasowane


def test_clean_invalid_coordinates(listings):
    listings = listings.copy()
    listings[["latitude", "longitude"]] = listings[["latitude", "longitude"]].astype(object)
    listings.loc[0, ["latitude", "longitude"]] = [95.0, 2.1]
    listings.loc[1, ["latitude", "longitude"]] = [41.3, 200.0]
    listings.loc[2, ["latitude", "longitude"]] = [0.0, 0.0]
    listings.loc[3, "latitude"] = np.nan
    listings.loc[4, "longitude"] = "nie-liczba"
    df, rep = clean_listings(listings)
    assert rep.invalid_coordinates == 5
    assert not df.loc[:4, "coord_valid"].any()
    assert df.loc[5:, "coord_valid"].all()


def test_clean_coordinates_outside_area(listings, grid):
    listings = listings.copy()
    listings.loc[0, ["latitude", "longitude"]] = [48.85, 2.35]  # Paryż
    df, rep = clean_listings(listings, CleaningParams(bounds=tuple(grid.total_bounds)))
    assert rep.coordinates_outside_area == 1
    assert not df.loc[0, "coord_valid"]
    assert rep.invalid_coordinates == 0


def test_clean_empty_dataframe(listings):
    df, rep = clean_listings(listings.iloc[0:0])
    assert df.empty and rep.rows_raw == 0 and rep.price_outliers == 0
    assert rep.outlier_upper_bound is None


def test_clean_missing_required_column(listings):
    with pytest.raises(ValueError, match="Brak wymaganych kolumn"):
        clean_listings(listings.drop(columns=["room_type"]))


def test_clean_is_idempotent_for_same_input(listings):
    a, ra = clean_listings(listings)
    b, rb = clean_listings(listings)
    pd.testing.assert_frame_equal(a, b)
    assert ra == rb


def test_outliers_flagged_not_removed(listings):
    listings = listings.copy()
    listings.loc[0, "price"] = "$99,999.00"
    df, rep = clean_listings(listings)
    assert bool(df.loc[0, "price_outlier"])
    assert rep.price_outliers >= 1
    assert len(df) == len(listings)


def test_flag_price_outliers_small_sample():
    mask, lo, hi = flag_price_outliers(pd.Series([10.0, 20.0]))
    assert not mask.any() and lo is None and hi is None


def test_flag_price_outliers_bounds():
    prices = pd.Series([100.0] * 50 + [110.0] * 50 + [5000.0, 1.0])
    mask, lo, hi = flag_price_outliers(prices, k=1.5)
    assert mask.iloc[-1] and mask.iloc[-2]
    assert mask.sum() == 2 and lo < 100 < hi
