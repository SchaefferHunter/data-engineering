
import pandas as pd
import json
from pathlib import Path

def check_completeness(
    electricity: pd.DataFrame,
    building_ids: list[str],
    min_total: float = 0.95,
    min_year: float = 0.90
) -> pd.DataFrame:

    # Nur benötigte Daten auswählen
    data = electricity[["timestamp"] + building_ids].copy()

    # Jahr aus dem Zeitstempel extrahieren
    years = pd.to_datetime(data["timestamp"]).dt.year

    # Gesamtvollständigkeit pro Gebäude
    total = data[building_ids].notna().mean()

    # Vollständigkeit pro Gebäude und Jahr
    yearly = data[building_ids].notna().groupby(years).mean().T
    yearly.columns = [
        f"completeness_{year}" for year in yearly.columns
    ]

    # Qualitätsbericht erstellen
    report = pd.DataFrame({
        "completeness_total": total
    }).join(yearly)

    # Qualitätskriterien anwenden
    report["selected"] = (
        (report["completeness_total"] >= min_total)
        & (yearly >= min_year).all(axis=1)
    )

    return report.reset_index(names="building_id")



def validate_weather(df: pd.DataFrame) -> dict:

    required_columns = [
        "site_id",
        "timestamp",
        "air_temperature",
        "relative_humidity",
        "shortwave_radiation"
    ]

    # 1. Schema prüfen
    missing_columns = set(required_columns) - set(df.columns)

    if missing_columns:
        raise ValueError(f"Fehlende Spalten: {missing_columns}")

    if df.empty:
        raise ValueError("Der Wetterdatensatz ist leer.")

    # 2. UTC-Zeitstempel prüfen
    if (
        not isinstance(df["timestamp"].dtype, pd.DatetimeTZDtype)
        or str(df["timestamp"].dt.tz) != "UTC"
    ):
        raise ValueError("Zeitstempel müssen in UTC vorliegen.")

    # 3. Fehlende Werte zählen
    missing_values = df[required_columns].isna().sum().to_dict()

    # 4. Duplikate prüfen
    duplicates = df.duplicated(
        subset=["site_id", "timestamp"]
    ).sum()

    # 5. Stündliche Zeitabstände je Standort prüfen
    sorted_df = df.sort_values(["site_id", "timestamp"])

    intervals = (
        sorted_df.groupby("site_id")["timestamp"]
        .diff()
        .dropna()
    )

    invalid_intervals = (
        intervals != pd.Timedelta(hours=1)
    ).sum()

    # 6. Plausibilitätsprüfungen
    invalid_humidity = (
        df["relative_humidity"].notna()
        & ~df["relative_humidity"].between(0, 100)
    ).sum()

    negative_radiation = (
        df["shortwave_radiation"] < 0
    ).sum()

    return {
        "rows": len(df),
        "missing_values": missing_values,
        "duplicates": int(duplicates),
        "invalid_intervals": int(invalid_intervals),
        "invalid_humidity": int(invalid_humidity),
        "negative_radiation": int(negative_radiation)
    }


def validate_weather_coverage(
    df: pd.DataFrame,
    raw_path: Path
) -> dict:

    # Metadaten der archivierten API-Anfrage laden
    metadata_path = raw_path.with_name(
        raw_path.name.replace(".raw.json", ".metadata.json")
    )

    with open(metadata_path, "r", encoding="utf-8") as file:
        metadata = json.load(file)

    params = metadata["params"]

    if params["timezone"] != "UTC":
        raise ValueError("API-Zeitstempel müssen UTC sein.")

    # Erwartete Zeitachse aus dem Abrufzeitraum
    start = pd.Timestamp(params["start_date"], tz="UTC")
    end = pd.Timestamp(params["end_date"], tz="UTC")

    expected = pd.date_range(
        start=start,
        end=end + pd.Timedelta(hours=23),
        freq="h"
    )

    actual = pd.DatetimeIndex(df["timestamp"])

    missing = expected.difference(actual)
    unexpected = actual.difference(expected)

    return {
        "expected_hours": len(expected),
        "actual_hours": len(actual),
        "missing_timestamps": len(missing),
        "unexpected_timestamps": len(unexpected)
    }
