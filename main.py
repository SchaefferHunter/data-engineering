"""Batch-ETL für einen BDG2-Standort.

Aus dem Projektverzeichnis starten, zum Beispiel:
python main.py --site Fox --weather-raw data/raw/open_meteo/Fox/<abruf>.raw.json

Jeder Lauf schreibt ein Protokoll nach output/logs/ – auch im Fehlerfall.
"""

import argparse
import hashlib
import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

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
ELECTRICITY_CSV = ROOT / "data" / "electricity.csv"
METADATA_CSV = ROOT / "data" / "metadata.csv"

log = logging.getLogger("pipeline")


def sha256_file(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def git_state() -> dict:
    """Commit-Hash und ob uncommittete Änderungen vorliegen (falls Git verfügbar)."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
            capture_output=True, text=True, check=True,
        ).stdout.strip())
        return {"git_commit": commit, "git_uncommitted_changes": dirty}
    except (OSError, subprocess.CalledProcessError):
        return {"git_commit": None, "git_uncommitted_changes": None}


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


def prepare(site_id: str, raw_path: Path) -> dict:
    """Extract, Gebäudeauswahl, Transform und Wettervalidierung (ohne Laden)."""
    if site_id not in SITE_CONFIG:
        raise ValueError(f"Standort ist nicht konfiguriert: {site_id}")
    check_archive(raw_path, site_id)

    # Extract / vorbereitete BDG2-Rohdateien lesen
    metadata = pd.read_csv(METADATA_CSV)
    electricity = pd.read_csv(
        ELECTRICITY_CSV,
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

    return {
        "building_ids": building_ids,
        "selection": selection,
        "selected_ids": selected_ids,
        "sites": sites,
        "buildings": buildings,
        "energy": energy,
        "weather": weather,
        "quality": quality,
        "coverage": coverage,
    }


def run(site_id: str, raw_path: Path) -> None:
    started = perf_counter()

    # Herkunft vor jeder Verarbeitung festhalten (Manifest + Protokoll)
    provenance = {
        "site_id": site_id,
        "inputs_sha256": {
            "data/electricity.csv": sha256_file(ELECTRICITY_CSV),
            "data/metadata.csv": sha256_file(METADATA_CSV),
            raw_path.relative_to(ROOT).as_posix(): sha256_file(raw_path),
        },
        **git_state(),
    }
    for name, digest in provenance["inputs_sha256"].items():
        log.info("Eingabe %s sha256=%s", name, digest)
    log.info("Codestand %s (uncommittete Änderungen: %s)",
             provenance["git_commit"], provenance["git_uncommitted_changes"])

    data = prepare(site_id, raw_path)
    log.info("Gebäudeauswahl: %d / %d", len(data["selected_ids"]), len(data["building_ids"]))

    # Load: DB wird vorläufig neu aufgebaut und nur nach Erfolg ersetzt
    counts = rebuild_database(data["sites"], data["buildings"], data["energy"], data["weather"])
    log.info("DuckDB neu aufgebaut: %s", counts)

    # Export: eigene vorläufige Parquet-Datei, nach Prüfung ersetzt
    db_path = ROOT / "corpus" / "energy.duckdb"
    export_result = export_parquet(db_path, provenance=provenance)
    log.info("Parquet exportiert: %d Zeilen, Split %s, sha256=%s",
             export_result["rows"], export_result["split_counts"], export_result["sha256"])

    quality_report = create_quality_report(
        db_path, data["selection"], data["quality"], data["coverage"], export_result
    )
    log.info("Qualitätsbericht: %s", quality_report)
    log.info("Lauf erfolgreich in %.1f s", perf_counter() - started)


def setup_logging() -> Path:
    log_dir = ROOT / "output" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"run_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.log"
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for handler in (logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()):
        handler.setFormatter(formatter)
        log.addHandler(handler)
    log.setLevel(logging.INFO)
    return log_path


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

    log_path = setup_logging()
    log.info("Start: site=%s weather_raw=%s", args.site, args.weather_raw)
    try:
        run(args.site, raw_path.resolve())
    except Exception:
        # Abbruch protokollieren; veröffentlichte Dateien bleiben unverändert.
        log.exception("Lauf abgebrochen – veröffentlichte Dateien unverändert")
        raise
    finally:
        log.info("Protokoll: %s", log_path.relative_to(ROOT).as_posix())


if __name__ == "__main__":
    main()
