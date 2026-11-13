"""Einmaliger Vergleich: Laden derselben Daten mit/ohne PK/FK."""

from pathlib import Path
from time import perf_counter
import pandas as pd

from src.load.pipeline import _new_staging_path, _cleanup_stage, _crop_to_period
from src.load.database import create_database
from src.load.sites import load_sites
from src.load.buildings import load_buildings
from src.load.energy import load_energy
from src.load.weather import load_weather


def benchmark_load(
    sites: pd.DataFrame,
    buildings: pd.DataFrame,
    energy: pd.DataFrame,
    weather: pd.DataFrame,
    *,
    corpus_dir: Path | None = None,
) -> pd.DataFrame:
    if corpus_dir is None:
        corpus_dir = Path(__file__).resolve().parents[2] / "corpus"
    corpus_dir = Path(corpus_dir)
    corpus_dir.mkdir(parents=True, exist_ok=True)

    # Die identische Zuschneidung wird bei beiden Messungen verwendet.
    energy_cut, weather_cut = _crop_to_period(sites, buildings, energy, weather)
    results = []
    for enabled in (True, False):
        stage = _new_staging_path(corpus_dir / "benchmark.duckdb")
        try:
            # DDL und Zuschnitt bewusst NICHT mitstoppen.
            create_database(stage, with_constraints=enabled)
            start = perf_counter()
            load_sites(stage, sites)
            load_buildings(stage, buildings)
            load_energy(stage, energy_cut)
            load_weather(stage, weather_cut)
            duration = perf_counter() - start
            # Gleiche Python-Validierungen in beiden Messläufen.
            # Danach die resultierenden Zeilenzahlen kontrollieren.
            import duckdb
            con = duckdb.connect(str(stage), read_only=True)
            try:
                con.execute("SET TimeZone='UTC'")
                counts = {
                    table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in ("site", "building", "energy", "weather")
                }
            finally:
                con.close()
            expected = {
                "site": len(sites), "building": len(buildings),
                "energy": len(energy_cut), "weather": len(weather_cut)
            }
            if counts != expected:
                raise ValueError(f"Benchmark-Ladeprüfung fehlgeschlagen: {counts} != {expected}")
            results.append({
                "pk_fk_aktiv": enabled,
                "sekunden": round(duration, 3),
                "site": counts["site"],
                "building": counts["building"],
                "energy": counts["energy"],
                "weather": counts["weather"],
            })
        finally:
            _cleanup_stage(stage)
    return pd.DataFrame(results)
