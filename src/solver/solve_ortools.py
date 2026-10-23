"""Stage 1: OR-Tools routing (Guided Local Search) to build a high-quality warm start."""
import numpy as np
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from solver.instance import Instance

SCALE = 100  # costs in paise: OR-Tools needs integer arc costs


def solve_ortools(inst: Instance, time_limit_sec: int, log: bool = False,
                  first_solution: str = "CHRISTOFIDES") -> list[dict]:
    veh = inst.vehicles
    # largest (cheapest per crate) vehicles first so construction heuristics consolidate loads
    order = np.arange(len(veh))[::-1]
    veh_type = np.repeat(order, veh["available"].to_numpy()[order])
    n_vehicles = len(veh_type)

    manager = pywrapcp.RoutingIndexManager(len(inst.q), n_vehicles, 0)
    routing = pywrapcp.RoutingModel(manager)

    for k, row in veh.iterrows():
        cost = np.rint(inst.dist * row["cost_per_km_inr"] * SCALE).astype(np.int64).tolist()
        cb = routing.RegisterTransitMatrix(cost)
        for v in np.flatnonzero(veh_type == k):
            routing.SetArcCostEvaluatorOfVehicle(cb, int(v))
            routing.SetFixedCostOfVehicle(int(row["fixed_cost_inr"] * SCALE), int(v))

    demand_cb = routing.RegisterUnaryTransitVector(inst.q.tolist())
    capacities = [int(veh["capacity_crates"].iloc[k]) for k in veh_type]
    routing.AddDimensionWithVehicleCapacity(demand_cb, 0, capacities, True, "Load")

    for node in range(1, len(inst.q)):
        allowed = [int(v) for v in range(n_vehicles) if inst.compat[node, veh_type[v]]]
        if len(allowed) < n_vehicles:
            # -1 keeps the node's "inactive" value valid for the solver
            routing.VehicleVar(manager.NodeToIndex(node)).SetValues([-1] + allowed)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = getattr(routing_enums_pb2.FirstSolutionStrategy, first_solution)
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromSeconds(int(time_limit_sec))
    params.log_search = log

    solution = routing.SolveWithParameters(params)
    if solution is None:
        raise RuntimeError("OR-Tools found no feasible solution (check fleet size / compatibility).")

    routes = []
    for v in range(n_vehicles):
        idx = solution.Value(routing.NextVar(routing.Start(v)))
        stops = []
        while not routing.IsEnd(idx):
            stops.append(manager.IndexToNode(idx))
            idx = solution.Value(routing.NextVar(idx))
        if stops:
            routes.append({"type": int(veh_type[v]), "stops": stops})
    return routes
