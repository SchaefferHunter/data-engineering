"""Vergleich: Laden derselben Daten mit/ohne PK/FK, mehrfach wiederholt.

Aufruf im Projektverzeichnis (außerhalb von Jupyter messen):
python -m src.load.benchmark --site Fox --weather-raw data/raw/open_meteo/Fox/<abruf>.raw.json --repeats 5
"""

import argparse
from pathlib import Path
from time import perf_counter

import duckdb
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
    repeats: int = 1,
) -> pd.DataFrame:
    if repeats < 1:
        raise ValueError("repeats muss mindestens 1 sein")
    if corpus_dir is None:
        corpus_dir = Path(__file__).resolve().parents[2] / "corpus"
    corpus_dir = Path(corpus_dir)
    corpus_dir.mkdir(parents=True, exist_ok=True)

    # Die identische Zuschneidung wird bei allen Messungen verwendet.
    energy_cut, weather_cut = _crop_to_period(sites, buildings, energy, weather)
    expected = {
        "site": len(sites), "building": len(buildings),
        "energy": len(energy_cut), "weather": len(weather_cut),
    }
    results = []
    for run in range(1, repeats + 1):
        # Abwechselnde Reihenfolge, damit Cache-Effekte nicht einseitig wirken.
        order = (True, False) if run % 2 else (False, True)
        for enabled in order:
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
                # Gleiche Python-Validierungen in allen Messläufen.
                # Danach die resultierenden Zeilenzahlen kontrollieren.
                with duckdb.connect(str(stage), read_only=True) as con:
                    counts = {
                        table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                        for table in ("site", "building", "energy", "weather")
                    }
                if counts != expected:
                    raise ValueError(
                        f"Benchmark-Ladeprüfung fehlgeschlagen: {counts} != {expected}"
                    )
                results.append({
                    "lauf": run,
                    "pk_fk_aktiv": enabled,
                    "sekunden": round(duration, 3),
                    **counts,
                })
            finally:
                _cleanup_stage(stage)
    return pd.DataFrame(results)


def summarize(results: pd.DataFrame) -> pd.DataFrame:
    """Median, Minimum und Maximum je Variante."""
    return (
        results.groupby("pk_fk_aktiv")["sekunden"]
        .agg(laeufe="count", median="median", minimum="min", maximum="max")
        .reset_index()
    )


def main() -> None:
    # Import hier, damit main.py nicht beim Import dieses Moduls geladen wird.
    from main import ROOT, prepare

    parser = argparse.ArgumentParser(description="Ladezeit mit/ohne PK/FK messen")
    parser.add_argument("--site", required=True)
    parser.add_argument("--weather-raw", required=True, type=Path)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    raw_path = args.weather_raw if args.weather_raw.is_absolute() else ROOT / args.weather_raw

    data = prepare(args.site, raw_path.resolve())
    results = benchmark_load(
        data["sites"], data["buildings"], data["energy"], data["weather"],
        repeats=args.repeats,
    )
    summary = summarize(results)

    output_dir = ROOT / "output"
    output_dir.mkdir(exist_ok=True)
    results.to_csv(output_dir / "duckdb_benchmark.csv", index=False)
    summary.to_csv(output_dir / "duckdb_benchmark_summary.csv", index=False)
    print(results.to_string(index=False))
    print()
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
