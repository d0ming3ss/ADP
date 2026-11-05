"""Wizualizacje: mapy Folium (oferty, kartogramy, LISA) oraz wykresy Plotly."""

from __future__ import annotations

import json
import logging

import folium
import geopandas as gpd
import mapclassify
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from folium.plugins import FastMarkerCluster, Fullscreen, HeatMap

from georent.autocorr import CLUSTER_COLORS, CLUSTER_LABELS, MoranResult
from georent.spatial import METRICS, OUTSIDE_LABEL

logger = logging.getLogger(__name__)

PRIMARY = "#2563eb"
SEQUENTIAL = ["#ffffcc", "#fed976", "#fd8d3c", "#e31a1c", "#800026"]  # YlOrRd, 5 klas
NO_DATA_COLOR = "#d9d9d9"
PRICE_BINS = [(0, 50, "#1a9850"), (50, 100, "#91cf60"), (100, 200, "#fee08b"), (200, 400, "#fc8d59"),
              (400, np.inf, "#d73027")]


# --- Mapy -------------------------------------------------------------------------------------------

def _base_map(bounds: tuple[float, float, float, float]) -> folium.Map:
    minx, miny, maxx, maxy = bounds
    fmap = folium.Map(tiles="OpenStreetMap", control_scale=True, prefer_canvas=True)
    fmap.fit_bounds([[miny, minx], [maxy, maxx]])
    Fullscreen().add_to(fmap)
    return fmap


def _price_color(price: float) -> str:
    for lo, hi, color in PRICE_BINS:
        if lo <= price < hi:
            return color
    return PRICE_BINS[-1][2]


def _legend_html(title: str, items: list[tuple[str, str]]) -> folium.Element:
    rows = "".join(
        f'<div><span style="display:inline-block;width:14px;height:14px;background:{c};'
        f'border:1px solid #888;margin-right:6px;vertical-align:middle"></span>{label}</div>'
        for c, label in items
    )
    html = (
        '<div style="position:fixed;bottom:28px;left:12px;z-index:9999;background:rgba(255,255,255,.92);'
        'padding:8px 10px;border:1px solid #999;border-radius:4px;font:12px/1.5 sans-serif;color:#222">'
        f"<b>{title}</b>{rows}</div>"
    )
    return folium.Element(html)


def _boundaries_layer(polygons: gpd.GeoDataFrame, name: str = "Granice dzielnic") -> folium.GeoJson:
    return folium.GeoJson(
        polygons[["neighbourhood", "geometry"]].to_json(),
        name=name,
        style_function=lambda _: {"color": "#555", "weight": 1, "fillOpacity": 0},
        tooltip=folium.GeoJsonTooltip(fields=["neighbourhood"], aliases=["Dzielnica:"]),
    )


def offers_map(
    points: gpd.GeoDataFrame, polygons: gpd.GeoDataFrame, mode: str = "points", max_points: int = 3000, seed: int = 42
) -> tuple[folium.Map, int]:
    """Mapa ofert. ``mode``: ``points`` (kolor = cena), ``cluster`` lub ``heatmap``.

    Zwraca (mapa, liczba narysowanych ofert). Tryb punktowy rysuje losową próbkę (``seed``),
    aby przeglądarka pozostała responsywna; ``cluster`` i ``heatmap`` używają wszystkich ofert.
    """
    fmap = _base_map(tuple(polygons.total_bounds))
    _boundaries_layer(polygons).add_to(fmap)
    drawn = 0
    if not points.empty:
        if mode == "heatmap":
            HeatMap(points[["latitude", "longitude"]].to_numpy().tolist(), radius=9, blur=12, min_opacity=0.25,
                    name="Gęstość ofert").add_to(fmap)
            drawn = len(points)
        elif mode == "cluster":
            FastMarkerCluster(points[["latitude", "longitude"]].to_numpy().tolist(), name="Klastry ofert").add_to(fmap)
            drawn = len(points)
        else:
            sample = points.sample(n=min(max_points, len(points)), random_state=seed)
            group = folium.FeatureGroup(name="Oferty (kolor = cena za noc)")
            for r in sample.itertuples(index=False):
                price = "brak" if pd.isna(r.price) else f"{r.price:,.0f} $"
                popup = (
                    f"<b>{r.name or 'Oferta ' + str(r.id)}</b><br>Dzielnica: {r.neighbourhood}<br>"
                    f"Cena: {price}<br>Typ: {r.room_type}<br>Gości: {int(r.accommodates) if pd.notna(r.accommodates) else '–'}"
                )
                folium.CircleMarker(
                    (r.latitude, r.longitude), radius=3, weight=0.5, color="#333", fill=True, fill_opacity=0.85,
                    fill_color=_price_color(r.price) if pd.notna(r.price) else "#999",
                    tooltip=price, popup=folium.Popup(popup, max_width=260),
                ).add_to(group)
            group.add_to(fmap)
            drawn = len(sample)
            _legend_html("Cena za noc ($)", [(c, f"{lo:.0f}–{hi:.0f}" if np.isfinite(hi) else f"> {lo:.0f}")
                                              for lo, hi, c in PRICE_BINS]).add_to(fmap)
    folium.LayerControl(collapsed=True).add_to(fmap)
    return fmap, drawn


def _geojson_for(districts: gpd.GeoDataFrame, columns: list[str]) -> str:
    cols = list(dict.fromkeys(c for c in columns if c in districts.columns))  # bez duplikatów
    gdf = districts[cols + ["geometry"]].copy()
    # JSON nie zna NaN — zamiana na null; wartości liczbowe zaokrąglone dla czytelności tooltipów
    for col in gdf.select_dtypes("number").columns:
        gdf[col] = gdf[col].astype(float).round(3)
    return json.dumps(json.loads(gdf.to_json(na="null")), allow_nan=False)


def choropleth_map(districts: gpd.GeoDataFrame, metric: str, k: int = 5) -> folium.Map:
    """Kartogram miary dzielnicowej z klasami kwantylowymi i legendą z jednostką.

    Dzielnice bez wartości (brak ofert) są szare. Dla miar cenowych zaleca się zestawienie
    z ``n_priced`` — dzielnice o małej liczbie ofert mają niewiarygodne statystyki.
    """
    info = METRICS[metric]
    fmap = _base_map(tuple(districts.total_bounds))
    values = districts[metric].astype(float)
    valid = values.dropna()

    breaks: list[float] = []
    if len(valid) >= 2 and valid.nunique() > 1:
        classifier = mapclassify.Quantiles(valid.to_numpy(), k=min(k, valid.nunique()))
        breaks = [float(valid.min())] + [float(b) for b in classifier.bins]
    n_classes = max(len(breaks) - 1, 1)
    colors = [SEQUENTIAL[round(i * (len(SEQUENTIAL) - 1) / max(n_classes - 1, 1))] for i in range(n_classes)]

    def color_for(v: float | None) -> str:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return NO_DATA_COLOR
        if len(breaks) < 2:
            return colors[0]
        for i in range(1, len(breaks)):
            if v <= breaks[i]:
                return colors[i - 1]
        return colors[-1]

    fields = ["neighbourhood", metric, "n_listings", "n_priced", "area_km2"]
    data = _geojson_for(districts, fields)
    folium.GeoJson(
        data,
        name=info.label,
        style_function=lambda feat: {
            "fillColor": color_for(feat["properties"].get(metric)), "color": "#444", "weight": 0.8, "fillOpacity": 0.82,
        },
        highlight_function=lambda _: {"weight": 2.5, "color": "#000"},
        tooltip=folium.GeoJsonTooltip(
            fields=["neighbourhood", metric, "n_listings", "n_priced", "area_km2"],
            aliases=["Dzielnica:", f"{info.label} [{info.unit}]:", "Liczba ofert:", "Oferty z ceną:", "Powierzchnia [km²]:"],
            localize=True, sticky=True,
        ),
    ).add_to(fmap)

    if len(breaks) >= 2:
        items = [(colors[i - 1], f"{breaks[i - 1]:,.1f} – {breaks[i]:,.1f}") for i in range(1, len(breaks))]
    else:
        items = []
    items.append((NO_DATA_COLOR, "brak danych"))
    _legend_html(f"{info.label} [{info.unit}]<br><small>klasy kwantylowe</small>", items).add_to(fmap)
    return fmap


def lisa_map(districts: gpd.GeoDataFrame, variable_label: str = "mediana ceny") -> folium.Map:
    """Mapa klastrów LISA (High-High, Low-Low, High-Low, Low-High, brak istotności)."""
    fmap = _base_map(tuple(districts.total_bounds))
    cols = ["neighbourhood", "lisa_cluster", "lisa_code", "lisa_I", "lisa_p", "median_price", "n_priced"]
    folium.GeoJson(
        _geojson_for(districts, cols),
        name="Klastry LISA",
        style_function=lambda feat: {
            "fillColor": CLUSTER_COLORS.get(feat["properties"].get("lisa_code"), "#fff"),
            "color": "#444", "weight": 0.8, "fillOpacity": 0.85,
        },
        highlight_function=lambda _: {"weight": 2.5, "color": "#000"},
        tooltip=folium.GeoJsonTooltip(
            fields=["neighbourhood", "lisa_cluster", "lisa_I", "lisa_p", "median_price", "n_priced"],
            aliases=["Dzielnica:", "Klaster:", "Local Moran's I:", "pseudo-p:", "Mediana ceny [$]:", "Oferty z ceną:"],
            localize=True, sticky=True,
        ),
    ).add_to(fmap)
    _legend_html(f"Klastry LISA ({variable_label})",
                 [(CLUSTER_COLORS[c], CLUSTER_LABELS[c]) for c in ("HH", "LL", "HL", "LH", "ns", "n/a")]).add_to(fmap)
    return fmap


# --- Wykresy ----------------------------------------------------------------------------------------------

def _style(fig: go.Figure, height: int = 420) -> go.Figure:
    fig.update_layout(template="plotly_white", height=height, margin=dict(l=10, r=10, t=50, b=10),
                      font=dict(size=13), title_font_size=16)
    return fig


def price_histogram(points: gpd.GeoDataFrame, log_x: bool = True) -> go.Figure:
    """Histogram cen. Skala logarytmiczna: biny równej szerokości w log10(ceny), etykiety osi w dolarach."""
    df = points.dropna(subset=["price"])
    df = df[df["price"] > 0]
    labels = {"count": "Liczba ofert"}
    if log_x:
        fig = px.histogram(x=np.log10(df["price"]), nbins=60, color_discrete_sequence=[PRIMARY],
                           title="Rozkład cen za noc (skala logarytmiczna)")
        ticks = [t for t in (10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000) if df["price"].min() / 2 <= t <= df["price"].max() * 2]
        fig.update_xaxes(tickvals=np.log10(ticks), ticktext=[f"{t:,}".replace(",", " ") for t in ticks], title="Cena za noc [$]")
        median_x = np.log10(df["price"].median()) if len(df) else None
    else:
        upper = float(df["price"].quantile(0.99)) if len(df) else 1.0
        fig = px.histogram(df[df["price"] <= upper], x="price", nbins=60, color_discrete_sequence=[PRIMARY], labels=labels,
                           title=f"Rozkład cen za noc (do 99. percentyla = {upper:,.0f} $)".replace(",", " "))
        fig.update_xaxes(title="Cena za noc [$]")
        median_x = df["price"].median() if len(df) else None
    fig.update_layout(yaxis_title="Liczba ofert", bargap=0.03)
    if median_x is not None and len(df):
        fig.add_vline(x=median_x, line_dash="dash", line_color="#111",
                      annotation_text=f"mediana {df['price'].median():,.0f} $".replace(",", " "))
    return _style(fig)


def price_box(points: gpd.GeoDataFrame, by: str, title: str, order_by_median: bool = True, max_groups: int = 25) -> go.Figure:
    df = points.dropna(subset=["price"])
    order = df.groupby(by)["price"].median().sort_values(ascending=False)
    keep = order.index[:max_groups] if order_by_median else order.index
    fig = px.box(df[df[by].isin(keep)], x=by, y="price", log_y=True, category_orders={by: list(keep)},
                 color_discrete_sequence=[PRIMARY], labels={by: "", "price": "Cena za noc [$]"}, title=title)
    fig.update_xaxes(tickangle=-45)
    return _style(fig, 480)


def district_bar(districts: gpd.GeoDataFrame, metric: str, top_n: int = 15, min_priced: int = 0) -> go.Figure:
    info = METRICS[metric]
    df = districts.dropna(subset=[metric])
    if metric not in ("n_listings", "density_km2"):
        df = df[df["n_priced"] >= max(min_priced, 1)]
    df = df.sort_values(metric, ascending=False).head(top_n).iloc[::-1]
    fig = px.bar(df, x=metric, y="neighbourhood", orientation="h", color_discrete_sequence=[PRIMARY],
                 hover_data={"n_listings": True, "n_priced": True},
                 labels={metric: f"{info.label} [{info.unit}]", "neighbourhood": ""},
                 title=f"{info.label} — {len(df)} najwyższych dzielnic")
    return _style(fig, max(320, 26 * len(df) + 120))


def price_vs_distance(points: gpd.GeoDataFrame, max_points: int = 4000, seed: int = 42) -> go.Figure:
    df = points.dropna(subset=["price", "dist_center_km"])
    fig = go.Figure()
    if len(df):
        sample = df.sample(n=min(max_points, len(df)), random_state=seed)
        fig.add_trace(go.Scattergl(x=sample["dist_center_km"], y=sample["price"], mode="markers", name="oferty (próbka)",
                                   marker=dict(size=4, color=PRIMARY, opacity=0.25)))
        bins = pd.cut(df["dist_center_km"], bins=np.arange(0, df["dist_center_km"].max() + 0.5, 0.5))
        med = df.groupby(bins, observed=True).agg(d=("dist_center_km", "mean"), p=("price", "median"), n=("price", "size"))
        med = med[med["n"] >= 10]
        fig.add_trace(go.Scatter(x=med["d"], y=med["p"], mode="lines+markers", name="mediana w pasie 0,5 km",
                                 line=dict(color="#d73027", width=3)))
    fig.update_yaxes(type="log", title="Cena za noc [$] (skala log)")
    fig.update_xaxes(title="Odległość od centrum [km]")
    fig.update_layout(title="Cena a odległość od centrum miasta")
    return _style(fig)


def price_by_accommodates(points: gpd.GeoDataFrame) -> go.Figure:
    df = points.dropna(subset=["price", "accommodates"]).copy()
    df["accommodates"] = df["accommodates"].clip(upper=10).astype(int)
    fig = px.box(df, x="accommodates", y="price", log_y=True, color_discrete_sequence=[PRIMARY],
                 labels={"accommodates": "Liczba gości (10 = 10 i więcej)", "price": "Cena za noc [$]"},
                 title="Cena a liczba gości")
    return _style(fig)


def moran_scatterplot(result: MoranResult) -> go.Figure:
    """Wykres rozrzutu Morana: wartość standaryzowana vs średnia sąsiadów (kolor = klasa LISA)."""
    d = pd.DataFrame(result.districts.drop(columns="geometry"))
    d = d[d["z_value"].notna()]
    fig = px.scatter(d, x="z_value", y="z_lag", color="lisa_code", hover_name="neighbourhood",
                     color_discrete_map=CLUSTER_COLORS, category_orders={"lisa_code": ["HH", "LL", "HL", "LH", "ns"]},
                     labels={"z_value": f"Wartość standaryzowana ({result.variable})", "z_lag": "Średnia sąsiadów (lag przestrzenny)",
                             "lisa_code": "Klaster"},
                     title=f"Wykres rozrzutu Morana (nachylenie = I = {result.I:.3f})")
    fig.update_traces(marker=dict(size=10, line=dict(width=1, color="#333")))
    lim = float(max(d["z_value"].abs().max(), d["z_lag"].abs().max()) * 1.1)
    fig.add_trace(go.Scatter(x=[-lim, lim], y=[-lim * result.I, lim * result.I], mode="lines", name="regresja (nachylenie I)",
                             line=dict(color="#111", dash="dash")))
    fig.add_hline(y=0, line_color="#888", line_width=1)
    fig.add_vline(x=0, line_color="#888", line_width=1)
    return _style(fig, 480)


def label_outside(points: gpd.GeoDataFrame) -> int:
    """Liczba ofert poza granicami dzielnic (do komunikatów jakości)."""
    return int((points["neighbourhood"] == OUTSIDE_LABEL).sum())
