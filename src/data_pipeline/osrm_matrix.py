"""Build the road distance (km) and travel time (min) matrices from the OSRM Table service."""
import math
import time

import numpy as np
import pandas as pd
import requests

from common import ROOT, data_path, load_config


def fetch_block(session, ocfg, coords, src, dst):
    block = list(dict.fromkeys(src + dst))
    pos = {node: p for p, node in enumerate(block)}
    coord_str = ";".join(f"{coords[n][1]:.6f},{coords[n][0]:.6f}" for n in block)  # OSRM expects lng,lat
    url = f"{ocfg['base_url'].rstrip('/')}/table/v1/{ocfg['profile']}/{coord_str}"
    params = {
        "sources": ";".join(str(pos[i]) for i in src),
        "destinations": ";".join(str(pos[j]) for j in dst),
        "annotations": "distance,duration",
    }

    for attempt in range(1, ocfg["max_retries"] + 1):
        try:
            resp = session.get(url, params=params, timeout=ocfg["timeout_sec"])
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == "Ok":
                    return data
                raise RuntimeError(f"OSRM error: {data.get('code')} {data.get('message')}")
            if resp.status_code not in (429, 500, 502, 503, 504):
                raise RuntimeError(f"OSRM HTTP {resp.status_code}: {resp.text[:200]}")
        except requests.RequestException as exc:
            if attempt == ocfg["max_retries"]:
                raise
            print(f"  request failed ({exc}); retrying")
        time.sleep(ocfg["request_delay_sec"] * 2**attempt)
    raise RuntimeError("OSRM request failed after retries")


def main() -> None:
    cfg = load_config()
    ocfg = cfg["osrm"]
    proc = data_path(cfg, "processed_dir")
    stores = pd.read_csv(proc / "stores_ncr.csv")

    dep = cfg["depot"]
    nodes = pd.concat(
        [
            pd.DataFrame([{"node_id": dep["id"], "node_type": "depot", "lat": dep["lat"], "lng": dep["lng"]}]),
            pd.DataFrame(
                {"node_id": stores["store_id"].astype(str), "node_type": "store", "lat": stores["lat"], "lng": stores["lng"]}
            ),
        ],
        ignore_index=True,
    )
    nodes.insert(0, "node", range(len(nodes)))
    coords = list(zip(nodes["lat"], nodes["lng"]))
    n = len(nodes)

    dist = np.full((n, n), np.nan)
    dur = np.full((n, n), np.nan)
    snap = np.full(n, np.nan)

    chunk = ocfg["chunk_size"]
    blocks = [list(range(s, min(s + chunk, n))) for s in range(0, n, chunk)]
    total = len(blocks) ** 2
    print(f"Nodes: {n} | OSRM requests: {total} | server: {ocfg['base_url']}")

    with requests.Session() as session:
        session.headers["User-Agent"] = "blinkit-hfvrp-academic-project"
        k = 0
        for src in blocks:
            for dst in blocks:
                k += 1
                data = fetch_block(session, ocfg, coords, src, dst)
                d = np.array(data["distances"], dtype=float)
                t = np.array(data["durations"], dtype=float)
                dist[np.ix_(src, dst)] = d
                dur[np.ix_(src, dst)] = t
                for p, i in enumerate(src):
                    snap[i] = data["sources"][p]["distance"]
                print(f"  block {k}/{total} done")
                time.sleep(ocfg["request_delay_sec"])

    if np.isnan(dist).any() or np.isnan(dur).any():
        bad = sorted(set(np.argwhere(np.isnan(dist))[:, 0]))
        raise RuntimeError(f"OSRM returned unreachable pairs for nodes: {bad[:20]}")

    ids = nodes["node_id"].tolist()
    nodes["snap_distance_m"] = snap.round(1)
    nodes.to_csv(proc / "nodes.csv", index=False)
    pd.DataFrame(dist / 1000.0, index=ids, columns=ids).round(3).to_csv(proc / "distance_km.csv")
    pd.DataFrame(dur / 60.0, index=ids, columns=ids).round(2).to_csv(proc / "duration_min.csv")

    off = ~np.eye(n, dtype=bool)
    asym = np.abs(dist - dist.T)[off].mean() / 1000.0
    print(f"Distance km  min/mean/max (i!=j): {dist[off].min()/1000:.2f} / {dist[off].mean()/1000:.2f} / {dist[off].max()/1000:.2f}")
    print(f"Mean |d_ij - d_ji|: {asym:.2f} km | max snap distance: {snap.max():.0f} m")
    print(f"Written to {proc.relative_to(ROOT)}")
