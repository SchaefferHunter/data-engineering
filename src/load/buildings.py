"""Gebäude mit expliziter FK- und Flächenprüfung laden."""

from pathlib import Path
import pandas as pd

from src.load.common import insert_frame


def load_buildings(db_path: Path, buildings: pd.DataFrame) -> None:
    columns = ["building_id", "site_id", "primaryspaceusage", "sqm"]
    if "sqm" not in buildings.columns:
        raise ValueError("building: Spalte sqm fehlt")
    df = buildings.copy()
    df["sqm"] = pd.to_numeric(df["sqm"], errors="raise")
    if not (df["sqm"] > 0).all():
        raise ValueError("building: Gebäudefläche muss > 0 sein")

    insert_frame(
        db_path, "building", df,
        columns=columns,
        keys=["building_id"],
        non_null=["site_id", "sqm"],
        references={"site_id": ("site", "site_id")},
    )
