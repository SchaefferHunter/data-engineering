"""Explizite BDG2-Zeitannahmen für die beiden Pilotstandorte.

BDG2 liefert naive Stundenlabels. Der *Quellversatz* ist NICHT dasselbe
wie die standortbezogene IANA-Zeitzone für spätere Kalendermerkmale.
"""

import pandas as pd

SITE_CONFIG = {
    "Fox": {
        "source_utc_offset_hours": -7,       # angenommene feste Quellzeit
        "iana_timezone": "America/Phoenix", # lokale Kalendermerkmale
    },
    "Hog": {
        "source_utc_offset_hours": -6,       # Arbeitshypothese: feste CST-Zeit
        "iana_timezone": "America/Chicago",
    },
}

LOCAL_START = "2016-01-01 00:00:00"
LOCAL_END = "2017-12-31 23:00:00"
EXPECTED_HOURS = 17_544


def study_window_utc(site_id: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Inklusive UTC-Grenzen des BDG2-Untersuchungszeitraums."""
    if site_id not in SITE_CONFIG:
        raise ValueError(f"Keine Quellzeit-Konfiguration für Standort {site_id!r}")
    offset = SITE_CONFIG[site_id]["source_utc_offset_hours"]
    # Die naiven Quell-Labels werden als Zeit auf einer festen Offset-Uhr
    # interpretiert: UTC = lokale Quellzeit - Offset.
    start = pd.Timestamp(LOCAL_START, tz="UTC") - pd.Timedelta(hours=offset)
    end = pd.Timestamp(LOCAL_END, tz="UTC") - pd.Timedelta(hours=offset)
    assert len(pd.date_range(start, end, freq="h")) == EXPECTED_HOURS
    return start, end
