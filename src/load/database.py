"""DDL: immer eine neue Datenbankdatei erstellen, kein IF NOT EXISTS."""

from pathlib import Path
import duckdb


def create_database(db_path: Path, *, with_constraints: bool = True) -> Path:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if db_path.exists():
        raise FileExistsError(f"Vorläufige Datenbank existiert bereits: {db_path}")

    pk_site = ", PRIMARY KEY (site_id)" if with_constraints else ""
    pk_building = ", PRIMARY KEY (building_id)" if with_constraints else ""
    pk_energy = ", PRIMARY KEY (building_id, timestamp)" if with_constraints else ""
    pk_weather = ", PRIMARY KEY (site_id, timestamp)" if with_constraints else ""
    fk_building = ", FOREIGN KEY (site_id) REFERENCES site(site_id)" if with_constraints else ""
    fk_energy = ", FOREIGN KEY (building_id) REFERENCES building(building_id)" if with_constraints else ""
    fk_weather = ", FOREIGN KEY (site_id) REFERENCES site(site_id)" if with_constraints else ""

    con = duckdb.connect(str(db_path))
    try:
        con.execute("SET TimeZone = 'UTC'")
        # Reihenfolge: site -> building -> energy -> weather
        con.execute(f"""
            CREATE TABLE site (
                site_id VARCHAR NOT NULL,
                latitude DOUBLE NOT NULL,
                longitude DOUBLE NOT NULL,
                source_utc_offset_hours SMALLINT NOT NULL,
                iana_timezone VARCHAR NOT NULL
                {pk_site}
            )
        """)
        con.execute(f"""
            CREATE TABLE building (
                building_id VARCHAR NOT NULL,
                site_id VARCHAR NOT NULL,
                primaryspaceusage VARCHAR,
                sqm DOUBLE NOT NULL
                {pk_building}{fk_building}
            )
        """)
        con.execute(f"""
            CREATE TABLE energy (
                building_id VARCHAR NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL,
                consumption DOUBLE
                {pk_energy}{fk_energy}
            )
        """)
        con.execute(f"""
            CREATE TABLE weather (
                site_id VARCHAR NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL,
                air_temperature DOUBLE,
                relative_humidity DOUBLE,
                shortwave_radiation DOUBLE
                {pk_weather}{fk_weather}
            )
        """)
    finally:
        con.close()

    return db_path
