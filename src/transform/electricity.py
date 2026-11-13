"""BDG2-Wideformat -> Longformat mit FESTEM Quellzeitversatz.

Diese Funktion ist absichtlich BDG2-spezifisch. Für spätere UTC-Lieferungen
nach Data Contract ist keine Offset-Umrechnung erforderlich.
"""

import pandas as pd


def transform_electricity(
    electricity: pd.DataFrame,
    building_ids: list[str],
    source_utc_offset_hours: int,
) -> pd.DataFrame:
    columns = ["timestamp", *building_ids]
    df = electricity[columns].copy()
    timestamps = pd.to_datetime(df["timestamp"], errors="raise")
    if isinstance(timestamps.dtype, pd.DatetimeTZDtype):
        raise ValueError("BDG2-Adapter erwartet naive Quellzeitstempel")

    # UTC = lokale Quelluhrzeit - fester UTC-Versatz, z. B. Fox: 00:00 - (-7) = 07:00.
    timestamps_utc = timestamps - pd.Timedelta(hours=source_utc_offset_hours)
    df["timestamp"] = timestamps_utc.dt.tz_localize("UTC")

    return df.melt(
        id_vars="timestamp",
        var_name="building_id",
        value_name="consumption"
    )[["building_id", "timestamp", "consumption"]]
