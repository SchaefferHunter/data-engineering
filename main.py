"""Batch-ETL für einen BDG2-Standort.

Aus dem Projektverzeichnis starten, zum Beispiel:
python main.py --site Fox --weather-raw data/raw/open_meteo/Fox/<abruf>.raw.json
"""

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from src.config import SITE_CONFIG
from src.load.export import export_parquet
from src.load.pipeline import rebuild_database
from src.transform.buildings import transform_buildings
from src.transform.electricity import transform_electricity
from src.transform.sites import transform_sites
from src.transform.weather import transform_weather
from src.validation.report import create_quality_report
from src.validation.quality import (
    check_completeness,
    validate_weather,
    validate_weather_coverage,
)

ROOT = Path(__file__).resolve().parent


def check_archive(raw_path: Path, site_id: str) -> None:
    """Archiv-Prüfsumme und die für unsere Transformation wichtigen Parameter prüfen."""
    if not raw_path.name.endswith(".raw.json") or not raw_path.is_file():
        raise ValueError(f"Keine archivierte Open-Meteo-Rohdatei: {raw_path}")

    metadata_path = raw_path.with_name(
        raw_path.name.replace(".raw.json", ".metadata.json")
    )
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    if digest != metadata["sha256"]:
        raise ValueError("SHA-256-Prüfsumme der Wetter-Rohdaten stimmt nicht.")
    if metadata["site_id"] != site_id:
        raise ValueError("Standort im Wetterarchiv stimmt nicht mit --site überein.")

    params = metadata["params"]
    if params.get("timezone") != "UTC" or params.get("models") != "era5":
        raise ValueError("Wetterarchiv muss ERA5-Daten mit UTC-Zeitstempeln enthalten.")


def run(site_id: str, raw_path: Path) -> None:
    if site_id not in SITE_CONFIG:
        raise ValueError(f"Standort ist nicht konfiguriert: {site_id}")
    check_archive(raw_path, site_id)

    # Extract / vorbereitete BDG2-Rohdateien lesen
    metadata = pd.read_csv(ROOT / "data" / "metadata.csv")
    electricity = pd.read_csv(
        ROOT / "data" / "electricity.csv",
        usecols=lambda c: c == "timestamp" or c.startswith(f"{site_id}_"),
    )
    building_ids = [c for c in electricity.columns if c.startswith(f"{site_id}_")]
    if not building_ids:
        raise ValueError(f"Keine Strommessreihen für Standort {site_id} gefunden.")

    # Aus dem Data Profiling abgeleitete Selektionsregeln
    selection = check_completeness(electricity, building_ids)
    selected_ids = selection.loc[selection["selected"], "building_id"].tolist()
    if not selected_ids:
        raise ValueError("Keine Gebäude bestehen die Vollständigkeitsfilter.")

    # Transform
    sites = transform_sites(metadata, [site_id])
    buildings = transform_buildings(metadata, selected_ids)
    energy = transform_electricity(
        electricity,
        selected_ids,
        source_utc_offset_hours=SITE_CONFIG[site_id]["source_utc_offset_hours"],
    )
    weather = transform_weather(raw_path)  # liest nur aus dem Raw Layer
    if set(weather["site_id"].unique()) != {site_id}:
        raise ValueError("Wetterdaten enthalten einen anderen Standort.")

    # Validate: Probleme vor dem Laden abbrechen
    quality = validate_weather(weather)
    coverage = validate_weather_coverage(weather, raw_path)
    error_fields = ("duplicates", "invalid_intervals", "invalid_humidity", "negative_radiation")
    if any(quality[field] for field in error_fields):
        raise ValueError(f"Wetter-Qualitätsprüfung fehlgeschlagen: {quality}")
    if any(quality["missing_values"].values()):
        raise ValueError(f"Fehlende Wetterwerte: {quality['missing_values']}")
    if coverage["missing_timestamps"] or coverage["unexpected_timestamps"]:
        raise ValueError(f"Wetter-Zeitabdeckung fehlerhaft: {coverage}")

    # Load: DB wird vorläufig neu aufgebaut und nur nach Erfolg ersetzt
    counts = rebuild_database(sites, buildings, energy, weather)

    # Export: eigene vorläufige Parquet-Datei, nach Prüfung ersetzt
    db_path = ROOT / "corpus" / "energy.duckdb"
    export_result = export_parquet(db_path)
    quality_report = create_quality_report(
        db_path, selection, quality, coverage, export_result
    )

    print("DuckDB:", counts)
    print("Parquet:", export_result)
    print("Qualitaetsbericht:", quality_report)
    print("Ausgewählte Gebäude:", len(selected_ids), "/", len(building_ids))


def main() -> None:
    parser = argparse.ArgumentParser(description="BDG2/Open-Meteo-Korpus neu aufbauen")
    parser.add_argument("--site", required=True, choices=sorted(SITE_CONFIG))
    parser.add_argument(
        "--weather-raw", required=True, type=Path,
        help="Pfad zur archivierten Open-Meteo-*.raw.json-Datei",
    )
    args = parser.parse_args()
    raw_path = args.weather_raw
    if not raw_path.is_absolute():
        raw_path = ROOT / raw_path
    run(args.site, raw_path.resolve())


if __name__ == "__main__":
    main()
