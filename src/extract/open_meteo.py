
import requests
import json
import hashlib

from pathlib import Path
from datetime import datetime, timezone


def extract_weather(
    latitude: float,
    longitude: float,
    start_date: str,
    end_date: str,
    *,
    site_id: str
) -> Path:

    url = "https://archive-api.open-meteo.com/v1/archive"

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": [
            "temperature_2m",
            "relative_humidity_2m",
            "shortwave_radiation"
        ],
        "models": "era5",
        "timezone": "UTC"
    }

    # 1. API-Anfrage
    response = requests.get(url, params=params, timeout=60)
    response.raise_for_status()

    # Prüfen, ob die Antwort gültiges JSON enthält
    response.json()

    # 2. Projektverzeichnis ermitteln
    project_root = Path(__file__).resolve().parents[2]

    raw_dir = project_root / "data" / "raw" / "open_meteo" / site_id
    raw_dir.mkdir(parents=True, exist_ok=True)

    # 3. Eindeutige Kennung für den Abruf
    fetched_at = datetime.now(timezone.utc)
    run_id = fetched_at.strftime("%Y%m%dT%H%M%S%fZ")

    # 4. Originalantwort archivieren
    raw_bytes = response.content
    raw_path = raw_dir / f"{run_id}.raw.json"

    raw_path.write_bytes(raw_bytes)

    # 5. SHA-256-Prüfsumme berechnen
    checksum = hashlib.sha256(raw_bytes).hexdigest()

    # 6. Metadaten dokumentieren
    metadata = {
        "site_id": site_id,
        "url": response.url,
        "params": params,
        "fetched_at": fetched_at.isoformat(),
        "sha256": checksum
    }

    metadata_path = raw_dir / f"{run_id}.metadata.json"

    metadata_path.write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8"
    )

    # 7. Pfad zur archivierten Rohdatei zurückgeben
    return raw_path
