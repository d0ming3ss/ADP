"""Interfejs wiersza poleceń: ``python -m georent <polecenie>``."""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from georent import analysis, autocorr, data_source, export
from georent.config import CITIES, get_settings

logger = logging.getLogger("georent")


def _load(settings, city: str):
    meta = data_source.get_local_dataset(settings, city)
    if meta is None:
        raise SystemExit(
            f"Brak lokalnych danych dla miasta {city!r}. Uruchom najpierw: python -m georent download --city {city}"
        )
    return analysis.prepare_dataset(meta, settings.__class__(city=city).city_spec())


def cmd_download(args, settings) -> int:
    meta = data_source.download_dataset(settings, args.city, release=args.release, force=args.force)
    print(f"Dane {meta.label}: wydanie {meta.release}, {meta.listings_rows} ofert, "
          f"{meta.neighbourhood_features} dzielnic\nZapisano w: {meta.directory}\nPobrano: {meta.downloaded_at}")
    return 0


def cmd_update(args, settings) -> int:
    remote, local = data_source.check_for_update(settings, args.city)
    print(f"Wydanie w serwisie: {remote or 'nieznane (indeks niedostępny)'}; lokalne: {local or 'brak'}")
    if remote and remote != local:
        meta = data_source.download_dataset(settings, args.city, release=remote)
        print(f"Zaktualizowano do wydania {meta.release}.")
    else:
        print("Dane są aktualne (lub nie można sprawdzić nowszego wydania).")
    return 0


def cmd_analyze(args, settings) -> int:
    ds = _load(settings, args.city)
    res = analysis.run_analysis(ds, settings, analysis.Filters(exclude_outliers=args.exclude_outliers),
                                variable=args.variable, method=args.weights)
    print(f"Oferty po filtrach: {len(res.points)} (z ceną: {int(res.points['price'].notna().sum())}); dzielnic: {len(res.districts)}")
    print(ds.cleaning.as_table().to_string(index=False))
    if res.moran is None:
        print("Autokorelacja pominięta:", res.moran_error)
        return 0
    print("\n" + res.moran.summary_table().to_string(index=False))
    print("\n" + res.moran.interpretation)
    print("\n" + autocorr.cluster_counts(res.districts).to_string(index=False))
    return 0


def cmd_export(args, settings) -> int:
    ds = _load(settings, args.city)
    res = analysis.run_analysis(ds, settings, analysis.Filters(exclude_outliers=args.exclude_outliers),
                                variable=args.variable, method=args.weights)
    out_dir = Path(args.output) if args.output else settings.export_dir / args.city
    result = export.export_all(res, ds, out_dir)
    for f in result.files:
        print("  ", f)
    if not args.no_qgis:
        for exe in (args.qgis_python, "/usr/bin/python3"):
            try:
                proc = subprocess.run(
                    [exe, "-m", "georent.qgis_project", str(out_dir)], capture_output=True, text=True,
                    cwd=Path(__file__).resolve().parent.parent, timeout=300,
                    env={**__import__("os").environ, "QT_QPA_PLATFORM": "offscreen"},
                )
            except FileNotFoundError:
                continue
            if proc.returncode == 0:
                print(proc.stdout.strip())
                break
        else:
            print("Uwaga: nie wygenerowano pliku .qgz (brak PyQGIS). Warstwy GPKG/GeoJSON są gotowe — "
                  "patrz sekcja „QGIS” w PROJEKT.md.", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    settings = get_settings()
    parser = argparse.ArgumentParser(prog="georent", description="GeoRent-AI — analiza rynku najmu krótkoterminowego.")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p, weights=False):
        p.add_argument("--city", default=settings.city, choices=sorted(CITIES), help="miasto (domyślnie z GEORENT_CITY)")
        if weights:
            p.add_argument("--variable", default="median_price", help="zmienna do autokorelacji (kolumna statystyk dzielnic)")
            p.add_argument("--weights", default="queen", choices=sorted(autocorr.WEIGHT_METHODS))
            p.add_argument("--exclude-outliers", action="store_true", help="pomiń oferty odstające cenowo")

    p = sub.add_parser("download", help="pobierz dane Inside Airbnb"); common(p)
    p.add_argument("--release", help="data wydania RRRR-MM-DD (domyślnie najnowsze)")
    p.add_argument("--force", action="store_true", help="pobierz ponownie, nawet jeśli dane istnieją")
    p.set_defaults(func=cmd_download)
    p = sub.add_parser("update", help="sprawdź i pobierz nowsze wydanie danych"); common(p); p.set_defaults(func=cmd_update)
    p = sub.add_parser("analyze", help="uruchom analizę i wypisz wyniki"); common(p, True); p.set_defaults(func=cmd_analyze)
    p = sub.add_parser("export", help="eksport warstw GIS (GPKG, GeoJSON, CSV, QGZ)"); common(p, True)
    p.add_argument("--output", help="katalog eksportu (domyślnie exports/<miasto>)")
    p.add_argument("--no-qgis", action="store_true", help="nie generuj pliku projektu .qgz")
    p.add_argument("--qgis-python", default="python3", help="interpreter z PyQGIS")
    p.set_defaults(func=cmd_export)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    settings = replace(get_settings(), city=args.city)
    try:
        return args.func(args, settings)
    except (data_source.DataSourceError, ValueError, autocorr.InsufficientDataError) as exc:
        print(f"Błąd: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
