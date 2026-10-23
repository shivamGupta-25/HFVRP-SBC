"""Model instance (nodes, demand, distances, fleet, compatibility) and solution reporting."""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from common import ROOT, effective_demand, vehicles_table


@dataclass
class Instance:
    node_ids: list[str]          # index 0 = depot
    nodes: pd.DataFrame          # node_id, lat, lng (model order)
    demand: pd.DataFrame         # per-store effective demand table
    q: np.ndarray                # crates per node, q[0] = 0
    dist: np.ndarray             # km, asymmetric
    dur: np.ndarray              # minutes, asymmetric
    vehicles: pd.DataFrame       # sorted by capacity
    compat: np.ndarray           # (nodes x types) bool, depot row all True
    forecast_file: str


def _matrix(path, ids):
    m = pd.read_csv(path, index_col=0)
    m.index = m.index.astype(str)
    m.columns = m.columns.astype(str)
    return m.loc[ids, ids].to_numpy(dtype=float)


def load_instance(cfg: dict, forecast_file: str | None = None) -> Instance:
    proc = ROOT / cfg["paths"]["processed_dir"]
    syn = ROOT / cfg["paths"]["synthetic_dir"]
    forecast_file = forecast_file or cfg["model"]["forecast_file"]

    vehicles = vehicles_table(cfg)
    store_params = pd.read_csv(syn / "store_params.csv")
    forecast = pd.read_csv(ROOT / forecast_file)
    demand = effective_demand(store_params, forecast, vehicles)
    demand = demand[demand["demand_crates"] > 0].reset_index(drop=True)

    ids = [cfg["depot"]["id"]] + demand["store_id"].astype(str).tolist()
    nodes = pd.read_csv(proc / "nodes.csv", dtype={"node_id": str}).set_index("node_id").loc[ids].reset_index()

    cap = vehicles["capacity_crates"].to_numpy()
    store_max = demand["max_vehicle_type"].map(vehicles.set_index("type")["capacity_crates"]).to_numpy()
    max_cap = np.r_[cap.max(), store_max]

    return Instance(
        node_ids=ids,
        nodes=nodes[["node_id", "lat", "lng"]],
        demand=demand,
        q=np.r_[0, demand["demand_crates"].to_numpy()].astype(int),
        dist=_matrix(proc / "distance_km.csv", ids),
        dur=_matrix(proc / "duration_min.csv", ids),
        vehicles=vehicles,
        compat=cap[None, :] <= max_cap[:, None],
        forecast_file=str(forecast_file),
    )


def route_table(inst: Instance, routes: list[dict]) -> pd.DataFrame:
    """One row per route. A route is {"type": k, "stops": [node, ...]} (depot excluded)."""
    rows = []
    for r, route in enumerate(routes, start=1):
        veh = inst.vehicles.iloc[route["type"]]
        path = [0, *route["stops"], 0]
        km = sum(inst.dist[a, b] for a, b in zip(path, path[1:]))
        mins = sum(inst.dur[a, b] for a, b in zip(path, path[1:]))
        load = int(inst.q[route["stops"]].sum())
        rows.append(
            {
                "route_id": r,
                "vehicle_type": veh["type"],
                "vehicle_name": veh["name"],
                "n_stops": len(route["stops"]),
                "load_crates": load,
                "capacity_crates": int(veh["capacity_crates"]),
                "utilization_pct": round(100 * load / veh["capacity_crates"], 1),
                "distance_km": round(km, 2),
                "drive_time_min": round(mins, 1),
                "fixed_cost_inr": float(veh["fixed_cost_inr"]),
                "variable_cost_inr": round(km * veh["cost_per_km_inr"], 2),
                "sequence": " > ".join(inst.node_ids[i] for i in route["stops"]),
            }
        )
    df = pd.DataFrame(rows)
    df["total_cost_inr"] = df["fixed_cost_inr"] + df["variable_cost_inr"]
    return df


def stop_table(inst: Instance, routes: list[dict]) -> pd.DataFrame:
    """One row per store visit, in visiting order, with the load on board when arriving."""
    rows = []
    for r, route in enumerate(routes, start=1):
        on_board = int(inst.q[route["stops"]].sum())
        for seq, i in enumerate(route["stops"], start=1):
            rows.append(
                {
                    "route_id": r,
                    "vehicle_type": inst.vehicles.iloc[route["type"]]["type"],
                    "stop_seq": seq,
                    "node_id": inst.node_ids[i],
                    "lat": inst.nodes.at[i, "lat"],
                    "lng": inst.nodes.at[i, "lng"],
                    "demand_crates": int(inst.q[i]),
                    "load_on_arrival": on_board,
                }
            )
            on_board -= int(inst.q[i])
    return pd.DataFrame(rows)
