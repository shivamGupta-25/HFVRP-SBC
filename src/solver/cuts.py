"""Valid inequalities that strengthen the HFVRP lower bound without cutting off any feasible plan."""
import math
import time

import gurobipy as gp
import numpy as np
from gurobipy import GRB

from solver.instance import Instance


def add_fleet_count_cuts(model: gp.Model, inst: Instance, depot_out_k: dict) -> int:
    """Rounded vehicle counts: all vehicles >= ceil(sum q / Q_max), and for each smaller type k,
    vehicles of types <= k >= ceil(demand of stores that only accept types <= k / Q_k)."""
    Q = inst.vehicles["capacity_crates"].to_numpy()
    K = len(Q)
    n_cuts = 0
    for k in range(K):
        # stores whose largest allowed vehicle is type k or smaller
        stores = [i for i in range(1, len(inst.q)) if not inst.compat[i, k + 1:].any()] if k < K - 1 \
            else list(range(1, len(inst.q)))
        if not stores:
            continue
        need = math.ceil(inst.q[stores].sum() / Q[k])
        lhs = [v for t in range(k + 1) for v in depot_out_k.get(t, [])]
        model.addLConstr(gp.quicksum(lhs), GRB.GREATER_EQUAL, need, name=f"fleet_count[{k}]")
        n_cuts += 1
    return n_cuts


def add_capacity_cuts(model: gp.Model, inst: Instance, arcs: list, xvars: list, max_rounds: int,
                      cuts_per_round: int, min_improvement: float) -> dict:
    """Rounded capacity inequalities x(delta-in(S)) >= ceil(q(S) / Q_max), separated on the LP relaxation.

    Repeatedly solves the LP, finds store sets S that receive fewer vehicle entries than their demand
    requires, and adds those cuts to the model. Returns statistics of the cutting-plane loop."""
    q = inst.q
    N = len(q)
    q_max = inst.vehicles["capacity_crates"].max()
    ai = np.fromiter((a[0] for a in arcs), int, len(arcs))
    aj = np.fromiter((a[1] for a in arcs), int, len(arcs))
    in_src = [[] for _ in range(N)]
    for (i, j, _), v in zip(arcs, xvars):
        in_src[j].append((i, v))

    output_flag = model.Params.OutputFlag
    model.setAttr("VType", xvars, [GRB.CONTINUOUS] * len(xvars))
    model.Params.OutputFlag = 0
    model.Params.Method = 2  # barrier
    model.Params.Crossover = 0

    t0 = time.perf_counter()
    model.optimize()
    first = prev = model.ObjVal
    print(f"Capacity cuts | round 0: LP bound Rs {first:,.0f}", flush=True)
    total = rounds = 0
    for rounds in range(1, max_rounds + 1):
        X = np.zeros((N, N))
        np.add.at(X, (ai, aj), model.getAttr("X", xvars))
        cuts = _separate(X, q, q_max)[:cuts_per_round]
        if not cuts:
            break
        for members, need in cuts:
            inside = set(members)
            # sparse equivalent of x(delta-in(S)) >= need, since every store has in-degree 1
            model.addLConstr(
                gp.quicksum(v for j in members for i, v in in_src[j] if i in inside),
                GRB.LESS_EQUAL, len(members) - need, name=f"rci[{total}]",
            )
            total += 1
        model.optimize()
        print(f"Capacity cuts | round {rounds}: +{len(cuts)} cuts, LP bound Rs {model.ObjVal:,.0f} "
              f"({time.perf_counter() - t0:.0f}s)", flush=True)
        if model.ObjVal - prev < min_improvement * prev:
            break
        prev = model.ObjVal
    root_bound = model.ObjVal

    model.setAttr("VType", xvars, [GRB.BINARY] * len(xvars))
    model.Params.OutputFlag = output_flag
    model.Params.Method = -1
    model.Params.Crossover = -1
    return {"rci_cuts": total, "rci_rounds": rounds, "lp_bound_before_cuts_inr": first,
            "lp_bound_after_cuts_inr": root_bound, "rci_time_sec": time.perf_counter() - t0}


def _separate(X: np.ndarray, q: np.ndarray, q_max: float, max_set: int = 40, tol: float = 0.05) -> list:
    """Greedy separation: grow a store set from every seed along the strongest LP connections and
    keep every set whose vehicle in-flow is below its rounded demand."""
    N = len(q)
    W = X + X.T
    found, seen = [], set()
    for s in range(1, N):
        in_set = np.zeros(N, bool)
        in_set[s] = True
        members, load = [s], q[s]
        inflow = X[:, s].sum()
        conn = W[s].copy()
        conn[0] = conn[s] = -1
        for _ in range(max_set):
            nxt = int(np.argmax(conn))
            if conn[nxt] <= 1e-6:
                break
            # entering arcs of S + nxt = old in-flow + in-flow of nxt - arcs that became internal
            inflow += X[:, nxt].sum() - X[in_set, nxt].sum() - X[nxt, in_set].sum()
            in_set[nxt] = True
            members.append(nxt)
            load += q[nxt]
            conn += W[nxt]
            conn[in_set] = -1
            conn[0] = -1
            need = math.ceil(load / q_max)
            if inflow < need - tol:
                key = frozenset(members)
                if key not in seen:
                    seen.add(key)
                    found.append((list(members), need, need - inflow))
    found.sort(key=lambda t: -t[2])
    return [(members, need) for members, need, _ in found]
