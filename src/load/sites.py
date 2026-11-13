"""Standortstammdaten laden, ohne BDG2-Zeitzonenfelder zu übernehmen."""

from pathlib import Path
import pandas as pd

from src.config import SITE_CONFIG
from src.load.common import insert_frame


def load_sites(db_path: Path, sites: pd.DataFrame) -> None:
    df = sites.rename(columns={"lat": "latitude", "long": "longitude"}).copy()
    for col in ("site_id", "latitude", "longitude"):
        if col not in df.columns:
            raise ValueError(f"site: fehlende Spalte {col}")

    unknown = set(df["site_id"].dropna()) - set(SITE_CONFIG)
    if unknown or df["site_id"].isna().any():
        raise ValueError(f"site: keine Zeitkonfiguration für {sorted(map(str, unknown))}")

    df["source_utc_offset_hours"] = df["site_id"].map(
        lambda site: SITE_CONFIG[site]["source_utc_offset_hours"]
    )
    df["iana_timezone"] = df["site_id"].map(
        lambda site: SITE_CONFIG[site]["iana_timezone"]
    )
    # Eine vorhandene BDG2-Spalte 'timezone' wird absichtlich NICHT geladen.
    df["latitude"] = pd.to_numeric(df["latitude"], errors="raise")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="raise")

    if not df["latitude"].between(-90, 90).all():
        raise ValueError("site: Breitengrad außerhalb [-90, 90] oder fehlend")
    if not df["longitude"].between(-180, 180).all():
        raise ValueError("site: Längengrad außerhalb [-180, 180] oder fehlend")

    columns = [
        "site_id", "latitude", "longitude",
        "source_utc_offset_hours", "iana_timezone"
    ]
    insert_frame(db_path, "site", df, columns=columns, keys=["site_id"], non_null=columns)
