"""Save the solved plan (routes, stops, demand, KPI summary) to outputs/<scenario>/."""
import json
from pathlib import Path

from common import ROOT
from solver.instance import Instance, route_table, stop_table


def scenario_dir(cfg: dict, inst: Instance) -> Path:
    tag = Path(inst.forecast_file).stem.removeprefix("forecast_")
    out = ROOT / cfg["solver"]["output_dir"] / tag
    out.mkdir(parents=True, exist_ok=True)
    return out


def save_results(inst: Instance, out: Path, start_routes: list[dict], routes: list[dict],
                 gurobi_info: dict, ortools_runtime_sec: float) -> dict:
    rt = route_table(inst, routes)
    rt.to_csv(out / "routes.csv", index=False)
    stop_table(inst, routes).to_csv(out / "stops.csv", index=False)
    inst.demand.to_csv(out / "store_demand.csv", index=False)

    total_demand = int(inst.q.sum())
    summary = {
        "scenario": out.name,
        "forecast_file": inst.forecast_file,
        "n_stores": len(inst.q) - 1,
        "forecast_crates": int(inst.demand["forecast_crates"].sum()),
        "delivered_crates": total_demand,
        "unserved_crates": int(inst.demand["unserved_crates"].sum()),
        "routes": len(rt),
        "vehicles_used": rt["vehicle_type"].value_counts().reindex(inst.vehicles["type"], fill_value=0).to_dict(),
        "total_cost_inr": round(rt["total_cost_inr"].sum(), 2),
        "fixed_cost_inr": round(rt["fixed_cost_inr"].sum(), 2),
        "variable_cost_inr": round(rt["variable_cost_inr"].sum(), 2),
        "total_distance_km": round(rt["distance_km"].sum(), 2),
        "total_drive_time_hr": round(rt["drive_time_min"].sum() / 60, 2),
        "fleet_utilization_pct": round(100 * total_demand / rt["capacity_crates"].sum(), 2),
        "avg_route_utilization_pct": round(rt["utilization_pct"].mean(), 2),
        "cost_per_crate_inr": round(rt["total_cost_inr"].sum() / total_demand, 2),
        "warm_start_cost_inr": round(route_table(inst, start_routes)["total_cost_inr"].sum(), 2),
        "ortools_runtime_sec": round(ortools_runtime_sec, 1),
        "gurobi": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in gurobi_info.items()},
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
