"""Generowanie projektu QGIS (.qgz) z warstw wyeksportowanych do GeoPackage.

Moduł wymaga PyQGIS (``qgis.core``) i **nie** importuje pozostałych zależności projektu, dzięki czemu
można go uruchomić systemowym interpreterem z zainstalowanym QGIS:

    QT_QPA_PLATFORM=offscreen /usr/bin/python3 -m georent.qgis_project exports/barcelona

Ścieżki do warstw są względne wobec pliku projektu, więc katalog eksportu można przenosić.
"""

from __future__ import annotations

import argparse
import gc
import os
import sys
from pathlib import Path

GPKG_NAME = "georent.gpkg"
PROJECT_NAME = "georent.qgz"

LISA_STYLE = [  # (kod, etykieta, kolor)
    ("HH", "High-High (gorące punkty)", "#d7191c"),
    ("LL", "Low-Low (zimne punkty)", "#2c7bb6"),
    ("HL", "High-Low (wysoka wśród niskich)", "#fdae61"),
    ("LH", "Low-High (niska wśród wysokich)", "#abd9e9"),
    ("ns", "Brak istotności", "#e0e0e0"),
    ("n/a", "Brak danych / sąsiadów", "#ffffff"),
]


def build_project(export_dir: Path, project_path: Path | None = None) -> Path:
    """Zbuduj projekt QGIS dla katalogu eksportu zawierającego ``georent.gpkg``."""
    try:
        from qgis.core import QgsApplication  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "PyQGIS nie jest dostępny. Uruchom ten moduł interpreterem systemowym z zainstalowanym QGIS "
            "(np. /usr/bin/python3), a nie środowiskiem wirtualnym."
        ) from exc

    export_dir = export_dir.resolve()
    gpkg = export_dir / GPKG_NAME
    if not gpkg.exists():
        raise FileNotFoundError(f"Brak {gpkg} — najpierw wykonaj eksport (python -m georent export).")
    project_path = project_path or export_dir / PROJECT_NAME

    app = QgsApplication([], False)
    app.initQgis()
    try:
        _write_project(gpkg, project_path)
    finally:
        # Obiekty QGIS muszą zostać zniszczone przed exitQgis(), inaczej proces kończy się błędem segmentacji.
        gc.collect()
        app.exitQgis()
    return project_path


def _write_project(gpkg: Path, project_path: Path) -> None:
    """Zbuduj i zapisz projekt (wszystkie obiekty QGIS są lokalne dla tej funkcji)."""
    from qgis.core import (  # type: ignore
        QgsCategorizedSymbolRenderer, QgsClassificationQuantile, QgsCoordinateReferenceSystem, QgsFillSymbol,
        QgsGradientColorRamp, QgsGraduatedSymbolRenderer, QgsPalLayerSettings, QgsProject, QgsRendererCategory,
        QgsRasterLayer, QgsSymbol, QgsVectorLayer, QgsVectorLayerSimpleLabeling,
    )
    from qgis.PyQt.QtGui import QColor  # type: ignore

    project = QgsProject()
    project.setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    project.setTitle("GeoRent-AI — rynek najmu krótkoterminowego")
    root = project.layerTreeRoot()

    def load(layer_name: str, title: str) -> QgsVectorLayer:
        layer = QgsVectorLayer(f"{gpkg}|layername={layer_name}", title, "ogr")
        if not layer.isValid():
            raise RuntimeError(f"Nie można wczytać warstwy {layer_name!r} z {gpkg}")
        return layer

    def graduated(layer: QgsVectorLayer, field: str, light: str, dark: str, classes: int = 5) -> None:
        ramp = QgsGradientColorRamp(QColor(light), QColor(dark))
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        renderer = QgsGraduatedSymbolRenderer(field)
        renderer.setSourceSymbol(symbol)
        renderer.setSourceColorRamp(ramp)
        renderer.setClassificationMethod(QgsClassificationQuantile())
        renderer.updateClasses(layer, classes)
        layer.setRenderer(renderer)

    # --- podkład (kafelki OSM; wymaga Internetu przy otwarciu) ---
    basemap = QgsRasterLayer(
        "type=xyz&url=https://tile.openstreetmap.org/%7Bz%7D/%7Bx%7D/%7By%7D.png&zmax=19&zmin=0", "OpenStreetMap", "wms"
    )
    if basemap.isValid():
        project.addMapLayer(basemap, False)

    layers: list[QgsVectorLayer] = []

    # LISA — kategorie
    lisa = load("dzielnice_statystyki", "Klastry LISA (mediana ceny)")
    cats = []
    for code, label, color in LISA_STYLE:
        sym = QgsFillSymbol.createSimple({"color": color, "outline_color": "#555555", "outline_width": "0.2"})
        cats.append(QgsRendererCategory(code, sym, label))
    lisa.setRenderer(QgsCategorizedSymbolRenderer("lisa_code", cats))
    layers.append(lisa)

    # Kartogramy
    for field, title, light, dark in (
        ("median_price", "Kartogram — mediana ceny za noc [$]", "#ffffcc", "#800026"),
        ("density_km2", "Kartogram — zagęszczenie ofert [oferty/km²]", "#f7fbff", "#08306b"),
        ("n_listings", "Kartogram — liczba ofert", "#f7fcf5", "#00441b"),
    ):
        lyr = load("dzielnice_statystyki", title)
        graduated(lyr, field, light, dark)
        layers.append(lyr)

    # Oferty
    points = load("oferty_punkty", "Oferty najmu (punkty, kolor = cena)")
    graduated(points, "price", "#ffffb2", "#bd0026")
    points.renderer().sourceSymbol().setSize(1.6)
    points.renderer().updateSymbols(points.renderer().sourceSymbol())
    layers.insert(0, points)

    # Granice z etykietami
    borders = load("dzielnice_granice", "Granice dzielnic")
    symbol = QgsFillSymbol.createSimple({"color": "0,0,0,0", "outline_color": "#222222", "outline_width": "0.4"})
    borders.renderer().setSymbol(symbol)
    settings = QgsPalLayerSettings()
    settings.fieldName = "neighbourhood"
    settings.enabled = True
    borders.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    borders.setLabelsEnabled(False)  # etykiety można włączyć w panelu stylów
    layers.insert(0, borders)

    for layer in layers:
        project.addMapLayer(layer, False)
        root.addLayer(layer)
    if basemap.isValid():
        root.addLayer(basemap)

    # Domyślnie widoczne: granice + kartogram mediany ceny + LISA wyłączona itd.
    for layer in layers:
        node = root.findLayer(layer.id())
        node.setItemVisibilityChecked(layer.name() in {"Granice dzielnic", "Kartogram — mediana ceny za noc [$]"})
        node.setExpanded(False)

    project.setFileName(str(project_path))
    if not project.write(str(project_path)):
        raise RuntimeError(f"Nie udało się zapisać projektu: {project.error()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Zbuduj projekt QGIS (.qgz) z wyeksportowanego GeoPackage.")
    parser.add_argument("export_dir", type=Path, help="Katalog z georent.gpkg")
    parser.add_argument("--output", type=Path, default=None, help="Ścieżka pliku .qgz (domyślnie <export_dir>/georent.qgz)")
    args = parser.parse_args(argv)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    path = build_project(args.export_dir, args.output)
    print(f"Zapisano projekt QGIS: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
