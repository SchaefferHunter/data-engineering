"""Phase 1: Vollständiger Neuaufbau und Veröffentlichung per Dateiersatz."""

import os
from pathlib import Path
from uuid import uuid4

import duckdb
import pandas as pd

from src.config import SITE_CONFIG, study_window_utc
from src.load.database import create_database
from src.load.sites import load_sites
from src.load.buildings import load_buildings
from src.load.energy import load_energy
from src.load.weather import load_weather


def _new_staging_path(target: Path) -> Path:
    return target.parent / f".{target.stem}.{uuid4().hex}.building.duckdb"


def _cleanup_stage(stage: Path) -> None:
    for path in (stage, Path(str(stage) + ".wal")):
        path.unlink(missing_ok=True)


def _crop_to_period(
    sites: pd.DataFrame,
    buildings: pd.DataFrame,
    energy: pd.DataFrame,
    weather: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Strom und Wetter anhand der BDG2-Zeitannahmen auf 17544 Stunden kürzen."""
    # Strukturfehler vor dem Zuschneiden prüfen, statt sie durch Filterung
    # unbemerkt verschwinden zu lassen.
    required = (
        (sites, ["site_id"]),
        (buildings, ["building_id", "site_id"]),
        (energy, ["building_id", "timestamp"]),
        (weather, ["site_id", "timestamp"]),
    )
    for frame, columns in required:
        absent = set(columns) - set(frame.columns)
        if absent:
            raise ValueError(f"Fehlende Pflichtspalten: {sorted(absent)}")
        if frame[columns].isna().any().any():
            raise ValueError(f"Fehlende ID oder Zeitstempel in {columns}")
    if sites.empty or buildings.empty or energy.empty or weather.empty:
        raise ValueError("Mindestens ein erforderlicher Eingangsdatensatz ist leer")

    site_ids = set(sites["site_id"].dropna())
    unknown_sites = site_ids - set(SITE_CONFIG)
    if unknown_sites:
        raise ValueError(f"Unkonfigurierte Standorte: {sorted(unknown_sites)}")

    # Nicht bekannte Zuordnungen dürfen beim Zuschneiden nicht verschwinden.
    site_by_building = dict(zip(buildings["building_id"], buildings["site_id"]))
    if buildings["building_id"].duplicated().any():
        raise ValueError("building: doppelte Gebäude-IDs")
    if not set(buildings["site_id"].dropna()).issubset(site_ids):
        raise ValueError("building: unbekannte site_id")
    if not set(energy["building_id"].dropna()).issubset(site_by_building):
        raise ValueError("energy: unbekannte building_id")
    if not set(weather["site_id"].dropna()).issubset(site_ids):
        raise ValueError("weather: unbekannte site_id")

    energy_parts = []
    weather_parts = []
    for site_id in sorted(site_ids):
        start, end = study_window_utc(site_id)
        weather_parts.append(weather.loc[
            weather["site_id"].eq(site_id)
            & weather["timestamp"].between(start, end)
        ])
        building_ids = {k for k, site in site_by_building.items() if site == site_id}
        energy_parts.append(energy.loc[
            energy["building_id"].isin(building_ids)
            & energy["timestamp"].between(start, end)
        ])

    return (
        pd.concat(energy_parts, ignore_index=True),
        pd.concat(weather_parts, ignore_index=True),
    )


def _populate_and_verify(
    stage: Path,
    sites: pd.DataFrame,
    buildings: pd.DataFrame,
    energy: pd.DataFrame,
    weather: pd.DataFrame,
    *,
    with_constraints: bool,
) -> dict[str, int]:
    create_database(stage, with_constraints=with_constraints)
    load_sites(stage, sites)
    load_buildings(stage, buildings)
    load_energy(stage, energy)
    load_weather(stage, weather)

    con = duckdb.connect(str(stage))
    try:
        con.execute("SET TimeZone = 'UTC'")
        counts = {
            name: con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            for name in ("site", "building", "energy", "weather")
        }
        expected = {
            "site": len(sites),
            "building": len(buildings),
            "energy": len(energy),
            "weather": len(weather),
        }
        if counts != expected:
            raise ValueError(f"Nachprüfung fehlgeschlagen: ist={counts}, soll={expected}")
        # Checkpoint synchronisiert evtl. verbliebene WAL-Daten in die DB-Datei.
        con.execute("CHECKPOINT")
    finally:
        con.close()
    return counts


def rebuild_database(
    sites: pd.DataFrame,
    buildings: pd.DataFrame,
    energy: pd.DataFrame,
    weather: pd.DataFrame,
    *,
    target_path: Path | None = None,
    with_constraints: bool = True,
) -> dict[str, int]:
    """Nur nach komplett erfolgreichem Neuaufbau die bisherige DB ersetzen."""
    if target_path is None:
        target_path = Path(__file__).resolve().parents[2] / "corpus" / "energy.duckdb"
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = _new_staging_path(target)   # gleicher Ordner => gleicher Datenträger

    try:
        energy_trimmed, weather_trimmed = _crop_to_period(
            sites, buildings, energy, weather
        )
        counts = _populate_and_verify(
            stage, sites, buildings, energy_trimmed, weather_trimmed,
            with_constraints=with_constraints
        )
        # Keine DB-Verbindungen mehr offen halten; auch nicht auf target.
        # Bei Fehlschlag (z. B. Windows-Dateisperre) bleibt target unverändert.
        os.replace(stage, target)
        return counts
    finally:
        _cleanup_stage(stage)
