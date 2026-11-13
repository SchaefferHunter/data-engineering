"""Qualitaetsbericht eines bereits erzeugten BDG2-/Open-Meteo-Korpus.

Der Bericht wird aus dem tatsaechlichen DuckDB-Inhalt, der Gebaeudeauswahl
und den Ergebnissen der Wetterpruefung erstellt. Es werden keine Messwerte
geaendert. Die JSON-Datei wird erst nach erfolgreicher Erstellung ersetzt.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import duckdb
import pandas as pd

from src.config import EXPECTED_HOURS


def create_quality_report(
    db_path: Path,
    selection: pd.DataFrame,
    weather_validation: dict,
    weather_coverage: dict,
    export_result: dict,
    *,
    output_path: Path | None = None,
) -> dict:
    """Erzeugt output/quality_report.json nach einem erfolgreichen Export."""
    db_path = Path(db_path).resolve()
    if output_path is None:
        output_path = db_path.parent.parent / "output" / "quality_report.json"
    output_path = Path(output_path).resolve()

    required = {"building_id", "selected", "completeness_total"}
    if missing := required.difference(selection.columns):
        raise ValueError(f"Gebaeudeauswahl unvollstaendig: {sorted(missing)}")

    selected_count = int(selection["selected"].sum())
    excluded = selection.loc[~selection["selected"], "building_id"].tolist()

    with duckdb.connect(str(db_path), read_only=True) as con:
        con.execute("SET TimeZone = 'UTC'")
        site_rows = con.execute("""
            SELECT site_id, iana_timezone, source_utc_offset_hours
            FROM site ORDER BY site_id
        """).fetchall()
        building_count = con.execute("SELECT COUNT(*) FROM building").fetchone()[0]
        (energy_rows, missing_energy, min_utc, max_utc) = con.execute("""
            SELECT COUNT(*), COUNT(*) - COUNT(consumption),
                   MIN(timestamp), MAX(timestamp)
            FROM energy
        """).fetchone()
        weather_rows = con.execute("SELECT COUNT(*) FROM weather").fetchone()[0]
        weather_nulls = con.execute("""
            SELECT COUNT(*) FILTER (
                WHERE air_temperature IS NULL
                   OR relative_humidity IS NULL
                   OR shortwave_radiation IS NULL
            ) FROM weather
        """).fetchone()[0]
        by_building = con.execute("""
            SELECT building_id, COUNT(*) AS hours,
                   COUNT(consumption) AS valid_values,
                   COUNT(*) - COUNT(consumption) AS missing_values,
                   ROUND(100.0 * COUNT(consumption) / COUNT(*), 4)
                       AS completeness_pct
            FROM energy
            GROUP BY building_id
            ORDER BY missing_values DESC, building_id
        """).fetchall()

    if not site_rows or len(site_rows) != 1:
        raise ValueError("Dieser Bericht unterstuetzt einen Standort pro Lauf.")
    if building_count != selected_count or len(by_building) != selected_count:
        raise ValueError("Gebaeudeanzahl weicht von der Selektion ab.")
    if energy_rows != selected_count * EXPECTED_HOURS:
        raise ValueError("Stromdatensaetze entsprechen nicht dem erwarteten Stundenraster.")
    if any(hours != EXPECTED_HOURS for _, hours, *_ in by_building):
        raise ValueError("Mindestens ein Gebaeude hat eine unvollstaendige Stundenachse.")
    if weather_rows != EXPECTED_HOURS or weather_nulls:
        raise ValueError("Wetter-Zeitachse oder Wetterwerte sind unvollstaendig.")
    if int(export_result["rows"]) != energy_rows:
        raise ValueError("Parquet- und DuckDB-Zeilenanzahl unterscheiden sich.")
    if int(export_result["missing_consumption"]) != missing_energy:
        raise ValueError("Parquet und DuckDB haben unterschiedliche Verbrauchsluecken.")
    if sum(export_result["split_counts"].values()) != energy_rows:
        raise ValueError("Die Train/Test-Zeilenzahlen sind unplausibel.")

    building_details = [
        {
            "building_id": building_id,
            "hours": int(hours),
            "valid_values": int(valid),
            "missing_values": int(missing),
            "completeness_percent": float(pct),
        }
        for building_id, hours, valid, missing, pct in by_building
    ]
    top_five_missing = sum(b["missing_values"] for b in building_details[:5])
    site_id, iana_zone, source_offset = site_rows[0]

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "site_id": site_id,
        "time_assumption": {
            "source_utc_offset_hours": int(source_offset),
            "calendar_iana_timezone": iana_zone,
        },
        "selection": {
            "criteria": {"completeness_total_min": 0.95, "completeness_per_year_min": 0.90},
            "candidate_buildings": int(len(selection)),
            "selected_buildings": selected_count,
            "excluded_buildings": len(excluded),
            "excluded_building_ids": excluded,
        },
        "electricity": {
            "rows": int(energy_rows),
            "expected_hours_per_building": int(EXPECTED_HOURS),
            "missing_consumption": int(missing_energy),
            "completeness_percent": round(100 * (energy_rows - missing_energy) / energy_rows, 4),
            "first_timestamp_utc": min_utc.isoformat(),
            "last_timestamp_utc": max_utc.isoformat(),
            "five_buildings_with_most_missing_values": building_details[:5],
            "missing_values_in_five_weakest_buildings": int(top_five_missing),
            "share_of_all_missing_percent": round(100 * top_five_missing / missing_energy, 2)
            if missing_energy else 0.0,
            "all_selected_buildings": building_details,
        },
        "weather": {
            "raw_archive_rows": int(weather_validation["rows"]),
            "rows_in_database": int(weather_rows),
            "missing_complete_weather_records_in_database": int(weather_nulls),
            "raw_duplicates": int(weather_validation["duplicates"]),
            "raw_invalid_hourly_intervals": int(weather_validation["invalid_intervals"]),
            "raw_invalid_humidity": int(weather_validation["invalid_humidity"]),
            "raw_negative_radiation": int(weather_validation["negative_radiation"]),
            "raw_missing_values": {
                key: int(value) for key, value in weather_validation["missing_values"].items()
            },
            "coverage_checked": True,
        },
        "release": {
            "parquet_path": export_result["path"],
            "manifest_path": export_result["manifest_path"],
            "parquet_sha256": export_result["sha256"],
            "split_counts": export_result["split_counts"],
        },
    }

    # Die erwartete Abdeckung wurde bereits vor dem Datenbankaufbau validiert.
    # Die beiden Fehlerzaehler werden trotzdem im Bericht dokumentiert.
    for key in ("missing_timestamps", "unexpected_timestamps"):
        value = weather_coverage[key]
        report["weather"][key] = len(value) if isinstance(value, (list, tuple, set)) else int(value)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f".{output_path.stem}.{uuid4().hex}.tmp.json")
    try:
        temp_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_path, output_path)
    finally:
        temp_path.unlink(missing_ok=True)

    return {
        "path": str(output_path),
        "selected_buildings": selected_count,
        "completeness_percent": report["electricity"]["completeness_percent"],
        "missing_consumption": int(missing_energy),
        "weakest_building": building_details[0]["building_id"],
    }
