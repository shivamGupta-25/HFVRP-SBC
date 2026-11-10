"""Scaling study: solve the same model on nested random subsets of stores (e.g. 50, 100, 200, 361)
to show how the optimality gap and solve time grow with instance size."""
import json
import time
from pathlib import Path

import pandas as pd

from common import ROOT
from solver.instance import load_instance
from solver.results import save_results
from solver.solve_gurobi import solve_gurobi
from solver.solve_ortools import solve_ortools


def run_scaling_study(cfg: dict, forecast_file: str | None = None, rerun: bool = False) -> pd.DataFrame:
    """Solves each size once and caches it in outputs/scaling/<scenario>_n<size>/; rerun=True re-solves."""
    scfg, study = cfg["solver"], cfg["scaling_study"]
    gcfg = {**scfg["gurobi"], "time_limit_sec": study["gurobi_time_limit_sec"], "mip_gap": study["mip_gap"]}
    base = ROOT / scfg["output_dir"] / "scaling"

    rows = []
    for n in study["store_counts"]:
        inst = load_instance(cfg, forecast_file, n_stores=n)
        n_stores = len(inst.q) - 1
        scenario = Path(inst.forecast_file).stem.removeprefix("forecast_")
        out = base / f"{scenario}_n{n_stores}"

        if rerun or not (out / "summary.json").exists():
            out.mkdir(parents=True, exist_ok=True)
            print(f"\n=== Scaling study: {n_stores} stores ({scenario}) ===", flush=True)
            t0 = time.perf_counter()
            start = solve_ortools(inst, study["ortools_time_limit_sec"], first_solution=scfg["ortools_first_solution"])
            t_or = time.perf_counter() - t0
            routes, info = solve_gurobi(inst, start, gcfg, out / "gurobi.log")
            save_results(inst, out, start, routes, info, t_or)
        else:
            print(f"{n_stores} stores: cached result in {out.relative_to(ROOT)}")

        s = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        g = s["gurobi"]
        cut_time = g.get("rci_time_sec", 0.0)
        rows.append({
            "stores": n_stores,
            "crates": s["delivered_crates"],
            "vehicles": s["routes"],
            "binary_vars": g["n_binary_vars"],
            "constraints": g["n_constraints"],
            "best_cost_inr": s["total_cost_inr"],
            "lower_bound_inr": g["best_bound_inr"],
            "gap_pct": g["mip_gap_pct"],
            "status": g["status"],
            "nodes_explored": g["nodes_explored"],
            "ortools_sec": s["ortools_runtime_sec"],
            "cuts_sec": cut_time,
            "gurobi_sec": g["runtime_sec"],
            "total_sec": s["ortools_runtime_sec"] + cut_time + g["runtime_sec"],
            "cost_per_crate_inr": s["cost_per_crate_inr"],
            "fleet_utilization_pct": s["fleet_utilization_pct"],
        })

    df = pd.DataFrame(rows)
    df.to_csv(base / "scaling_results.csv", index=False)
    return df
