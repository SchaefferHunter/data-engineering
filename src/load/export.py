import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import duckdb
import holidays
import pandas as pd


def export_parquet(db_path: Path, *, provenance: dict | None = None) -> dict:
    """Trainingskorpus als Parquet exportieren, prüfen und mit Manifest veröffentlichen.

    provenance: Herkunftsangaben für das Manifest, z. B. SHA-256 der
    Eingabedateien und Git-Commit des Codestands.
    """
    db_path = Path(db_path).resolve()
    if not db_path.is_file():
        raise FileNotFoundError(f"DuckDB-Datei nicht vorhanden: {db_path}")

    export_path = db_path.parent / "training_corpus.parquet"
    manifest_path = db_path.parent / "training_corpus.manifest.json"
    run_id = uuid4().hex
    temp_path = export_path.parent / f".training_corpus_{run_id}.tmp.parquet"
    temp_manifest = manifest_path.parent / f".training_corpus_{run_id}.tmp.manifest.json"

    # US-Bundesfeiertage inklusive beobachteter Ersatztermine.
    # Nur die beiden Untersuchungsjahre sind Teil dieses Pilotprojekts.
    us_holidays = holidays.US(years=[2016, 2017], observed=True)
    holiday_dates = pd.DataFrame({
        "holiday_date": sorted(us_holidays.keys())  # Python-date -> DuckDB DATE
    })

    try:
        with duckdb.connect(str(db_path), read_only=True) as con:
            con.execute("SET TimeZone = 'UTC'")
            con.register("us_federal_holidays", holiday_dates)

            expected_rows, expected_missing = con.execute("""
                SELECT COUNT(*), COUNT(*) - COUNT(consumption)
                FROM energy
            """).fetchone()

            if expected_rows == 0:
                raise ValueError("Tabelle energy ist leer.")

            # Nur der selbst erzeugte lokale Pfad wird in SQL eingebettet.
            sql_path = temp_path.as_posix().replace("'", "''")
            con.execute(f"""
                COPY (
                    WITH local_times AS (
                        SELECT
                            e.building_id,
                            b.site_id,
                            e.timestamp,
                            e.consumption,
                            b.primaryspaceusage,
                            b.sqm,
                            w.air_temperature,
                            w.relative_humidity,
                            w.shortwave_radiation,
                            timezone(s.iana_timezone, e.timestamp) AS local_ts
                        FROM energy AS e
                        JOIN building AS b
                            ON e.building_id = b.building_id
                        JOIN site AS s
                            ON b.site_id = s.site_id
                        LEFT JOIN weather AS w
                            ON b.site_id = w.site_id
                           AND e.timestamp = w.timestamp
                    ),
                    features AS (
                        SELECT
                            lt.building_id,
                            lt.site_id,
                            lt.timestamp,
                            lt.consumption,
                            lt.primaryspaceusage,
                            lt.sqm,
                            lt.air_temperature,
                            lt.relative_humidity,
                            lt.shortwave_radiation,
                            CAST(date_part('hour', lt.local_ts) AS INTEGER)
                                AS local_hour,
                            CAST(date_part('isodow', lt.local_ts) - 1 AS INTEGER)
                                AS local_weekday,
                            (date_part('isodow', lt.local_ts) >= 6)
                                AS is_weekend,
                            (h.holiday_date IS NOT NULL)
                                AS is_holiday,
                            CASE
                                WHEN date_part('year', lt.local_ts) = 2016
                                    THEN 'train'
                                WHEN date_part('year', lt.local_ts) = 2017
                                    THEN 'test'
                                ELSE NULL
                            END AS split
                        FROM local_times AS lt
                        LEFT JOIN us_federal_holidays AS h
                            ON CAST(lt.local_ts AS DATE) = h.holiday_date
                    )
                    SELECT *
                    FROM features
                    ORDER BY building_id, timestamp
                )
                TO '{sql_path}'
                (FORMAT PARQUET, COMPRESSION ZSTD)
            """)

            # Prüfen: Anzahl, fehlende Verbrauchswerte, Wetter, Kalender, Split.
            (rows, missing_consumption, missing_weather,
             missing_calendar, invalid_weekday, invalid_split) = con.execute("""
                SELECT
                    COUNT(*),
                    COUNT(*) - COUNT(consumption),
                    COUNT(*) FILTER (
                        WHERE air_temperature IS NULL
                           OR relative_humidity IS NULL
                           OR shortwave_radiation IS NULL
                    ),
                    COUNT(*) FILTER (
                        WHERE local_hour IS NULL
                           OR local_weekday IS NULL
                           OR is_weekend IS NULL
                           OR is_holiday IS NULL
                    ),
                    COUNT(*) FILTER (
                        WHERE local_hour NOT BETWEEN 0 AND 23
                           OR local_weekday NOT BETWEEN 0 AND 6
                           OR is_weekend <> (local_weekday >= 5)
                    ),
                    COUNT(*) FILTER (
                        WHERE split IS NULL OR split NOT IN ('train', 'test')
                    )
                FROM read_parquet(?)
            """, [str(temp_path)]).fetchone()

            split_counts = dict(con.execute("""
                SELECT split, COUNT(*)
                FROM read_parquet(?)
                GROUP BY split
                ORDER BY split
            """, [str(temp_path)]).fetchall())

            # Metadaten ohne vollständigen Daten-Import in pandas abfragen.
            con.execute("SELECT * FROM read_parquet(?) LIMIT 0", [str(temp_path)])
            columns = [column[0] for column in con.description]

        if rows != expected_rows:
            raise ValueError(f"Parquet-Zeilenzahl: {rows} statt {expected_rows}.")
        if missing_consumption != expected_missing:
            raise ValueError(
                f"Fehlende Verbrauchswerte: {missing_consumption} statt {expected_missing}."
            )
        if missing_weather:
            raise ValueError(f"{missing_weather} Exportzeilen ohne vollständige Wettermerkmale.")
        if missing_calendar or invalid_weekday:
            raise ValueError("Kalendermerkmale fehlen oder enthalten ungültige Werte.")
        if invalid_split or sum(split_counts.values()) != rows:
            raise ValueError("Ungültige train/test-Zuordnung.")
        if not split_counts.get("train") or not split_counts.get("test"):
            raise ValueError("Trainings- oder Testzeitraum fehlt im Korpus.")

        with temp_path.open("rb") as file:
            checksum = hashlib.file_digest(file, "sha256").hexdigest()

        # Nach der Validierung wird zuerst Parquet veröffentlicht.
        os.replace(temp_path, export_path)

        created_at = datetime.now(timezone.utc)
        manifest = {
            "version": f"{created_at:%Y%m%dT%H%M%SZ}-{checksum[:12]}",
            "created_at_utc": created_at.isoformat(),
            "parquet_file": export_path.name,
            "rows": int(rows),
            "missing_consumption": int(missing_consumption),
            "split_counts": {key: int(value) for key, value in split_counts.items()},
            "columns": columns,
            "sha256": checksum,
            "calendar": {
                "timezone_source": "site.iana_timezone",
                "local_weekday": "0=Montag, 6=Sonntag",
                "holidays": "US-Bundesfeiertage (beobachtete Ersatztermine eingeschlossen)",
                "train_year_local": 2016,
                "test_year_local": 2017,
            },
            "provenance": provenance or {},
        }
        temp_manifest.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_manifest, manifest_path)

        return {
            "rows": int(rows),
            "missing_consumption": int(missing_consumption),
            "missing_weather": int(missing_weather),
            "split_counts": manifest["split_counts"],
            "path": str(export_path),
            "manifest_path": str(manifest_path),
            "sha256": checksum,
        }

    finally:
        temp_path.unlink(missing_ok=True)
        temp_manifest.unlink(missing_ok=True)
