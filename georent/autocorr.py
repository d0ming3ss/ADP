"""Autokorelacja przestrzenna cen: Global Moran's I oraz lokalne wskaźniki LISA (PySAL).

Używane biblioteki: ``libpysal`` (macierze wag przestrzennych) i ``esda`` (statystyki Morana).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import esda
import geopandas as gpd
import numpy as np
import pandas as pd
from libpysal import weights as lpw

from georent.spatial import project_crs

logger = logging.getLogger(__name__)

WEIGHT_METHODS = {
    "queen": "Queen (wspólny bok lub wierzchołek)",
    "rook": "Rook (wspólny bok)",
    "knn": "k najbliższych sąsiadów (k = 5, centroidy)",
}
KNN_K = 5
MIN_OBSERVATIONS = 8  # poniżej tej liczby jednostek test permutacyjny nie jest wiarygodny

CLUSTER_LABELS = {
    "HH": "High-High (gorące punkty)",
    "LL": "Low-Low (zimne punkty)",
    "HL": "High-Low (wysoka wśród niskich)",
    "LH": "Low-High (niska wśród wysokich)",
    "ns": "Brak istotności",
    "n/a": "Brak danych / sąsiadów",
}
CLUSTER_COLORS = {
    "HH": "#d7191c", "LL": "#2c7bb6", "HL": "#fdae61", "LH": "#abd9e9", "ns": "#e0e0e0", "n/a": "#ffffff",
}
_QUADRANT_TO_CODE = {1: "HH", 2: "LH", 3: "LL", 4: "HL"}


class InsufficientDataError(Exception):
    """Zbyt mało jednostek (lub połączonych sąsiadów) do wiarygodnej analizy autokorelacji."""


@dataclass
class MoranResult:
    """Wynik analizy autokorelacji dla jednej zmiennej."""

    variable: str
    method: str
    permutations: int
    seed: int
    alpha: float
    n_obs: int
    I: float
    expected_I: float
    z_sim: float
    p_sim: float  # pseudo-p dwustronne = min(1, 2 × jednostronne pseudo-p z esda)
    p_sim_one_sided: float  # pseudo-p jednostronne (w kierunku znaku I), jak zwraca esda.Moran
    p_norm: float
    mean_neighbors: float
    excluded: dict[str, str] = field(default_factory=dict)  # dzielnica -> powód wyłączenia
    districts: gpd.GeoDataFrame | None = None  # wszystkie dzielnice + kolumny LISA

    @property
    def significant(self) -> bool:
        return self.p_sim < self.alpha

    @property
    def interpretation(self) -> str:
        return interpret_global_moran(self.I, self.p_sim, self.alpha)

    def summary_table(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                ("Zmienna", self.variable),
                ("Macierz wag", WEIGHT_METHODS[self.method] + ", standaryzacja wierszowa"),
                ("Liczba jednostek w analizie", self.n_obs),
                ("Średnia liczba sąsiadów", round(self.mean_neighbors, 2)),
                ("Moran's I", round(self.I, 4)),
                ("E[I] przy braku autokorelacji", round(self.expected_I, 4)),
                ("z (symulacja)", round(self.z_sim, 3)),
                (f"pseudo-p dwustronne (permutacje = {self.permutations})", round(self.p_sim, 4)),
                ("pseudo-p jednostronne (esda)", round(self.p_sim_one_sided, 4)),
                ("p (aproksymacja normalna)", round(self.p_norm, 4)),
            ],
            columns=["Parametr", "Wartość"],
        )


def interpret_global_moran(i: float, p: float, alpha: float = 0.05) -> str:
    """Opisowa interpretacja Global Moran's I (po polsku)."""
    if p >= alpha:
        return (
            f"Brak istotnej statystycznie autokorelacji przestrzennej (I = {i:.3f}, pseudo-p = {p:.3f} ≥ {alpha}). "
            "Nie ma podstaw, by odrzucić hipotezę o losowym rozmieszczeniu wartości w przestrzeni."
        )
    if i > 0:
        return (
            f"Istotna dodatnia autokorelacja przestrzenna (I = {i:.3f}, pseudo-p = {p:.3f} < {alpha}): "
            "dzielnice o podobnym poziomie cen sąsiadują ze sobą — ceny tworzą skupiska."
        )
    return (
        f"Istotna ujemna autokorelacja przestrzenna (I = {i:.3f}, pseudo-p = {p:.3f} < {alpha}): "
        "sąsiednie dzielnice mają tendencję do skrajnie różnych cen (układ „szachownicy”)."
    )


# --- Macierz wag ---------------------------------------------------------------------------------

def _build_weights(gdf: gpd.GeoDataFrame, method: str) -> lpw.W:
    projected = gdf.to_crs(project_crs(gdf)).reset_index(drop=True)
    if method == "queen":
        w = lpw.Queen.from_dataframe(projected, use_index=False)
    elif method == "rook":
        w = lpw.Rook.from_dataframe(projected, use_index=False)
    elif method == "knn":
        w = lpw.KNN.from_dataframe(projected, k=min(KNN_K, len(projected) - 1), use_index=False)
    else:
        raise ValueError(f"Nieznana metoda wag: {method!r}. Dostępne: {sorted(WEIGHT_METHODS)}")
    return w


def build_weights_without_islands(
    gdf: gpd.GeoDataFrame, method: str
) -> tuple[lpw.W, gpd.GeoDataFrame, list[str]]:
    """Zbuduj wagi i usuń jednostki bez sąsiadów (wyspy).

    Zwraca (wagi wierszowo standaryzowane, GeoDataFrame bez wysp, nazwy usuniętych dzielnic).
    Dla ``knn`` wysp nie ma z definicji. Wyspy są usuwane, bo Moran's I nie jest dla nich zdefiniowany.
    """
    w = _build_weights(gdf, method)
    islands_idx = list(w.islands)
    dropped = gdf.reset_index(drop=True).loc[islands_idx, "neighbourhood"].tolist() if islands_idx else []
    if islands_idx:
        gdf = gdf.reset_index(drop=True).drop(index=islands_idx).reset_index(drop=True)
        w = _build_weights(gdf, method)
    w.transform = "R"
    return w, gdf.reset_index(drop=True), dropped


# --- Moran ---------------------------------------------------------------------------------------

def compute_moran(
    districts: gpd.GeoDataFrame,
    variable: str = "median_price",
    *,
    method: str = "queen",
    permutations: int = 999,
    seed: int = 42,
    alpha: float = 0.05,
    min_listings: int = 10,
) -> MoranResult:
    """Oblicz Global Moran's I i lokalne Moran's I (LISA) dla zmiennej na poziomie dzielnic.

    Do analizy wchodzą dzielnice, które mają niepustą wartość zmiennej i co najmniej
    ``min_listings`` ofert z ceną (``price_reliable``). Dzielnice bez sąsiadów (w zbiorze
    tak zredukowanym) są wyłączane i raportowane w ``excluded``. Istotność wyznaczana jest
    testem permutacyjnym (``permutations`` losowych przestawień wartości, ziarno ``seed``); decyzje
    o istotności opierają się na pseudo-p **dwustronnym** (globalnym: 2 × jednostronne z esda; lokalnym: ``alternative='two-sided'``).

    Zwracany ``districts`` zawiera wszystkie dzielnice wejściowe z kolumnami: ``lisa_I``,
    ``lisa_p``, ``lisa_z``, ``lisa_code`` (HH/LL/HL/LH/ns/n/a), ``lisa_cluster``,
    ``lisa_sig_fdr``, ``z_value`` (standaryzowana wartość) oraz ``z_lag`` (średnia sąsiadów).
    """
    if variable not in districts.columns:
        raise ValueError(f"Brak zmiennej {variable!r} w danych dzielnic.")
    if method not in WEIGHT_METHODS:
        raise ValueError(f"Nieznana metoda wag: {method!r}. Dostępne: {sorted(WEIGHT_METHODS)}")

    out = districts.reset_index(drop=True).copy()
    excluded: dict[str, str] = {}
    usable = out[variable].notna() & out["price_reliable"] if "price_reliable" in out else out[variable].notna()
    for name in out.loc[~usable, "neighbourhood"]:
        excluded[name] = f"za mało ofert z ceną (< {min_listings}) lub brak wartości"

    candidates = out[usable].copy()
    candidates["_pos"] = candidates.index  # pozycja w tabeli wynikowej
    if len(candidates) < MIN_OBSERVATIONS:
        raise InsufficientDataError(
            f"Do analizy autokorelacji kwalifikuje się {len(candidates)} dzielnic; wymagane co najmniej "
            f"{MIN_OBSERVATIONS}. Poluzuj filtry lub zmniejsz próg minimalnej liczby ofert."
        )

    w, sub, islands = build_weights_without_islands(candidates, method)
    for name in islands:
        excluded[name] = "brak sąsiadów w zbiorze dzielnic z wiarygodnymi cenami (wyspa)"
    if len(sub) < MIN_OBSERVATIONS:
        raise InsufficientDataError(
            f"Po usunięciu dzielnic bez sąsiadów pozostało {len(sub)} jednostek; wymagane co najmniej {MIN_OBSERVATIONS}."
        )

    y = sub[variable].to_numpy(dtype=float)
    if np.isclose(np.nanstd(y), 0.0):
        raise InsufficientDataError("Zmienna jest stała we wszystkich dzielnicach — autokorelacja nie jest określona.")

    # Wynik musi być powtarzalny: ziarno dla testu globalnego (esda.Moran nie ma parametru seed).
    np.random.seed(seed)
    moran = esda.Moran(y, w, permutations=permutations)
    lisa = esda.Moran_Local(y, w, permutations=permutations, seed=seed, alternative="two-sided")

    codes = np.where(lisa.p_sim < alpha, [_QUADRANT_TO_CODE[int(q)] for q in lisa.q], "ns")
    try:
        fdr_threshold = esda.fdr(lisa.p_sim, alpha)
        sig_fdr = lisa.p_sim <= fdr_threshold
    except ValueError:  # np. brak p-wartości spełniających warunek
        sig_fdr = np.zeros(len(y), dtype=bool)
    z = (y - y.mean()) / y.std()
    lag = lpw.lag_spatial(w, z)

    out["lisa_I"] = np.nan
    out["lisa_p"] = np.nan
    out["lisa_z"] = np.nan
    out["z_value"] = np.nan
    out["z_lag"] = np.nan
    out["lisa_sig_fdr"] = False
    out["lisa_code"] = "n/a"
    pos = sub["_pos"].to_numpy()
    rows = np.arange(len(sub))
    out.loc[pos, "lisa_I"] = lisa.Is[rows]
    out.loc[pos, "lisa_p"] = lisa.p_sim[rows]
    out.loc[pos, "lisa_z"] = lisa.z_sim[rows]
    out.loc[pos, "z_value"] = z[rows]
    out.loc[pos, "z_lag"] = lag[rows]
    out.loc[pos, "lisa_sig_fdr"] = sig_fdr[rows]
    out.loc[pos, "lisa_code"] = codes[rows]
    out["lisa_cluster"] = out["lisa_code"].map(CLUSTER_LABELS)

    logger.info("Moran's I (%s, %s) = %.4f, pseudo-p (dwustronne) = %.4f, n = %d", variable, method, moran.I, min(1.0, 2 * moran.p_sim), len(y))
    return MoranResult(
        variable=variable, method=method, permutations=permutations, seed=seed, alpha=alpha,
        n_obs=len(y), I=float(moran.I), expected_I=float(moran.EI), z_sim=float(moran.z_sim),
        p_sim=min(1.0, 2.0 * float(moran.p_sim)), p_sim_one_sided=float(moran.p_sim), p_norm=float(moran.p_norm),
        mean_neighbors=float(w.mean_neighbors), excluded=excluded, districts=out,
    )


def cluster_counts(districts: gpd.GeoDataFrame) -> pd.DataFrame:
    """Liczba dzielnic w poszczególnych klasach LISA."""
    counts = districts["lisa_code"].value_counts().reindex(list(CLUSTER_LABELS), fill_value=0)
    return pd.DataFrame(
        {"Klasa": [CLUSTER_LABELS[c] for c in counts.index], "Kod": counts.index, "Liczba dzielnic": counts.to_numpy()}
    )
