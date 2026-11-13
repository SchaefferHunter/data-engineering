import pandas as pd

def transform_buildings(
    metadata: pd.DataFrame,
    building_ids: list[str]
) -> pd.DataFrame:

    # 1. Nur die ausgewählten Gebäude filtern
    metadata_filtered = metadata[
        metadata["building_id"].isin(building_ids)
    ]

    # 2. Nur die benötigten Spalten auswählen
    columns = ["building_id", "site_id", "primaryspaceusage", "sqm"]

    # 3. Ergebnis zurückgeben
    return metadata_filtered[columns].copy()