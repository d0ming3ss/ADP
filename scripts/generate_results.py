"""Generuje docs/WYNIKI.md oraz rysunki docs/img/*.png wyłącznie z obliczeń na pobranych danych.

Użycie (po ``python -m georent download``):

    python scripts/generate_results.py [--city barcelona]

Żadna wartość liczbowa nie jest wpisywana ręcznie — raport jest odtwarzalny (ziarno losowe z konfiguracji).
"""

from __future__ import annotations

import argparse
import sys
import warnings
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from georent import analysis, autocorr, data_source  # noqa: E402
from georent.analysis import Filters  # noqa: E402
from georent.config import get_settings  # noqa: E402
from georent.spatial import METRICS  # noqa: E402

warnings.filterwarnings("ignore")


def md_table(df: pd.DataFrame, floatfmt: str = "{:,.2f}") -> str:
    def fmt(v):
        if isinstance(v, float):
            return "—" if pd.isna(v) else floatfmt.format(v).replace(",", " ")
        return str(v)
    head = "| " + " | ".join(map(str, df.columns)) + " |\n|" + "|".join("---" for _ in df.columns) + "|\n"
    return head + "\n".join("| " + " | ".join(fmt(v) for v in row) + " |" for row in df.itertuples(index=False))


def choropleth_png(districts, column, title, path, cmap="YlOrRd"):
    fig, ax = plt.subplots(figsize=(7, 7))
    districts.plot(column=column, cmap=cmap, scheme="quantiles", k=5, legend=True, ax=ax, edgecolor="#555", linewidth=0.4,
                   missing_kwds={"color": "#d9d9d9", "label": "brak danych"}, legend_kwds={"loc": "lower left", "fontsize": 8})
    ax.set_title(title); ax.set_axis_off()
    fig.savefig(path, dpi=130, bbox_inches="tight"); plt.close(fig)


def lisa_png(districts, path):
    fig, ax = plt.subplots(figsize=(7, 7))
    for code in ("n/a", "ns", "LL", "LH", "HL", "HH"):
        part = districts[districts["lisa_code"] == code]
        if len(part):
            part.plot(ax=ax, color=autocorr.CLUSTER_COLORS[code], edgecolor="#555", linewidth=0.4, label=autocorr.CLUSTER_LABELS[code])
    ax.legend(loc="lower left", fontsize=8); ax.set_title("Klastry LISA — mediana ceny za noc"); ax.set_axis_off()
    fig.savefig(path, dpi=130, bbox_inches="tight"); plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", default=None)
    args = parser.parse_args()
    settings = get_settings()
    if args.city:
        settings = replace(settings, city=args.city)
    meta = data_source.get_local_dataset(settings)
    if meta is None:
        raise SystemExit("Brak danych — uruchom: python -m georent download")
    ds = analysis.prepare_dataset(meta, settings.city_spec())
    res = analysis.run_analysis(ds, settings)
    m = res.moran
    img = ROOT / "docs" / "img"
    img.mkdir(parents=True, exist_ok=True)

    d = res.districts
    reliable = d[d["price_reliable"]]
    lines: list[str] = []
    w = lines.append
    w(f"# Wyniki analizy — {ds.city.label}\n")
    w("> Plik wygenerowany automatycznie przez `scripts/generate_results.py` z rzeczywistych obliczeń na danych "
      f"Inside Airbnb (wydanie **{meta.release}**, pobrano {meta.downloaded_at}). Nie edytuj ręcznie.\n")
    w(f"Parametry: permutacje = {settings.permutations}, ziarno = {settings.seed}, próg wiarygodności dzielnicy = "
      f"{settings.min_listings} ofert z ceną, filtry: brak.\n")

    w("## 1. Dane i jakość\n")
    w(md_table(ds.cleaning.as_table()))
    w(f"\nPłotki Tukeya (k = 1,5, ln ceny): {ds.cleaning.outlier_lower_bound:.1f} – {ds.cleaning.outlier_upper_bound:.1f} $. "
      f"Symbol waluty w źródle: {', '.join(ds.cleaning.currency_symbols)}.")
    w(f"\nGeometrie dzielnic: {meta.neighbourhood_features} obiektów w pliku → {len(ds.polygons)} dzielnic po scaleniu powtórzonych nazw. "
      f"Zgodność Spatial Join z etykietą źródłową `neighbourhood_cleansed`: **{ds.join_agreement * 100:.2f}%**; "
      f"oferty poza granicami: {int(ds.points['outside_boundaries'].sum())}.\n")

    w("## 2. Rynek (wszystkie oferty z ceną)\n")
    price = ds.points["price"].dropna()
    market = [
        ("Oferty w źródle", f"{ds.listings_total:,}".replace(",", " ")), ("Oferty z ceną", f"{len(price):,}".replace(",", " ")),
        ("Średnia cena [$]", f"{price.mean():.2f}"), ("Mediana ceny [$]", f"{price.median():.2f}"),
        ("Odchylenie std. [$]", f"{price.std():.2f}"), ("Min [$]", f"{price.min():.2f}"), ("Maks [$]", f"{price.max():.2f}"),
        ("Dzielnice z ofertami", str(int((d["n_listings"] > 0).sum()))), ("Dzielnice wiarygodne (≥ próg)", str(len(reliable))),
    ]
    w(md_table(pd.DataFrame(market, columns=["Miara", "Wartość"])))
    w("\nUdział typów pokoju:\n")
    w(md_table(ds.points["room_type"].value_counts(normalize=True).mul(100).round(1).rename_axis("Typ pokoju").reset_index(name="%")))

    w("\n## 3. Dzielnice — rankingi (dzielnice wiarygodne)\n")
    cols = ["neighbourhood", "n_listings", "n_priced", "median_price", "mean_price", "std_price", "density_km2"]
    ren = {"neighbourhood": "Dzielnica", "n_listings": "Oferty", "n_priced": "Z ceną", "median_price": "Mediana $", "mean_price": "Średnia $",
           "std_price": "Odch. std $", "density_km2": "Oferty/km²"}
    w("**10 najdroższych (mediana ceny):**\n")
    w(md_table(reliable.sort_values("median_price", ascending=False).head(10)[cols].rename(columns=ren), "{:,.1f}"))
    w("\n**10 najtańszych (mediana ceny):**\n")
    w(md_table(reliable.sort_values("median_price").head(10)[cols].rename(columns=ren), "{:,.1f}"))
    w("\n**10 dzielnic o największym zagęszczeniu ofert:**\n")
    w(md_table(d.sort_values("density_km2", ascending=False).head(10)[cols].rename(columns=ren), "{:,.1f}"))
    w("\n**10 dzielnic o największej liczbie ofert:**\n")
    w(md_table(d.sort_values("n_listings", ascending=False).head(10)[cols].rename(columns=ren), "{:,.1f}"))

    w("\n## 4. Autokorelacja przestrzenna\n")
    w(f"Zmienna: **mediana ceny za noc**, wagi: Queen (standaryzacja wierszowa).\n")
    w(md_table(m.summary_table().astype(str)))
    w(f"\n**Interpretacja:** {m.interpretation}\n")
    w(f"Wyłączone z analizy: {len(m.excluded)} dzielnic (za mało ofert z ceną lub brak sąsiadów).\n")
    w("### Klastry LISA (pseudo-p < 0,05)\n")
    w(md_table(autocorr.cluster_counts(d)[["Klasa", "Liczba dzielnic"]]))
    sig = d[d["lisa_code"].isin(["HH", "LL", "HL", "LH"])].sort_values(["lisa_code", "lisa_p"])
    w("\n" + md_table(sig[["neighbourhood", "lisa_code", "median_price", "n_priced", "lisa_I", "lisa_p", "lisa_sig_fdr"]].rename(columns={
        "neighbourhood": "Dzielnica", "lisa_code": "Klaster", "median_price": "Mediana $", "n_priced": "Z ceną", "lisa_I": "Local I",
        "lisa_p": "pseudo-p", "lisa_sig_fdr": "Istotny po FDR"}), "{:,.3f}"))

    w("\n### Odporność wyniku: inne macierze wag i zmienne\n")
    rows = []
    for var in ("median_price", "mean_price", "median_price_pp", "density_km2", "share_entire_home", "mean_rating", "mean_occupancy"):
        for method in autocorr.WEIGHT_METHODS:
            r = analysis.run_analysis(ds, settings, Filters(), variable=var, method=method)
            if r.moran:
                rows.append((METRICS[var].label, method, r.moran.n_obs, r.moran.I, r.moran.p_sim))
    w(md_table(pd.DataFrame(rows, columns=["Zmienna", "Wagi", "n", "Moran's I", "pseudo-p"]), "{:,.3f}"))

    w("\n### Wpływ ofert odstających\n")
    impact = analysis.outlier_impact(ds, settings, Filters())
    w(md_table(impact.table, "{:,.3f}"))

    w("\n## 5. Wpływ lokalizacji na cenę\n")
    eff = analysis.location_effect(res.points)
    w(md_table(pd.DataFrame({
        "Model OLS dla ln(ceny)": ["Cechy oferty (goście + typ pokoju)", "Sama dzielnica", "Cechy oferty + odległość od centrum", "Cechy oferty + dzielnica"],
        "Skorygowane R²": [eff.r2_offer, eff.r2_district, eff.r2_offer_distance, eff.r2_offer_district]}), "{:,.3f}"))
    w(f"\nSpearman (cena vs odległość od centrum): ρ = {eff.spearman_distance:.3f}, n = {eff.n}.\n")

    w("## 6. Rysunki\n")
    w("![Mediana ceny](img/mediana_ceny.png)\n\n![Zagęszczenie](img/zageszczenie.png)\n\n![LISA](img/lisa.png)\n")

    choropleth_png(d, "median_price", "Mediana ceny za noc [$] (kwantyle)", img / "mediana_ceny.png")
    choropleth_png(d, "density_km2", "Zagęszczenie ofert [oferty/km²] (kwantyle)", img / "zageszczenie.png", "Blues")
    lisa_png(d, img / "lisa.png")
    out = ROOT / "docs" / "WYNIKI.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Zapisano", out)


if __name__ == "__main__":
    main()
