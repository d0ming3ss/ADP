"""Czyszczenie i przygotowanie tabeli ofert Inside Airbnb.

Pipeline jest deterministyczny i powtarzalny: ta sama tabela wejściowa i te same parametry
dają ten sam wynik oraz ten sam raport jakości (:class:`CleaningReport`).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from georent.data_source import DatasetMetadata

logger = logging.getLogger(__name__)

# Kolumny, które pipeline stara się zachować (brakujące kolumny opcjonalne są pomijane).
KEEP_COLUMNS = [
    "id", "name", "listing_url", "host_id", "host_is_superhost",
    "neighbourhood_cleansed", "neighbourhood_group_cleansed", "latitude", "longitude",
    "property_type", "room_type", "accommodates", "bedrooms", "beds", "price",
    "minimum_nights", "has_availability", "availability_365", "number_of_reviews",
    "review_scores_rating", "estimated_occupancy_l365d", "estimated_revenue_l365d",
    "instant_bookable",
]
REQUIRED_COLUMNS = ["id", "latitude", "longitude", "price", "room_type", "accommodates", "neighbourhood_cleansed"]

_PRICE_STRIP = re.compile(r"[^\d.,\-]")
_COMMA_DECIMAL = re.compile(r",\d{1,2}$")


@dataclass
class CleaningParams:
    """Parametry czyszczenia."""

    outlier_iqr_k: float = 1.5  # mnożnik IQR dla płotków Tukeya liczonych na ln(ceny)
    bounds: tuple[float, float, float, float] | None = None  # (minx, miny, maxx, maxy) w stopniach
    bounds_margin_deg: float = 0.1  # margines wokół obszaru dzielnic


@dataclass
class CleaningReport:
    """Liczności po kolejnych krokach czyszczenia oraz parametry progów."""

    rows_raw: int = 0
    duplicates_removed: int = 0
    rows_after_dedup: int = 0
    price_missing: int = 0
    price_nonpositive: int = 0
    price_valid: int = 0
    invalid_coordinates: int = 0
    coordinates_outside_area: int = 0
    price_outliers: int = 0
    outlier_lower_bound: float | None = None
    outlier_upper_bound: float | None = None
    missing_by_column: dict[str, int] = field(default_factory=dict)
    currency_symbols: list[str] = field(default_factory=list)

    def as_table(self) -> pd.DataFrame:
        """Zestawienie do wyświetlenia w interfejsie."""
        rows = [
            ("Rekordy w pliku źródłowym", self.rows_raw),
            ("Usunięte duplikaty (powtórzone id)", self.duplicates_removed),
            ("Rekordy po deduplikacji", self.rows_after_dedup),
            ("Brak ceny (pole puste)", self.price_missing),
            ("Cena niepoprawna (≤ 0 lub nieliczbowa)", self.price_nonpositive),
            ("Rekordy z poprawną ceną", self.price_valid),
            ("Niepoprawne współrzędne (brak / poza zakresem / 0,0)", self.invalid_coordinates),
            ("Współrzędne poza obszarem miasta", self.coordinates_outside_area),
            ("Oznaczone jako odstające (cena, IQR na ln)", self.price_outliers),
        ]
        return pd.DataFrame(rows, columns=["Krok", "Liczba"])


def parse_price(values: pd.Series) -> pd.Series:
    """Zamień ceny tekstowe (``"$1,234.50"``, ``"1.234,50 €"``, ``"85"``) na liczby zmiennoprzecinkowe.

    Obsługiwane są symbole walut, spacje (także niełamliwe), separator tysięcy w postaci
    przecinka lub kropki oraz przecinek dziesiętny. Wartości puste lub nieparsowalne
    dają ``NaN`` — nigdy wartość domyślną.
    """

    def _parse(raw: object) -> float:
        if raw is None or (isinstance(raw, float) and np.isnan(raw)):
            return np.nan
        if isinstance(raw, (int, float, np.integer, np.floating)):
            return float(raw)
        text = _PRICE_STRIP.sub("", str(raw))
        if not text or not re.search(r"\d", text):
            return np.nan
        negative = text.startswith("-")
        text = text.replace("-", "")
        if "," in text and "." in text:
            # ostatni separator jest dziesiętny, drugi to separator tysięcy
            decimal = "," if text.rfind(",") > text.rfind(".") else "."
            thousands = "." if decimal == "," else ","
            text = text.replace(thousands, "").replace(decimal, ".")
        elif "," in text:
            text = text.replace(",", ".") if _COMMA_DECIMAL.search(text) and text.count(",") == 1 else text.replace(",", "")
        elif text.count(".") > 1:
            text = text.replace(".", "")
        try:
            value = float(text)
        except ValueError:
            return np.nan
        return -value if negative else value

    return values.map(_parse).astype("float64")


def detect_currency_symbols(values: pd.Series) -> list[str]:
    """Wykryj symbole walut występujące w kolumnie cen (do informacji o źródle)."""
    sample = values.dropna().astype(str).head(5000)
    symbols = set(re.findall(r"[^\d\s.,\-]", "".join(sample)))
    return sorted(symbols)


def flag_price_outliers(price: pd.Series, k: float = 1.5) -> tuple[pd.Series, float | None, float | None]:
    """Oznacz obserwacje odstające płotkami Tukeya na ln(ceny).

    Zwraca (maska odstających, dolna granica ceny, górna granica ceny). Obserwacje bez ceny
    nie są oznaczane. Odstające nie są usuwane — to decyzja analizy (patrz filtr w aplikacji).
    """
    valid = price.dropna()
    valid = valid[valid > 0]
    if len(valid) < 4:
        return pd.Series(False, index=price.index), None, None
    logp = np.log(valid)
    q1, q3 = logp.quantile([0.25, 0.75])
    iqr = q3 - q1
    low, high = float(np.exp(q1 - k * iqr)), float(np.exp(q3 + k * iqr))
    mask = (price < low) | (price > high)
    return mask.fillna(False), low, high


def _to_bool(series: pd.Series) -> pd.Series:
    mapped = series.astype("string").str.lower().map({"t": True, "true": True, "f": False, "false": False})
    return mapped.astype("boolean")


def clean_listings(raw: pd.DataFrame, params: CleaningParams | None = None) -> tuple[pd.DataFrame, CleaningReport]:
    """Oczyść tabelę ofert i zwróć ją wraz z raportem.

    Dodawane kolumny: ``coord_valid`` (współrzędne poprawne), ``has_price``, ``price_outlier``,
    ``price_per_person``. Rekordy z niepoprawną ceną lub współrzędnymi **nie są kasowane** — są
    oznaczone, a dalsze etapy decydują, czy je wykorzystać.
    """
    params = params or CleaningParams()
    missing_required = [c for c in REQUIRED_COLUMNS if c not in raw.columns]
    if missing_required:
        raise ValueError(f"Brak wymaganych kolumn w danych: {missing_required}")

    report = CleaningReport(rows_raw=len(raw))
    df = raw[[c for c in KEEP_COLUMNS if c in raw.columns]].copy()

    # 1) duplikaty
    df = df.drop_duplicates(subset="id", keep="first")
    report.duplicates_removed = report.rows_raw - len(df)
    report.rows_after_dedup = len(df)

    # 2) typy danych
    df["id"] = df["id"].astype("int64")
    for col in ("accommodates", "bedrooms", "beds", "minimum_nights", "availability_365",
                "number_of_reviews", "review_scores_rating", "estimated_occupancy_l365d",
                "estimated_revenue_l365d"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ("has_availability", "instant_bookable", "host_is_superhost"):
        if col in df.columns:
            df[col] = _to_bool(df[col])
    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")

    # 3) cena
    report.currency_symbols = detect_currency_symbols(df["price"])
    price_missing_mask = df["price"].isna() | (df["price"].astype("string").str.strip() == "")
    df["price"] = parse_price(df["price"])
    report.price_missing = int(price_missing_mask.sum())
    nonpositive = df["price"].notna() & (df["price"] <= 0)
    unparsable = df["price"].isna() & ~price_missing_mask
    report.price_nonpositive = int(nonpositive.sum() + unparsable.sum())
    df.loc[nonpositive, "price"] = np.nan
    df["has_price"] = df["price"].notna()
    report.price_valid = int(df["has_price"].sum())

    # 4) współrzędne
    lat, lon = df["latitude"], df["longitude"]
    coord_valid = lat.between(-90, 90) & lon.between(-180, 180) & ~((lat == 0) & (lon == 0))
    report.invalid_coordinates = int((~coord_valid).sum())
    if params.bounds is not None:
        minx, miny, maxx, maxy = params.bounds
        m = params.bounds_margin_deg
        inside = lon.between(minx - m, maxx + m) & lat.between(miny - m, maxy + m)
        report.coordinates_outside_area = int((coord_valid & ~inside).sum())
        coord_valid &= inside
    df["coord_valid"] = coord_valid.fillna(False)

    # 5) odstające i cena na osobę
    outlier, low, high = flag_price_outliers(df["price"], params.outlier_iqr_k)
    df["price_outlier"] = outlier
    report.price_outliers = int(outlier.sum())
    report.outlier_lower_bound, report.outlier_upper_bound = low, high
    df["price_per_person"] = df["price"] / df["accommodates"].where(df["accommodates"] > 0)

    report.missing_by_column = {c: int(n) for c, n in df.isna().sum().items() if n}
    logger.info("Czyszczenie: %d -> %d rekordów, %d z poprawną ceną", report.rows_raw, len(df), report.price_valid)
    return df.reset_index(drop=True), report


def load_raw_listings(meta: DatasetMetadata) -> pd.DataFrame:
    """Wczytaj surowe oferty z lokalnego ``listings.csv.gz``."""
    path = meta.path("listings.csv.gz")
    wanted = set(KEEP_COLUMNS)
    return pd.read_csv(path, usecols=lambda c: c in wanted)
