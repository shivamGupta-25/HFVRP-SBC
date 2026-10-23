"""Filter the all-India Blinkit dark store file down to Delhi + core NCR."""
import pandas as pd

from common import ROOT, data_path, load_config


def main() -> None:
    cfg = load_config()
    raw = pd.read_csv(ROOT / cfg["paths"]["raw_stores"])
    r = cfg["region"]

    in_region = raw["lat"].between(r["lat_min"], r["lat_max"]) & raw["lng"].between(r["lng_min"], r["lng_max"])
    ncr = (
        raw[in_region]
        .drop_duplicates(subset="id")
        .drop_duplicates(subset=["lat", "lng"])
        .rename(columns={"id": "store_id", "accuracy": "location_accuracy"})
        .sort_values("store_id")
        .reset_index(drop=True)
    )[["store_id", "lat", "lng", "location_accuracy"]]

    out = data_path(cfg, "processed_dir") / "stores_ncr.csv"
    ncr.to_csv(out, index=False)
    print(f"Raw stores (India): {len(raw)}")
    print(f"Delhi-NCR stores:   {len(ncr)} -> {out.relative_to(ROOT)}")
