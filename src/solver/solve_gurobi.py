"""Stage 2: exact HFVRP MILP (single-commodity flow) in Gurobi, warm-started from OR-Tools."""
import os
from pathlib import Path

import gurobipy as gp
from gurobipy import GRB

from solver.instance import Instance

STATUS = {GRB.OPTIMAL: "OPTIMAL", GRB.TIME_LIMIT: "TIME_LIMIT", GRB.INTERRUPTED: "INTERRUPTED"}


def solve_gurobi(inst: Instance, start_routes: list[dict], gcfg: dict, log_file: Path) -> tuple[list[dict], dict]:
    if gcfg.get("license_file"):
        os.environ["GRB_LICENSE_FILE"] = str(gcfg["license_file"])

    q, d = inst.q, inst.dist
    veh = inst.vehicles
    Q = veh["capacity_crates"].to_numpy()
    F = veh["fixed_cost_inr"].to_numpy()
    c = veh["cost_per_km_inr"].to_numpy()
    m = veh["available"].to_numpy()
    N, K = len(q), len(veh)

    # constraint (4) + capacity pre-filter: a type only visits stores it may access and whose q fits in Q_k
    arcs = []
    for k in range(K):
        allowed = [i for i in range(N) if inst.compat[i, k] and q[i] <= Q[k]]
        arcs += [(i, j, k) for i in allowed for j in allowed if i != j]
    obj = [c[k] * d[i, j] + (F[k] if i == 0 else 0.0) for i, j, k in arcs]
    flow_arcs = [a for a in arcs if a[1] != 0]  # constraint (7): f_i0k = 0

    env = gp.Env(empty=True)
    env.setParam("OutputFlag", 1 if gcfg.get("log_to_console", True) else 0)
    env.setParam("LogFile", str(log_file))
    env.start()
    model = gp.Model("blinkit_hfvrp", env=env)

    x = model.addVars(arcs, vtype=GRB.BINARY, obj=obj, name="x")
    f = model.addVars(flow_arcs, lb=0.0, ub=[Q[k] - q[i] for i, _, k in flow_arcs], name="f")
    model.ModelSense = GRB.MINIMIZE

    x_in = {j: [] for j in range(N)}
    x_in_k, x_out_k = {}, {}
    f_in = {j: [] for j in range(N)}
    f_out = {j: [] for j in range(N)}
    for i, j, k in arcs:
        x_in[j].append(x[i, j, k])
        x_in_k.setdefault((j, k), []).append(x[i, j, k])
        x_out_k.setdefault((i, k), []).append(x[i, j, k])
    for i, j, k in flow_arcs:
        f_in[j].append(f[i, j, k])
        f_out[i].append(f[i, j, k])

    for j in range(1, N):
        model.addLConstr(gp.quicksum(x_in[j]), GRB.EQUAL, 1, name=f"visit[{j}]")                      # (1)
        model.addLConstr(gp.quicksum(f_in[j]) - gp.quicksum(f_out[j]), GRB.EQUAL, int(q[j]), f"flow[{j}]")  # (5)
    for (j, k), ins in x_in_k.items():
        model.addLConstr(gp.quicksum(ins), GRB.EQUAL, gp.quicksum(x_out_k[j, k]), name=f"cont[{j},{k}]")  # (2)
    for k in range(K):
        model.addLConstr(gp.quicksum(x_out_k.get((0, k), [])), GRB.LESS_EQUAL, int(m[k]), name=f"fleet[{k}]")  # (3)
    for i, j, k in flow_arcs:                                                                          # (6)
        model.addLConstr(f[i, j, k] - int(q[j]) * x[i, j, k], GRB.GREATER_EQUAL, 0)
        model.addLConstr(f[i, j, k] - int(Q[k] - q[i]) * x[i, j, k], GRB.LESS_EQUAL, 0)

    _set_start(model, x, f, start_routes, q)

    model.Params.TimeLimit = gcfg["time_limit_sec"]
    model.Params.MIPGap = gcfg["mip_gap"]
    model.Params.Threads = gcfg["threads"]
    model.Params.Seed = gcfg["seed"]
    model.optimize()

    if model.SolCount == 0:
        raise RuntimeError(f"Gurobi found no solution (status {model.Status}).")

    routes = _extract_routes(arcs, model.getAttr("X", [x[a] for a in arcs]))
    info = {
        "status": STATUS.get(model.Status, str(model.Status)),
        "objective_inr": model.ObjVal,
        "best_bound_inr": model.ObjBound,
        "mip_gap_pct": 100 * model.MIPGap,
        "runtime_sec": model.Runtime,
        "nodes_explored": model.NodeCount,
        "n_binary_vars": len(arcs),
        "n_continuous_vars": len(flow_arcs),
        "n_constraints": model.NumConstrs,
    }
    model.dispose()
    env.dispose()
    return routes, info


def _set_start(model, x, f, routes, q):
    model.setAttr("Start", list(x.values()), [0.0] * len(x))
    model.setAttr("Start", list(f.values()), [0.0] * len(f))
    for route in routes:
        k, path = route["type"], [0, *route["stops"], 0]
        load = int(q[route["stops"]].sum())
        for a, b in zip(path, path[1:]):
            x[a, b, k].Start = 1.0
            if b != 0:
                f[a, b, k].Start = load
                load -= int(q[b])


def _extract_routes(arcs, values):
    used = [a for a, v in zip(arcs, values) if v > 0.5]
    succ = {(i, k): j for i, j, k in used if i != 0}
    routes = []
    for _, j, k in (a for a in used if a[0] == 0):
        stops = []
        while j != 0:
            stops.append(j)
            j = succ[(j, k)]
        routes.append({"type": k, "stops": stops})
    return sorted(routes, key=lambda r: (r["type"], r["stops"][0]))
