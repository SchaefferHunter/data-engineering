"""Wetterdaten im vollständigen UTC-Stundenraster laden."""

from pathlib import Path
import duckdb
import pandas as pd

from src.load.common import insert_frame, require_complete_hour_grid, require_utc


def load_weather(db_path: Path, weather: pd.DataFrame) -> None:
    columns = [
        "site_id", "timestamp", "air_temperature",
        "relative_humidity", "shortwave_radiation"
    ]
    missing = set(columns) - set(weather.columns)
    if missing:
        raise ValueError(f"weather: fehlende Spalten {sorted(missing)}")
    require_utc(weather)
    df = weather[columns].copy()

    for col in columns[2:]:
        df[col] = pd.to_numeric(df[col], errors="raise")
    if (~df["relative_humidity"].between(0, 100) & df["relative_humidity"].notna()).any():
        raise ValueError("weather: relative Luftfeuchtigkeit außerhalb [0, 100]")
    if (df["shortwave_radiation"] < 0).any():
        raise ValueError("weather: negative Globalstrahlung")

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        sites = {row[0] for row in con.execute("SELECT site_id FROM site").fetchall()}
    finally:
        con.close()

    if df[["site_id", "timestamp"]].isna().any().any():
        raise ValueError("weather: Standort-ID oder Zeitstempel fehlt")
    if df.duplicated(["site_id", "timestamp"]).any():
        raise ValueError("weather: doppelte Standort-Zeit-Schlüssel")
    require_complete_hour_grid(
        df, group_column="site_id", group_sites={site: site for site in sites}
    )
    insert_frame(
        db_path, "weather", df, columns=columns,
        keys=["site_id", "timestamp"],
        references={"site_id": ("site", "site_id")},
    )
