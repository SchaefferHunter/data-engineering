"""Unit-Tests für die zentralen Transformations- und Auswahlregeln.

Aufruf im Projektverzeichnis: python -m tests.test_units
(oder mit pytest: python -m pytest tests/test_units.py)
"""

import pandas as pd

from src.config import EXPECTED_HOURS, study_window_utc
from src.transform.electricity import transform_electricity
from src.validation.quality import check_completeness


def test_utc_offset_conversion():
    """Fox: Quellzeit 00:00 bei festem UTC-7 entspricht 07:00 UTC."""
    raw = pd.DataFrame({
        "timestamp": ["2016-01-01 00:00:00", "2016-07-01 12:00:00"],
        "Fox_test_A": [1.0, 2.0],
    })
    result = transform_electricity(raw, ["Fox_test_A"], source_utc_offset_hours=-7)
    assert list(result.columns) == ["building_id", "timestamp", "consumption"]
    assert str(result["timestamp"].dt.tz) == "UTC"
    # Fester Versatz: auch im Sommer keine Sommerzeitverschiebung
    expected = pd.to_datetime(
        ["2016-01-01 07:00:00", "2016-07-01 19:00:00"]
    ).tz_localize("UTC")
    assert (result["timestamp"].values == expected.values).all()


def test_wide_to_long_keeps_missing_values():
    """Wide -> Long: eine Zeile je Gebäude und Stunde, NaN bleibt erhalten."""
    raw = pd.DataFrame({
        "timestamp": ["2016-01-01 00:00:00", "2016-01-01 01:00:00"],
        "Fox_a": [1.0, None],
        "Fox_b": [3.0, 4.0],
    })
    result = transform_electricity(raw, ["Fox_a", "Fox_b"], source_utc_offset_hours=-7)
    assert len(result) == 4
    assert result["consumption"].isna().sum() == 1


def test_already_aware_timestamps_are_rejected():
    """Doppelte Umrechnung verhindern: zeitzonenbehaftete Eingaben abweisen."""
    raw = pd.DataFrame({
        "timestamp": pd.to_datetime(["2016-01-01 00:00:00"]).tz_localize("UTC"),
        "Fox_a": [1.0],
    })
    try:
        transform_electricity(raw, ["Fox_a"], source_utc_offset_hours=-7)
    except ValueError:
        return
    raise AssertionError("Zeitzonenbehaftete Zeitstempel wurden akzeptiert")


def test_completeness_rules():
    """95 % gesamt UND 90 % je Jahr; eine Jahreslücke führt zum Ausschluss."""
    stamps = pd.date_range("2016-01-01", "2017-12-31 23:00", freq="h")
    n = len(stamps)
    is_2016 = stamps.year == 2016
    df = pd.DataFrame({"timestamp": stamps})
    df["full"] = 1.0
    df["year_gap"] = [None if y else 1.0 for y in is_2016]   # 2016 komplett leer
    gap = [1.0] * n
    for i in range(0, int(n * 0.06)):                         # 6 % fehlen am Stück
        gap[i] = None
    df["six_percent_gap"] = gap

    report = check_completeness(df, ["full", "year_gap", "six_percent_gap"])
    selected = dict(zip(report["building_id"], report["selected"]))
    assert selected == {"full": True, "year_gap": False, "six_percent_gap": False}


def test_study_window_has_expected_hours():
    start, end = study_window_utc("Fox")
    assert str(start) == "2016-01-01 07:00:00+00:00"
    assert str(end) == "2018-01-01 06:00:00+00:00"
    assert len(pd.date_range(start, end, freq="h")) == EXPECTED_HOURS


if __name__ == "__main__":
    tests = [obj for name, obj in dict(globals()).items() if name.startswith("test_")]
    for test in tests:
        test()
        print("OK:", test.__name__)
    print(f"{len(tests)} Tests erfolgreich")
