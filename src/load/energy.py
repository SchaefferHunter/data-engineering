"""Stromverbrauch im vollständigen UTC-Stundenraster laden.

NaN/None in consumption bleibt erhalten und wird in DuckDB zu NULL.
"""

from pathlib import Path
import duckdb
import pandas as pd

from src.load.common import insert_frame, require_complete_hour_grid, require_utc


def load_energy(db_path: Path, energy: pd.DataFrame) -> None:
    columns = ["building_id", "timestamp", "consumption"]
    missing = set(columns) - set(energy.columns)
    if missing:
        raise ValueError(f"energy: fehlende Spalten {sorted(missing)}")
    require_utc(energy)
    df = energy[columns].copy()
    df["consumption"] = pd.to_numeric(df["consumption"], errors="raise")

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        building_sites = dict(con.execute(
            "SELECT building_id, site_id FROM building"
        ).fetchall())
    finally:
        con.close()

    if df[["building_id", "timestamp"]].isna().any().any():
        raise ValueError("energy: Gebäude-ID oder Zeitstempel fehlt")
    if df.duplicated(["building_id", "timestamp"]).any():
        raise ValueError("energy: doppelte Gebäude-Zeit-Schlüssel")
    require_complete_hour_grid(
        df, group_column="building_id", group_sites=building_sites
    )
    insert_frame(
        db_path, "energy", df, columns=columns,
        keys=["building_id", "timestamp"],
        references={"building_id": ("building", "building_id")},
    )
