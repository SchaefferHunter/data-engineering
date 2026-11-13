"""Kleiner Integrations-/Abbruchtest mit 1 synthetischen Fox-Gebäude.

Aufruf im Projektroot: python tests/smoke_test.py
Die Dateien werden in einem temporären Verzeichnis erstellt.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb
import pandas as pd

from src.config import study_window_utc, EXPECTED_HOURS
from src.load.pipeline import rebuild_database


def main():
    start, end = study_window_utc("Fox")
    timestamps = pd.date_range(start, end, freq="h")
    assert len(timestamps) == EXPECTED_HOURS

    sites = pd.DataFrame({
        "site_id": ["Fox"], "lat": [33.424425], "long": [-111.92814],
        "timezone": ["US/Mountain"]  # wird bewusst ignoriert
    })
    buildings = pd.DataFrame({
        "building_id": ["Fox_test_1"], "site_id": ["Fox"],
        "primaryspaceusage": ["Office"], "sqm": [100.0]
    })
    energy = pd.DataFrame({
        "building_id": ["Fox_test_1"] * len(timestamps),
        "timestamp": timestamps,
        "consumption": [1.5] * (len(timestamps) - 1) + [float("nan")]
    })
    weather = pd.DataFrame({
        "site_id": ["Fox"] * len(timestamps), "timestamp": timestamps,
        "air_temperature": [10.0] * len(timestamps),
        "relative_humidity": [40.0] * len(timestamps),
        "shortwave_radiation": [0.0] * len(timestamps)
    })

    with TemporaryDirectory() as temp:
        target = Path(temp) / "corpus" / "energy.duckdb"
        result = rebuild_database(sites, buildings, energy, weather, target_path=target)
        assert result == {"site": 1, "building": 1, "energy": 17544, "weather": 17544}
        with duckdb.connect(str(target), read_only=True) as con:
            con.execute("SET TimeZone='UTC'")
            before = con.execute("SELECT COUNT(*), COUNT(consumption) FROM energy").fetchone()
        assert before == (17544, 17543), before

        bad_buildings = buildings.copy()
        bad_buildings.loc[0, "sqm"] = 0  # Validierung muss abbrechen
        try:
            rebuild_database(sites, bad_buildings, energy, weather, target_path=target)
        except ValueError:
            pass
        else:
            raise AssertionError("Ungültige Gebäudefläche wurde akzeptiert")

        with duckdb.connect(str(target), read_only=True) as con:
            after = con.execute("SELECT COUNT(*), COUNT(consumption) FROM energy").fetchone()
        assert after == before, "Die veröffentlichte DB wurde bei Fehler verändert"
        assert not list(target.parent.glob("*.building.duckdb")), "Stagingdatei übrig"
        print("OK: Neuaufbau erfolgreich; Abbruch erhält alte DB und entfernt Staging")


if __name__ == "__main__":
    main()
