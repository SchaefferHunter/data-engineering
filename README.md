# Datenkorpus für die Lastprognose von Gebäuden

Batch-ETL-Pipeline aus der Studienarbeit im Modul *Data Engineering* (OTH Regensburg).
Sie führt stündliche Stromverbrauchsdaten des Standorts **Fox** aus dem
Building Data Genome Project 2 (BDG2) mit Wetterdaten von Open-Meteo (ERA5)
zusammen. Das Ergebnis ist eine DuckDB-Datenbank und ein versionierter
Parquet-Trainingskorpus.

## Installation

```bash
pip install -r requirements.txt
```

Getestet mit Python 3.12 unter Windows 11.

## Eingabedaten

| Datei | Herkunft | Versioniert |
|---|---|---|
| `data/electricity.csv`, `data/metadata.csv`, `data/weather.csv` | BDG2, separat herunterladen | nein (groß) |
| `data/raw/open_meteo/Fox/*.raw.json` + `*.metadata.json` | Open-Meteo-Archivabruf mit SHA-256 | ja |

Ein neuer Wetterabruf erfolgt bewusst getrennt von der Pipeline über
`src/extract/open_meteo.py::extract_weather`.

## Ausführung

```bash
python main.py --site Fox --weather-raw data/raw/open_meteo/Fox/20261112T134659694282Z.raw.json
```

Ergebnisse:

| Datei | Inhalt |
|---|---|
| `corpus/energy.duckdb` | normalisierte Tabellen `site`, `building`, `energy`, `weather` |
| `corpus/training_corpus.parquet` | Trainingstabelle mit Kalendermerkmalen und Spalte `split` (`train` = 2016, `test` = 2017) |
| `corpus/training_corpus.manifest.json` | Release-Manifest: Version, Zeilenzahlen, SHA-256, Eingabe-Prüfsummen, Git-Commit |
| `output/quality_report.json` | Qualitätsbericht (Auswahl, Lücken, Nullphasen, Wetter) |
| `output/logs/run_*.log` | Laufprotokoll je Ausführung, auch im Fehlerfall |

Datenbank und Export werden bei jedem Lauf vollständig neu aufgebaut und erst
nach erfolgreicher Prüfung atomar ersetzt. Mit den Versionen aus
`requirements.txt` entsteht ein byte-identischer Parquet-Export.

## Tests und Benchmark

```bash
python -m tests.test_units     # Unit-Tests (Zeitversatz, Wide->Long, Auswahlregeln)
python -m tests.smoke_test     # Integrationstest: Neuaufbau und Abbruchverhalten
python -m src.load.benchmark --site Fox --weather-raw data/raw/open_meteo/Fox/20261112T134659694282Z.raw.json --repeats 5
```

## Projektstruktur

```
main.py              Einstiegspunkt der Pipeline
src/config.py        Zeitannahmen je Standort (Quellversatz, IANA-Zeitzone)
src/extract/         Open-Meteo-Abruf mit Archivierung
src/transform/       Standorte, Gebäude, Strom (Wide->Long, UTC), Wetter
src/validation/      Auswahlregeln, Wetterprüfung, Qualitätsbericht
src/load/            Schema, Loader, Neuaufbau, Parquet-Export, Benchmark
tests/               Unit- und Integrationstests
notebooks/           Data Profiling und Entwicklungsprotokoll
```

## Datenquellen

- Miller et al. (2020): *The Building Data Genome Project 2*, Scientific Data 7, 368.
- Open-Meteo Historical Weather API (ERA5-Reanalyse), Lizenz CC BY 4.0.
