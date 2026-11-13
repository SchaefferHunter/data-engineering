"""Gemeinsame Prüf- und INSERT-Strategie aller vier Loader."""

from pathlib import Path
import duckdb
import pandas as pd


def require_utc(df: pd.DataFrame) -> None:
    dtype = df["timestamp"].dtype
    if not isinstance(dtype, pd.DatetimeTZDtype) or str(df["timestamp"].dt.tz) != "UTC":
        raise ValueError("timestamp muss timezone-aware und UTC sein")


def insert_frame(
    db_path: Path,
    table: str,
    frame: pd.DataFrame,
    *,
    columns: list[str],
    keys: list[str],
    non_null: list[str] | None = None,
    references: dict[str, tuple[str, str]] | None = None,
) -> None:
    """Prüft Schema/Schlüssel/Referenzen und führt genau ein INSERT aus.

    table/columns/references werden nur aus dem projektierten Code übergeben,
    nicht aus Nutzereingaben (SQL-Identifier sind nicht parametrisierbar).
    """
    missing = set(columns) - set(frame.columns)
    if missing:
        raise ValueError(f"{table}: fehlende Spalten {sorted(missing)}")
    df = frame[columns].copy()
    if df.empty:
        raise ValueError(f"{table}: leerer Datensatz")

    required = sorted(set(keys) | set(non_null or []))
    if df[required].isna().any().any():
        raise ValueError(f"{table}: fehlende Schlüssel-/Pflichtwerte in {required}")
    if df.duplicated(subset=keys).any():
        raise ValueError(f"{table}: doppelte Schlüssel {keys}")

    con = duckdb.connect(str(db_path))
    try:
        con.execute("SET TimeZone = 'UTC'")
        for incoming_col, (parent_table, parent_col) in (references or {}).items():
            valid_ids = {row[0] for row in con.execute(
                f"SELECT {parent_col} FROM {parent_table}"
            ).fetchall()}
            unknown = set(df[incoming_col].unique()) - valid_ids
            if unknown:
                raise ValueError(
                    f"{table}: unbekannte Referenzen in {incoming_col}: "
                    f"{sorted(map(str, unknown))[:5]}"
                )

        con.register("incoming_batch", df)
        try:
            field_names = ", ".join(columns)
            con.execute(
                f"INSERT INTO {table} ({field_names}) "
                f"SELECT {field_names} FROM incoming_batch"
            )
        finally:
            con.unregister("incoming_batch")
    finally:
        con.close()


def require_complete_hour_grid(
    df: pd.DataFrame,
    *,
    group_column: str,
    group_sites: dict[str, str],
) -> None:
    """Prüft exakt 17544 unterschiedliche volle Stunden je Gruppe.

    Eindeutigkeit wird zusätzlich vor INSERT über den zusammengesetzten
    Schlüssel geprüft. Anzahl + Grenzen + Stundenraster implizieren dann
    lückenlose Abdeckung.
    """
    from src.config import EXPECTED_HOURS, study_window_utc

    require_utc(df)
    actual_groups = set(df[group_column].dropna().unique())
    required_groups = set(group_sites)
    if actual_groups != required_groups:
        raise ValueError(
            f"{group_column}: Gruppen fehlen/unerwartet: "
            f"fehlend={sorted(required_groups - actual_groups)}, "
            f"unerwartet={sorted(actual_groups - required_groups)}"
        )

    for name, part in df.groupby(group_column, sort=False):
        start, end = study_window_utc(group_sites[name])
        stamps = part["timestamp"]
        if len(part) != EXPECTED_HOURS:
            raise ValueError(f"{name}: {len(part)} statt {EXPECTED_HOURS} Stunden")
        if stamps.min() != start or stamps.max() != end:
            raise ValueError(f"{name}: falscher Zeitraum {stamps.min()} bis {stamps.max()}")
        if ((stamps - start) % pd.Timedelta(hours=1) != pd.Timedelta(0)).any():
            raise ValueError(f"{name}: Zeitstempel nicht im vollen Stundenraster")
        # In Kombination mit Duplikatprüfung und korrekter Zeilenanzahl ist
        # somit jede erwartete Stunde exakt einmal vorhanden.
