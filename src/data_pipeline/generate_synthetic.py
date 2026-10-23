"""Generate synthetic vehicle, store-parameter and forecast data for the NCR dark stores."""
import numpy as np
import pandas as pd

from common import ROOT, data_path, effective_demand, load_config, vehicles_table


def haversine_km(lat1, lng1, lat2, lng2):
    lat1, lng1, lat2, lng2 = map(np.radians, (lat1, lng1, lat2, lng2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(a))


def build_store_params(stores: pd.DataFrame, cfg: dict, vehicles: pd.DataFrame, rng) -> pd.DataFrame:
    scfg = cfg["stores"]
    tiers = scfg["tiers"]
    names = list(tiers)
    n = len(stores)

    tier = rng.choice(names, size=n, p=[tiers[t]["share"] for t in names])
    capacity = np.array([tiers[t]["storage_capacity_crates"] for t in tier])
    lo, hi = scfg["on_hand_fraction"]
    on_hand = np.round(capacity * rng.uniform(lo, hi, n)).astype(int)

    rank = {t: i for i, t in enumerate(vehicles["type"])}
    max_vehicle = np.full(n, vehicles["type"].iloc[-1], dtype=object)
    zone = np.full(n, "none", dtype=object)
    for z in scfg["restricted_zones"]:
        inside = haversine_km(stores["lat"].values, stores["lng"].values, z["lat"], z["lng"]) <= z["radius_km"]
        tighter = inside & np.array([rank[z["max_vehicle"]] < rank[v] for v in max_vehicle])
        max_vehicle[tighter] = z["max_vehicle"]
        zone[tighter] = z["name"]

    return pd.DataFrame(
        {
            "store_id": stores["store_id"].values,
            "tier": tier,
            "storage_capacity_crates": capacity,
            "on_hand_crates": on_hand,
            "max_vehicle_type": max_vehicle,
            "restricted_zone": zone,
        }
    )


def build_forecasts(store_params: pd.DataFrame, cfg: dict, rng) -> dict[str, pd.DataFrame]:
    tiers = cfg["stores"]["tiers"]
    mean = store_params["tier"].map(lambda t: tiers[t]["mean_demand_crates"]).values
    cv = cfg["stores"]["demand_cv"]
    # lognormal with the tier mean and the configured coefficient of variation
    sigma = np.sqrt(np.log(1 + cv**2))
    base = rng.lognormal(np.log(mean) - sigma**2 / 2, sigma)

    forecasts = {}
    for name, sc in cfg["scenarios"].items():
        lo, hi = sc["multiplier"]
        f = np.maximum(1, np.round(base * rng.uniform(lo, hi, len(base)))).astype(int)
        forecasts[name] = pd.DataFrame({"store_id": store_params["store_id"].values, "forecast_crates": f})
    return forecasts


def main() -> None:
    cfg = load_config()
    rng = np.random.default_rng(cfg["project"]["seed"])
    stores = pd.read_csv(data_path(cfg, "processed_dir") / "stores_ncr.csv")
    vehicles = vehicles_table(cfg)

    out_dir = data_path(cfg, "synthetic_dir")
    fc_dir = out_dir / "forecasts"
    fc_dir.mkdir(exist_ok=True)

    vehicles.to_csv(out_dir / "vehicles.csv", index=False)
    store_params = build_store_params(stores, cfg, vehicles, rng)
    store_params.to_csv(out_dir / "store_params.csv", index=False)
    forecasts = build_forecasts(store_params, cfg, rng)
    for name, df in forecasts.items():
        df.to_csv(fc_dir / f"forecast_{name}.csv", index=False)

    fleet_cap = int((vehicles["capacity_crates"] * vehicles["available"]).sum())
    print(f"Stores: {len(store_params)} | tiers: {store_params['tier'].value_counts().to_dict()}")
    print(f"Max vehicle allowed: {store_params['max_vehicle_type'].value_counts().to_dict()}")
    print(f"Fleet capacity (all vehicles): {fleet_cap} crates")
    for name, fc in forecasts.items():
        d = effective_demand(store_params, fc, vehicles)
        status = "OK" if d["demand_crates"].sum() <= fleet_cap else "EXCEEDS FLEET"
        print(
            f"[{name:8s}] forecast={d['forecast_crates'].sum():6d}  deliverable={d['demand_crates'].sum():6d}  "
            f"unserved={d['unserved_crates'].sum():5d}  stores_space_limited={(d['unserved_crates'] > 0).sum():3d}  {status}"
        )
    print(f"Written to {out_dir.relative_to(ROOT)}")
