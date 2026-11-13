import pandas as pd


def transform_sites(
    metadata: pd.DataFrame,
    site_ids: list[str]
) -> pd.DataFrame:
    columns = ["site_id", "lat", "lng", "timezone"]

    sites_filtered = metadata.loc[
        metadata["site_id"].isin(site_ids), columns
    ].copy()

    # Mehrere Gebäude eines Standorts zusammenfassen
    sites_transformed = sites_filtered.groupby(
        "site_id", as_index=False
    ).agg({
        "lat": "median",
        "lng": "median",
        "timezone": "first"
    })

    return sites_transformed.rename(
        columns={"lng": "long"}
    )[["site_id", "lat", "long", "timezone"]]
