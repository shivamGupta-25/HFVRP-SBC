from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_config(path: Path = ROOT / "config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # secrets.yaml is git-ignored and holds API keys
    secrets = ROOT / "secrets.yaml"
    if secrets.exists():
        with open(secrets, encoding="utf-8") as f:
            cfg.update(yaml.safe_load(f) or {})
    return cfg


def data_path(cfg: dict, key: str) -> Path:
    p = ROOT / cfg["paths"][key]
    p.mkdir(parents=True, exist_ok=True)
    return p


def vehicles_table(cfg: dict) -> pd.DataFrame:
    return pd.DataFrame(cfg["vehicles"]).sort_values("capacity_crates").reset_index(drop=True)


def effective_demand(store_params: pd.DataFrame, forecast: pd.DataFrame, vehicles: pd.DataFrame) -> pd.DataFrame:
    """q_i = min(forecast_i, free storage_i, capacity of largest vehicle allowed at i)."""
    df = store_params.merge(forecast, on="store_id", how="left", validate="1:1")
    if df["forecast_crates"].isna().any():
        missing = df.loc[df["forecast_crates"].isna(), "store_id"].tolist()
        raise ValueError(f"Forecast missing for stores: {missing[:10]}")

    cap = vehicles.set_index("type")["capacity_crates"]
    df["free_space_crates"] = (df["storage_capacity_crates"] - df["on_hand_crates"]).clip(lower=0)
    df["max_vehicle_capacity"] = df["max_vehicle_type"].map(cap)
    df["demand_crates"] = np.minimum.reduce(
        [df["forecast_crates"], df["free_space_crates"], df["max_vehicle_capacity"]]
    ).astype(int)
    df["unserved_crates"] = (df["forecast_crates"] - df["demand_crates"]).astype(int)
    return df
