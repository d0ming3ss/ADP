"""Warstwa interfejsu Streamlit: filtry, sekcje aplikacji i buforowanie obliczeń."""

from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from georent import analysis, autocorr, data_source, export, visualization as viz
from georent.analysis import AnalysisResult, Dataset, Filters
from georent.config import CITIES, PROJECT_ROOT, Settings, get_settings
from georent.spatial import METRICS, OUTSIDE_LABEL

logger = logging.getLogger(__name__)

SECTIONS = [
    "Dashboard", "Mapa ofert", "Analiza dzielnic", "Analiza cen", "Analiza przestrzenna", "Dane i źródła", "Metodologia",
]
# Zmienne, dla których ma sens liczyć autokorelację przestrzenną.
MORAN_VARIABLES = ["median_price", "mean_price", "median_price_pp", "density_km2", "share_entire_home", "mean_rating", "mean_occupancy"]


# --- Dostęp do danych (buforowany) ------------------------------------------------------------------------

@st.cache_resource(show_spinner="Wczytywanie i czyszczenie danych, Spatial Join…")
def load_dataset(data_dir: str, city: str, release: str) -> Dataset:
    settings = replace(get_settings(), data_dir=Path(data_dir), city=city)
    meta = next(m for m in data_source.list_local_releases(settings, city) if m.release == release)
    return analysis.prepare_dataset(meta, settings.city_spec())


@st.cache_data(show_spinner="Obliczanie statystyk dzielnic i autokorelacji przestrzennej…", max_entries=64)
def compute(_dataset: Dataset, dataset_key: str, filters: Filters, variable: str, method: str,
            permutations: int, seed: int, min_listings: int) -> AnalysisResult:
    settings = replace(get_settings(), city=_dataset.city.slug, permutations=permutations, seed=seed,
                       min_listings=min_listings)
    return analysis.run_analysis(_dataset, settings, filters, variable=variable, method=method)


@st.cache_data(show_spinner="Porównanie wariantów…", max_entries=32)
def compute_outlier_impact(_dataset: Dataset, dataset_key: str, filters: Filters, variable: str, method: str,
                           permutations: int, seed: int, min_listings: int) -> analysis.OutlierImpact:
    settings = replace(get_settings(), city=_dataset.city.slug, permutations=permutations, seed=seed,
                       min_listings=min_listings)
    return analysis.outlier_impact(_dataset, settings, filters, variable, method)


def _map(fmap, height: int = 560) -> None:
    st_folium(fmap, height=height, use_container_width=True, returned_objects=[])


def _money(x: float | None, digits: int = 0) -> str:
    return "—" if x is None or pd.isna(x) else f"{x:,.{digits}f} $".replace(",", " ")


# --- Filtry --------------------------------------------------------------------------------------------------

def _named_metric(col, label: str, name: str, value: str) -> None:
    """Wskaźnik z długą nazwą (st.metric ucina długie wartości)."""
    col.caption(label)
    col.markdown(f"**{name}**  \n{value}")


def sidebar_filters(ds: Dataset) -> Filters:
    pts = ds.points
    st.sidebar.header("Filtry ofert")
    groups = sorted(pts["neighbourhood_group_cleansed"].dropna().unique())
    sel_groups = st.sidebar.multiselect("Grupa dzielnic (dystrykt)", groups, placeholder="Wszystkie")
    scope = pts if not sel_groups else pts[pts["neighbourhood_group_cleansed"].isin(sel_groups)]
    hoods = sorted(h for h in scope["neighbourhood"].unique() if h != OUTSIDE_LABEL)
    sel_hoods = st.sidebar.multiselect("Dzielnica (barri)", hoods, placeholder="Wszystkie")

    priced = pts["price"].dropna()
    cap = int(np.ceil(priced.quantile(0.995) / 50) * 50) if len(priced) else 1000
    price_lo, price_hi = st.sidebar.slider("Cena za noc [$]", 0, cap, (0, cap), step=10)
    no_upper = st.sidebar.checkbox(f"Bez górnego limitu ceny (powyżej {cap} $)", value=True)
    price_range = (float(price_lo), float("inf") if no_upper and price_hi == cap else float(price_hi))
    if price_range == (0.0, float("inf")):
        price_range = None
    only_priced = st.sidebar.checkbox("Tylko oferty z podaną ceną", value=False,
                                      help="Oferty bez ceny są liczone w liczbie ofert i zagęszczeniu, ale nie w statystykach cen.")

    rooms = st.sidebar.multiselect("Typ pokoju", sorted(pts["room_type"].dropna().unique()), placeholder="Wszystkie")
    props = st.sidebar.multiselect("Typ nieruchomości", pts["property_type"].value_counts().index.tolist(), placeholder="Wszystkie")
    acc_max = int(pts["accommodates"].max()) if pts["accommodates"].notna().any() else 16
    acc = st.sidebar.slider("Liczba osób (accommodates)", 1, acc_max, (1, acc_max))
    acc_range = None if acc == (1, acc_max) else (int(acc[0]), int(acc[1]))

    st.sidebar.subheader("Dostępność i jakość danych")
    only_avail = st.sidebar.checkbox("Tylko oferty z dostępnymi terminami (has_availability)", value=False)
    min_av = st.sidebar.slider("Min. dostępnych dni w roku (availability_365)", 0, 365, 0)
    min_rev = st.sidebar.slider("Min. liczba opinii", 0, 100, 0)
    excl = st.sidebar.checkbox("Wyklucz oferty o cenach odstających", value=False,
                               help="Reguła IQR (Tukey, k = 1,5) na logarytmie ceny. Wpływ tej filtracji pokazuje sekcja „Analiza przestrzenna”.")
    return Filters(
        neighbourhood_groups=tuple(sel_groups), neighbourhoods=tuple(sel_hoods), price_range=price_range,
        room_types=tuple(rooms), property_types=tuple(props), accommodates_range=acc_range,
        only_available=only_avail, min_availability_365=min_av or None, min_reviews=min_rev,
        exclude_outliers=excl, require_price=only_priced,
    )


def sidebar_analysis_params() -> tuple[str, str]:
    with st.sidebar.expander("Parametry analizy przestrzennej", expanded=False):
        variable = st.selectbox("Zmienna do autokorelacji", MORAN_VARIABLES, index=0,
                                format_func=lambda k: METRICS[k].label)
        method = st.selectbox("Macierz wag przestrzennych", list(autocorr.WEIGHT_METHODS), index=0,
                              format_func=lambda k: autocorr.WEIGHT_METHODS[k])
    return variable, method


# --- Sekcje ---------------------------------------------------------------------------------------------------

def page_dashboard(ds: Dataset, res: AnalysisResult, settings: Settings) -> None:
    st.subheader("Dashboard — podsumowanie rynku")
    pts, dist = res.points, res.districts
    priced = pts["price"].dropna()
    c = st.columns(4)
    c[0].metric("Analizowane oferty", f"{len(pts):,}".replace(",", " "), help=f"W źródle: {ds.listings_total:,} ofert.".replace(",", " "))
    c[1].metric("Dzielnice z ofertami", f"{int((dist['n_listings'] > 0).sum())} / {len(dist)}")
    c[2].metric("Średnia cena za noc", _money(priced.mean()))
    c[3].metric("Mediana ceny za noc", _money(priced.median()))

    reliable = dist[dist["price_reliable"]]
    c = st.columns(4)
    if len(reliable):
        top = reliable.sort_values("median_price", ascending=False).iloc[0]
        low = reliable.sort_values("median_price").iloc[0]
        _named_metric(c[0], "Najdroższa dzielnica (mediana ceny)", top["neighbourhood"], _money(top["median_price"]))
        _named_metric(c[1], "Najtańsza dzielnica (mediana ceny)", low["neighbourhood"], _money(low["median_price"]))
    else:
        _named_metric(c[0], "Najdroższa dzielnica (mediana ceny)", "—", "")
        _named_metric(c[1], "Najtańsza dzielnica (mediana ceny)", "—", "")
    if dist["n_listings"].sum() > 0:
        busiest = dist.sort_values("n_listings", ascending=False).iloc[0]
        _named_metric(c[2], "Dzielnica z największą liczbą ofert", busiest["neighbourhood"], f"{int(busiest['n_listings'])} ofert")
    if res.moran:
        m = res.moran
        c[3].metric("Global Moran's I", f"{m.I:.3f}", f"pseudo-p = {m.p_sim:.3f}", delta_color="off", delta_arrow="off",
                    help=f"Zmienna: {METRICS[m.variable].label}; wagi: {m.method}; n = {m.n_obs}.")
    else:
        c[3].metric("Global Moran's I", "—", "brak danych", delta_color="off", delta_arrow="off")
    st.caption(
        f"Dzielnice o mniej niż {settings.min_listings} ofertach z ceną są pomijane w rankingach cen i w autokorelacji "
        "(niewiarygodne statystyki). Źródło danych: Inside Airbnb, wydanie "
        f"{ds.meta.release} — {ds.city.label}."
    )
    if res.moran:
        st.info(res.moran.interpretation)
    elif res.moran_error:
        st.warning(res.moran_error)

    left, right = st.columns(2)
    left.plotly_chart(viz.district_bar(dist, "median_price", 12, settings.min_listings), width="stretch")
    right.plotly_chart(viz.district_bar(dist, "density_km2", 12), width="stretch")


def page_map(ds: Dataset, res: AnalysisResult) -> None:
    st.subheader("Mapa ofert")
    mode = st.radio("Sposób prezentacji", ["points", "cluster", "heatmap"], horizontal=True,
                    format_func={"points": "Punkty (kolor = cena)", "cluster": "Klastry (wszystkie oferty)",
                                 "heatmap": "Mapa gęstości (heatmap)"}.get)
    max_points = 3000
    if mode == "points":
        max_points = st.slider("Maks. liczba punktów (losowa próbka)", 500, 8000, 3000, 500,
                               help="Ogranicza liczbę markerów, aby mapa pozostała płynna.")
    if res.points.empty:
        st.warning("Brak ofert spełniających wybrane filtry.")
        return
    fmap, drawn = viz.offers_map(res.points, ds.polygons, mode, max_points)
    if mode == "points" and drawn < len(res.points):
        st.caption(f"Narysowano losową próbkę {drawn:,} z {len(res.points):,} ofert.".replace(",", " "))
    else:
        st.caption(f"Pokazano {drawn:,} ofert.".replace(",", " "))
    _map(fmap, 620)
    out = viz.label_outside(res.points)
    if out:
        st.warning(f"{out} ofert leży poza granicami dzielnic (nie wchodzą do statystyk dzielnicowych).")


def page_districts(ds: Dataset, res: AnalysisResult, settings: Settings) -> None:
    st.subheader("Analiza dzielnic")
    st.caption("Kartogramy używają klas kwantylowych (po 5 klas). Liczby bezwzględne zależą od powierzchni dzielnicy — "
               "porównując intensywność, użyj zagęszczenia (oferty/km²).")
    keys = list(METRICS)
    c1, c2 = st.columns(2)
    m1 = c1.selectbox("Kartogram A", keys, index=keys.index("n_listings"), format_func=lambda k: f"{METRICS[k].label} [{METRICS[k].unit}]")
    m2 = c2.selectbox("Kartogram B", keys, index=keys.index("median_price"), format_func=lambda k: f"{METRICS[k].label} [{METRICS[k].unit}]")
    for col, m in ((c1, m1), (c2, m2)):
        with col:
            _map(viz.choropleth_map(res.districts, m), 480)
            st.caption(METRICS[m].description)
    st.caption(f"Dzielnice o mniej niż {settings.min_listings} ofertach z ceną mają mało wiarygodne statystyki cenowe "
               "(kolumna „Wiarygodne”).")

    st.markdown("#### Tabela statystyk dzielnic")
    table = pd.DataFrame(res.districts.drop(columns="geometry"))[[
        "neighbourhood", "neighbourhood_group", "n_listings", "n_priced", "mean_price", "median_price", "min_price",
        "max_price", "std_price", "area_km2", "density_km2", "price_reliable",
    ]].rename(columns={
        "neighbourhood": "Dzielnica", "neighbourhood_group": "Dystrykt", "n_listings": "Oferty", "n_priced": "Z ceną",
        "mean_price": "Średnia [$]", "median_price": "Mediana [$]", "min_price": "Min [$]", "max_price": "Maks [$]",
        "std_price": "Odch. std [$]", "area_km2": "Pow. [km²]", "density_km2": "Oferty/km²", "price_reliable": "Wiarygodne",
    }).sort_values("Oferty", ascending=False)
    st.dataframe(table, hide_index=True, width="stretch", column_config={
        c: st.column_config.NumberColumn(format="%.1f") for c in table.columns
        if c not in ("Dzielnica", "Dystrykt", "Oferty", "Z ceną", "Wiarygodne")
    })
    st.download_button("Pobierz tabelę (CSV)", table.to_csv(index=False).encode("utf-8"),
                       file_name=f"dzielnice_{ds.city.slug}.csv", mime="text/csv")
    with st.expander("Dzielnice bez ofert / poza analizą"):
        empty = table[table["Oferty"] == 0]["Dzielnica"].tolist()
        st.write(f"Bez ofert po zastosowaniu filtrów: **{len(empty)}** — " + (", ".join(empty) or "brak"))


def page_prices(ds: Dataset, res: AnalysisResult) -> None:
    st.subheader("Analiza cen")
    pts = res.points
    if pts["price"].notna().sum() < 5:
        st.warning("Zbyt mało ofert z ceną dla wybranych filtrów.")
        return
    c1, c2 = st.columns(2)
    log_x = c1.checkbox("Skala logarytmiczna osi cen", value=True)
    c1.plotly_chart(viz.price_histogram(pts, log_x), width="stretch")
    c2.plotly_chart(viz.price_box(pts, "room_type", "Cena wg typu pokoju", order_by_median=True), width="stretch")
    st.plotly_chart(viz.price_box(pts, "neighbourhood_group_cleansed", "Cena wg dystryktu (mediana malejąco)"), width="stretch")
    c3, c4 = st.columns(2)
    c3.plotly_chart(viz.price_by_accommodates(pts), width="stretch")
    c4.plotly_chart(viz.price_vs_distance(pts), width="stretch")

    st.markdown("#### Jak lokalizacja wpływa na cenę?")
    eff = analysis.location_effect(pts)
    if eff is None:
        st.info("Za mało danych (min. 50 ofert w co najmniej 2 dzielnicach), aby oszacować wpływ lokalizacji.")
        return
    table = pd.DataFrame({
        "Model OLS dla ln(ceny)": ["Cechy oferty (liczba gości + typ pokoju)", "Sama dzielnica (zmienne zero-jedynkowe)",
                                   "Cechy oferty + odległość od centrum", "Cechy oferty + dzielnica"],
        "Skorygowane R²": [eff.r2_offer, eff.r2_district, eff.r2_offer_distance, eff.r2_offer_district],
    })
    st.dataframe(table, hide_index=True, width="stretch",
                 column_config={"Skorygowane R²": st.column_config.NumberColumn(format="%.3f")})
    gain = eff.r2_offer_district - eff.r2_offer
    st.write(
        f"Spearman (cena vs odległość od centrum): **ρ = {eff.spearman_distance:.3f}** (n = {eff.n:,}). ".replace(",", " ")
        + f"Sama dzielnica tłumaczy ok. **{eff.r2_district * 100:.1f}%** zmienności ln(ceny); po uwzględnieniu cech oferty "
        f"dzielnica dodaje **{gain * 100:.1f} p.p.** wyjaśnionej wariancji. To związek opisowy, a nie przyczynowy."
    )


def page_spatial(ds: Dataset, res: AnalysisResult, filters: Filters, key: str, variable: str, method: str,
                 settings: Settings) -> None:
    st.subheader("Analiza przestrzenna — autokorelacja cen")
    info = METRICS[variable]
    if res.moran is None:
        st.warning(res.moran_error or "Nie udało się obliczyć autokorelacji.")
        _map(viz.choropleth_map(res.districts, variable), 480)
        return
    m = res.moran
    c = st.columns(4)
    c[0].metric("Global Moran's I", f"{m.I:.3f}", f"E[I] = {m.expected_I:.3f}", delta_color="off", delta_arrow="off")
    c[1].metric("pseudo-p dwustronne", f"{m.p_sim:.4f}", f"{m.permutations} permutacji", delta_color="off", delta_arrow="off")
    c[2].metric("z (symulacja)", f"{m.z_sim:.2f}")
    c[3].metric("Jednostki w analizie", m.n_obs, f"śr. sąsiadów: {m.mean_neighbors:.1f}", delta_color="off", delta_arrow="off")
    (st.success if m.significant and m.I > 0 else st.warning if m.significant else st.info)(m.interpretation)
    st.caption(f"Zmienna: {info.label}. Wagi: {autocorr.WEIGHT_METHODS[method]}, standaryzacja wierszowa. "
               f"Ziarno losowe: {m.seed}. Przy {m.permutations} permutacjach najmniejsze osiągalne dwustronne pseudo-p to "
               f"{2 / (m.permutations + 1):.3f}.")

    left, right = st.columns([3, 2])
    with left:
        st.markdown("#### Mapa klastrów LISA")
        _map(viz.lisa_map(m.districts, info.label.lower()), 560)
    with right:
        st.markdown("#### Wykres rozrzutu Morana")
        st.plotly_chart(viz.moran_scatterplot(m), width="stretch")
        st.dataframe(autocorr.cluster_counts(m.districts)[["Klasa", "Liczba dzielnic"]], hide_index=True, width="stretch")
    st.caption("Istotność lokalna: dwustronne pseudo-p < 0,05 z testu permutacyjnego. Przy wielu jednostkach rośnie ryzyko fałszywych "
               "alarmów — kolumna „lisa_sig_fdr” w eksporcie wskazuje klastry istotne po korekcie FDR.")

    sig = pd.DataFrame(m.districts.drop(columns="geometry"))
    sig = sig[sig["lisa_code"].isin(["HH", "LL", "HL", "LH"])][
        ["neighbourhood", "lisa_cluster", "median_price", "n_priced", "lisa_I", "lisa_p", "lisa_sig_fdr"]
    ].rename(columns={"neighbourhood": "Dzielnica", "lisa_cluster": "Klaster", "median_price": "Mediana [$]",
                      "n_priced": "Z ceną", "lisa_I": "Local I", "lisa_p": "pseudo-p", "lisa_sig_fdr": "Istotny po FDR"})
    st.markdown("#### Istotne klastry lokalne")
    st.dataframe(sig.sort_values("pseudo-p"), hide_index=True, width="stretch")

    with st.expander(f"Dzielnice wyłączone z analizy ({len(m.excluded)})"):
        st.write("Dzielnice bez wiarygodnych statystyk lub bez sąsiadów nie mają określonego wskaźnika Morana.")
        st.dataframe(pd.DataFrame({"Dzielnica": list(m.excluded), "Powód": list(m.excluded.values())}),
                     hide_index=True, width="stretch")

    with st.expander("Odporność wyniku: porównanie macierzy wag i wpływ ofert odstających", expanded=False):
        rows = []
        for meth in autocorr.WEIGHT_METHODS:
            r = compute(ds, key, filters, variable, meth, settings.permutations, settings.seed, settings.min_listings)
            if r.moran:
                rows.append({"Wagi": autocorr.WEIGHT_METHODS[meth], "Moran's I": r.moran.I, "pseudo-p": r.moran.p_sim, "n": r.moran.n_obs})
        st.markdown("**Wariant macierzy wag**")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        impact = compute_outlier_impact(ds, key, filters, variable, method, settings.permutations, settings.seed, settings.min_listings)
        st.markdown("**Oferty odstające cenowo** (reguła IQR na ln ceny): wyniki z i bez filtracji, przy pozostałych filtrach bez zmian")
        st.dataframe(impact.table, hide_index=True, width="stretch")


def page_data(ds: Dataset, settings: Settings, res: AnalysisResult) -> None:
    st.subheader("Dane i źródła")
    meta = ds.meta
    st.markdown(
        f"**Źródło:** [Inside Airbnb — {meta.label}]({meta.source_url})  \n"
        f"**Wydanie (scrape):** {meta.release}  \n**Pobrano lokalnie:** {meta.downloaded_at}  \n"
        f"**Licencja:** {meta.license}  \n**Lokalizacja plików:** `{meta.directory}`"
    )
    files = pd.DataFrame([{"Plik": n, "Rozmiar [KB]": f["bytes"] / 1024, "SHA-256": f["sha256"][:16] + "…", "URL": f["url"]}
                          for n, f in meta.files.items()])
    st.dataframe(files, hide_index=True, width="stretch",
                 column_config={"Rozmiar [KB]": st.column_config.NumberColumn(format="%.0f")})

    st.markdown("#### Jakość danych i czyszczenie")
    st.dataframe(ds.cleaning.as_table(), hide_index=True, width="stretch")
    rep = ds.cleaning
    if rep.outlier_upper_bound:
        st.caption(f"Płotki Tukeya (k = 1,5) na ln(ceny): oferty tańsze niż {rep.outlier_lower_bound:,.0f} $ lub droższe niż "
                   f"{rep.outlier_upper_bound:,.0f} $ są oznaczone jako odstające (nie usuwane automatycznie). "
                   f"Symbol waluty w źródle: {', '.join(rep.currency_symbols) or 'brak'}.")
    if ds.join_agreement is not None:
        st.caption(f"Kontrola Spatial Join: {ds.join_agreement * 100:.2f}% ofert otrzymało tę samą dzielnicę co etykieta "
                   f"`neighbourhood_cleansed` w pliku źródłowym.")
    miss = pd.DataFrame({"Kolumna": list(rep.missing_by_column), "Braki": list(rep.missing_by_column.values())})
    with st.expander("Braki wartości wg kolumn"):
        st.dataframe(miss.sort_values("Braki", ascending=False), hide_index=True, width="stretch")

    st.markdown("#### Aktualizacja danych")
    c1, c2 = st.columns(2)
    if c1.button("Sprawdź i pobierz nowsze wydanie"):
        with st.spinner("Sprawdzanie indeksu Inside Airbnb…"):
            remote, local = data_source.check_for_update(settings, ds.city.slug)
            if remote is None:
                st.error("Nie udało się odczytać indeksu Inside Airbnb (brak połączenia?).")
            elif remote == local:
                st.success(f"Dane są aktualne (wydanie {local}).")
            else:
                try:
                    new = data_source.download_dataset(settings, ds.city.slug, release=remote)
                    st.success(f"Pobrano wydanie {new.release}. Odśwież stronę, aby użyć nowych danych.")
                    st.cache_resource.clear(); st.cache_data.clear()
                except data_source.DataSourceError as exc:
                    st.error(f"Nie udało się pobrać danych: {exc}")
    if c2.button("Pobierz ponownie bieżące wydanie (wymuś)"):
        try:
            with st.spinner("Pobieranie…"):
                data_source.download_dataset(settings, ds.city.slug, release=meta.release, force=True)
            st.cache_resource.clear(); st.cache_data.clear()
            st.success("Dane pobrane ponownie. Odśwież stronę.")
        except data_source.DataSourceError as exc:
            st.error(f"Nie udało się pobrać danych: {exc}")

    st.markdown("#### Eksport do QGIS")
    st.caption("Eksport obejmuje bieżące filtry i parametry analizy. Warstwy: oferty_punkty, dzielnice_granice, "
               "dzielnice_statystyki (z Moran's I i LISA), lisa_klastry — w EPSG:4326.")
    if st.button("Wygeneruj eksport (GPKG, GeoJSON, CSV)"):
        out_dir = settings.export_dir / ds.city.slug
        try:
            result = export.export_all(res, ds, out_dir)
        except OSError as exc:
            st.error(f"Nie udało się zapisać eksportu: {exc}")
        else:
            st.session_state["export_dir"] = str(out_dir)
            st.success(f"Zapisano {len(result.files)} plików w `{out_dir}`. Warstwy: " +
                       ", ".join(f"{k} ({v})" for k, v in result.layers.items()))
    export_dir = Path(st.session_state.get("export_dir", settings.export_dir / ds.city.slug))
    if (export_dir / export.GPKG_NAME).exists():
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(export_dir.rglob("*")):
                if p.is_file():
                    zf.write(p, p.relative_to(export_dir))
        st.download_button("Pobierz cały eksport (ZIP)", buf.getvalue(), file_name=f"georent_{ds.city.slug}_eksport.zip",
                           mime="application/zip")


def page_methodology(settings: Settings) -> None:
    doc = PROJECT_ROOT / "docs" / "METODOLOGIA.md"
    if doc.exists():
        st.markdown(doc.read_text(encoding="utf-8"))
    else:
        st.warning("Brak pliku docs/METODOLOGIA.md.")
    st.markdown("#### Parametry bieżącej konfiguracji")
    st.table(pd.DataFrame([
        ("Miasto", settings.city), ("Liczba permutacji", settings.permutations), ("Ziarno losowe", settings.seed),
        ("Minimalna liczba ofert z ceną w dzielnicy", settings.min_listings),
    ], columns=["Parametr", "Wartość"]).astype(str))


# --- Punkt wejścia ------------------------------------------------------------------------------------------------

def download_panel(settings: Settings, city: str) -> None:
    spec = CITIES[city]
    st.info(f"Brak lokalnych danych dla miasta **{spec.label}**. Dane Inside Airbnb (CC BY 4.0) pobierane są jednorazowo "
            f"i zapisywane w `{settings.raw_dir}`.")
    if st.button(f"Pobierz dane: {spec.label}", type="primary"):
        try:
            with st.spinner("Pobieranie i walidacja danych (kilka MB)…"):
                data_source.download_dataset(settings, city)
        except data_source.DataSourceError as exc:
            st.error(f"Nie udało się pobrać danych: {exc}")
        else:
            st.rerun()


def run_app() -> None:
    st.set_page_config(page_title="GeoRent-AI", page_icon="🏙️", layout="wide")
    settings = get_settings()
    st.title("🏙️ GeoRent-AI — przestrzenna analiza rynku najmu krótkoterminowego")

    available = data_source.local_cities(settings)
    options = sorted(set(available) | {settings.city})
    default_idx = options.index(settings.city)
    city = st.sidebar.selectbox("Miasto", options, index=default_idx, format_func=lambda s: CITIES[s].label)
    settings = replace(settings, city=city)

    local = data_source.get_local_dataset(settings, city)
    if local is None:
        download_panel(settings, city)
        st.stop()

    ds = load_dataset(str(settings.data_dir), city, local.release)
    section = st.sidebar.radio("Sekcja", SECTIONS, label_visibility="collapsed")

    if section == "Metodologia":
        page_methodology(settings)
        return
    filters = sidebar_filters(ds)
    variable, method = sidebar_analysis_params()
    key = f"{city}:{local.release}"
    res = compute(ds, key, filters, variable, method, settings.permutations, settings.seed, settings.min_listings)

    if section == "Dane i źródła":
        page_data(ds, settings, res)
        return
    if res.points.empty:
        st.warning("Żadna oferta nie spełnia wybranych filtrów — poluzuj kryteria w panelu bocznym.")
        return
    st.caption(f"{ds.city.label} · dane Inside Airbnb z {local.release} · oferty po filtrach: "
               f"{len(res.points):,} z {ds.listings_total:,}".replace(",", " "))
    if section == "Dashboard":
        page_dashboard(ds, res, settings)
    elif section == "Mapa ofert":
        page_map(ds, res)
    elif section == "Analiza dzielnic":
        page_districts(ds, res, settings)
    elif section == "Analiza cen":
        page_prices(ds, res)
    elif section == "Analiza przestrzenna":
        page_spatial(ds, res, filters, key, variable, method, settings)
