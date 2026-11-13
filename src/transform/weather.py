
import json
import pandas as pd
from pathlib import Path


def transform_weather(raw_path: Path) -> pd.DataFrame:

    # 1. Zugehörige Metadatendatei bestimmen
    metadata_path = raw_path.with_name(
        raw_path.name.replace(".raw.json", ".metadata.json")
    )

    # 2. Rohdaten und Metadaten einlesen
    with open(raw_path, "r", encoding="utf-8") as file:
        data = json.load(file)

    with open(metadata_path, "r", encoding="utf-8") as file:
        metadata = json.load(file)

    # 3. Standort aus Metadaten übernehmen
    site_id = metadata["site_id"]

    # 4. Stündliche Wetterdaten transformieren
    df = pd.DataFrame(data["hourly"])

    df = df.rename(columns={
        "time": "timestamp",
        "temperature_2m": "air_temperature",
        "relative_humidity_2m": "relative_humidity"
    })

    df["timestamp"] = pd.to_datetime(
        df["timestamp"],
        utc=True
    )

    df.insert(0, "site_id", site_id)

    return df[
        [
            "site_id",
            "timestamp",
            "air_temperature",
            "relative_humidity",
            "shortwave_radiation"
        ]
    ]
