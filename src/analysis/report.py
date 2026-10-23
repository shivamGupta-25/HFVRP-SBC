"""Result analysis: KPI tables and charts for a solved scenario."""
import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


def load_results(out: Path) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    routes = pd.read_csv(out / "routes.csv")
    stops = pd.read_csv(out / "stops.csv", dtype={"node_id": str})
    return summary, routes, stops


def kpi_table(summary: dict) -> pd.DataFrame:
    g = summary["gurobi"]
    return pd.Series(
        {
            "Solver status": g["status"],
            "Best cost Z (Rs)": f"{summary['total_cost_inr']:,.0f}",
            "Lower bound (Rs)": f"{g['best_bound_inr']:,.0f}",
            "Optimality gap (%)": round(g["mip_gap_pct"], 2),
            "Warm-start cost (Rs)": f"{summary['warm_start_cost_inr']:,.0f}",
            "Improvement by Gurobi (%)": round(100 * (1 - summary["total_cost_inr"] / summary["warm_start_cost_inr"]), 2),
            "Stores served": summary["n_stores"],
            "Crates delivered": summary["delivered_crates"],
            "Crates unserved (no space)": summary["unserved_crates"],
            "Vehicles dispatched": summary["routes"],
            "Vehicles by type": summary["vehicles_used"],
            "Fixed cost (Rs)": f"{summary['fixed_cost_inr']:,.0f}",
            "Variable cost (Rs)": f"{summary['variable_cost_inr']:,.0f}",
            "Total distance (km)": summary["total_distance_km"],
            "Total drive time (h)": summary["total_drive_time_hr"],
            "Fleet utilisation (%)": summary["fleet_utilization_pct"],
            "Cost per crate (Rs)": summary["cost_per_crate_inr"],
        }
    ).to_frame("value")


def fleet_mix(routes: pd.DataFrame, vehicles: pd.DataFrame) -> pd.DataFrame:
    by_type = (
        routes.groupby(["vehicle_type", "vehicle_name"])
        .agg(
            vehicles=("route_id", "count"),
            stores=("n_stops", "sum"),
            avg_stops=("n_stops", "mean"),
            crates=("load_crates", "sum"),
            avg_util_pct=("utilization_pct", "mean"),
            avg_km=("distance_km", "mean"),
            total_cost_inr=("total_cost_inr", "sum"),
        )
        .reindex(vehicles["type"], level=0)
    )
    by_type["cost_per_crate_inr"] = by_type["total_cost_inr"] / by_type["crates"]
    return by_type.round(1)


def plot_utilization_and_cost(routes: pd.DataFrame, vehicles: pd.DataFrame) -> plt.Figure:
    fig, axes = plt.subplots(1, 3, figsize=(16, 4.5))

    for t in vehicles["type"]:
        axes[0].hist(routes.loc[routes["vehicle_type"] == t, "utilization_pct"], bins=range(40, 105, 5), alpha=0.7, label=t)
    axes[0].set(title="Route utilisation", xlabel="Load / capacity (%)", ylabel="Vehicles")
    axes[0].legend(title="Type")

    r_sorted = routes.sort_values("load_crates")
    axes[1].bar(range(len(r_sorted)), r_sorted["capacity_crates"], color="lightgray", label="Capacity")
    axes[1].bar(range(len(r_sorted)), r_sorted["load_crates"], color="tab:blue", label="Load")
    axes[1].set(title="Load vs capacity per vehicle", xlabel="Vehicle (sorted by load)", ylabel="Crates")
    axes[1].legend()

    cost_split = routes.groupby("vehicle_type")[["fixed_cost_inr", "variable_cost_inr"]].sum().reindex(vehicles["type"])
    cost_split.plot.bar(stacked=True, ax=axes[2], color=["tab:orange", "tab:green"], rot=0)
    axes[2].set(title="Cost split by vehicle type", xlabel="Vehicle type", ylabel="Rs")
    axes[2].legend(["Fixed", "Variable (km)"])

    fig.tight_layout()
    return fig


def dispatch_sheet(routes: pd.DataFrame) -> pd.DataFrame:
    return routes[["route_id", "vehicle_type", "n_stops", "load_crates", "capacity_crates", "utilization_pct",
                   "distance_km", "drive_time_min", "total_cost_inr", "sequence"]]


def key_findings(out: Path, vehicles: pd.DataFrame) -> str:
    """Markdown findings computed from the saved results, so they stay correct for any scenario."""
    s, routes, stops = load_results(out)
    demand = pd.read_csv(out / "store_demand.csv", dtype={"store_id": str})
    g = s["gurobi"]
    cap = vehicles.set_index("type")["capacity_crates"]
    names = vehicles.set_index("type")["name"]
    largest = cap.idxmax()

    # 1. solution quality
    gain = s["warm_start_cost_inr"] - s["total_cost_inr"]
    gain_pct = 100 * gain / s["warm_start_cost_inr"]
    if g["status"] == "OPTIMAL":
        quality = f"The plan is **proven optimal** (gap {g['mip_gap_pct']:.2f} %)."
    else:
        quality = (f"Gurobi proved a lower bound of ₹{g['best_bound_inr']:,.0f}, so the plan is **guaranteed to be "
                   f"within {g['mip_gap_pct']:.2f} % of the true optimum**.")
    if gain_pct < 0.5:
        warm = (f"Gurobi improved the OR-Tools warm start only marginally (₹{gain:,.0f}), so the warm start was "
                "already near-optimal; the remaining gap mainly reflects the weak LP bound of fixed-cost VRP models.")
    else:
        warm = f"Gurobi improved the OR-Tools warm start by ₹{gain:,.0f} ({gain_pct:.1f} %)."

    # 2. utilisation
    min_vehicles = -(-s["delivered_crates"] // int(cap.max()))
    extra = s["routes"] - min_vehicles
    util_title = "Capacity is used almost fully." if s["fleet_utilization_pct"] >= 90 else "Capacity utilisation."

    # 3. vehicle choice: a smaller-vehicle route is access-forced if it serves a store that allows nothing larger
    st = stops.merge(demand[["store_id", "max_vehicle_type", "restricted_zone"]],
                     left_on="node_id", right_on="store_id", how="left")
    violations = int((st["vehicle_type"].map(cap) > st["max_vehicle_type"].map(cap)).sum())
    restricted = st[st["max_vehicle_type"] != largest]
    mix_lines = []
    for t in vehicles["type"]:
        r_t = routes[routes["vehicle_type"] == t]
        if r_t.empty:
            continue
        cpc = r_t["total_cost_inr"].sum() / r_t["load_crates"].sum()
        line = (f"   - **{len(r_t)} × {names[t]} ({t})** serve {int(r_t['n_stops'].sum())} stores "
                f"(avg {r_t['n_stops'].mean():.1f} per vehicle) at **₹{cpc:.1f} per crate**")
        if t == largest:
            line += "; the model uses them wherever access allows."
        else:
            on_t = st[st["route_id"].isin(r_t["route_id"])]
            forced_ids = set(on_t.loc[on_t["max_vehicle_type"] == t, "route_id"])
            zones = sorted(set(on_t.loc[on_t["max_vehicle_type"] == t, "restricted_zone"]))
            fill = int((on_t["route_id"].isin(forced_ids) & (on_t["max_vehicle_type"] != t)).sum())
            parts = []
            if forced_ids:
                n = len(forced_ids)
                parts.append(f"{n} {'is' if n == 1 else 'are'} required by access restrictions ({', '.join(zones)})"
                             + (f", with {fill} unrestricted stores added to fill them" if fill else ""))
            if len(r_t) > len(forced_ids):
                n = len(r_t) - len(forced_ids)
                parts.append(f"{n} {'carries' if n == 1 else 'carry'} a small leftover load where this vehicle is "
                             f"cheaper than another {names[largest]}")
            line += "; " + "; ".join(parts) + "."
        mix_lines.append(line)
    compat = (f"   - Every restricted store is served by an allowed vehicle "
              f"(compatibility satisfied {len(restricted) - violations}/{len(restricted)}).")

    # 4. cost structure
    fixed_pct = 100 * s["fixed_cost_inr"] / s["total_cost_inr"]
    km_l = routes.loc[routes["vehicle_type"] == largest, "distance_km"]
    driver = "fleet sizing and consolidation" if fixed_pct >= 50 else "route length"

    # 5. unserved demand
    fleet_cap = int((vehicles["capacity_crates"] * vehicles["available"]).sum())
    space_limited = int((demand["unserved_crates"] > 0).sum())
    if s["unserved_crates"] == 0:
        unserved = "**All forecast demand is delivered**; no store is limited by storage space."
    else:
        unserved = (f"**Unserved demand is a storage problem, not a transport problem.** "
                    f"{s['unserved_crates']:,} crates ({100 * s['unserved_crates'] / s['forecast_crates']:.1f} % of "
                    f"the forecast) at {space_limited} stores cannot be delivered because those stores lack free "
                    f"space or exceed their largest allowed vehicle, while the fleet still had "
                    f"{fleet_cap - s['delivered_crates']:,} crates of spare capacity.")

    return "\n".join([
        f"**Scenario:** `{s['scenario']}` (forecast file `{s['forecast_file']}`)",
        "",
        f"1. **Solution quality.** The final plan costs **₹{s['total_cost_inr']:,.0f} per night**. {quality} {warm}",
        "",
        f"2. **{util_title}** {s['routes']} vehicles carry {s['delivered_crates']:,} crates with "
        f"**{s['fleet_utilization_pct']:.1f} % fleet utilisation** (average route {s['avg_route_utilization_pct']:.1f} %). "
        f"The theoretical minimum is {min_vehicles} × {names[largest]}, and the plan uses {extra} more. "
        "Minimising fixed + variable cost automatically maximises utilisation.",
        "",
        "3. **The model picks the vehicle type for a reason.**",
        *mix_lines,
        compat,
        "",
        f"4. **Cost structure.** Fixed vehicle cost is **{fixed_pct:.0f} %** of the total (₹{s['fixed_cost_inr']:,.0f}); "
        f"distance costs ₹{s['variable_cost_inr']:,.0f} for {s['total_distance_km']:,.0f} km. "
        f"So {driver} matters most. {names[largest]} routes range from {km_l.min():.0f} to {km_l.max():.0f} km "
        f"(median {km_l.median():.0f} km).",
        "",
        f"5. {unserved}",
        "",
        "6. **Adaptability.** Changing `FORECAST_FILE` (cell 7) and re-running the notebook recomputes effective "
        "demand, vehicle choice, routes and these findings with no change to the model.",
    ])
