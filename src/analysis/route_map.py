"""Folium map of the optimal routes, drawn on real roads using the OSRM Route service."""
import json
import time
from pathlib import Path

import folium
import pandas as pd
import requests
import xyzservices.providers as xyz
from matplotlib import colormaps
from matplotlib.colors import to_hex

CARTO_STYLES = {
    "Carto Positron (light)": xyz.CartoDB.Positron,
    "Carto Voyager": xyz.CartoDB.Voyager,
    "Carto Dark Matter": xyz.CartoDB.DarkMatter,
}
# OpenStreetMap's own tile servers block pages without an HTTP Referer (local HTML / notebooks)
KEYLESS_STYLES = {
    "Esri Street Map": xyz.Esri.WorldStreetMap,
    "Esri Satellite": xyz.Esri.WorldImagery,
}


def _road_geometry(session, ocfg, points):
    coord_str = ";".join(f"{lng:.6f},{lat:.6f}" for lat, lng in points)
    url = f"{ocfg['base_url'].rstrip('/')}/route/v1/{ocfg['profile']}/{coord_str}"
    for attempt in range(1, ocfg["max_retries"] + 1):
        resp = session.get(url, params={"overview": "full", "geometries": "geojson"}, timeout=ocfg["timeout_sec"])
        if resp.status_code == 200 and resp.json().get("code") == "Ok":
            return [[lat, lng] for lng, lat in resp.json()["routes"][0]["geometry"]["coordinates"]]
        time.sleep(ocfg["request_delay_sec"] * 2**attempt)
    raise RuntimeError(f"OSRM route request failed: HTTP {resp.status_code} {resp.text[:200]}")


def route_geometries(cfg: dict, stops: pd.DataFrame, out_dir: Path) -> dict[int, list]:
    """Road paths per route; cached by stop sequence so a re-solve only fetches changed routes."""
    cache_file = out_dir / "route_geometries.json"
    cache = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {}

    ocfg, dep = cfg["osrm"], cfg["depot"]
    geoms, used = {}, {}
    with requests.Session() as session:
        session.headers["User-Agent"] = "blinkit-hfvrp-academic-project"
        for rid, g in stops.sort_values(["route_id", "stop_seq"]).groupby("route_id"):
            key = ">".join(g["node_id"])
            if key not in cache:
                pts = [(dep["lat"], dep["lng"]), *zip(g["lat"], g["lng"]), (dep["lat"], dep["lng"])]
                cache[key] = _road_geometry(session, ocfg, pts)
                time.sleep(ocfg["request_delay_sec"])
            geoms[int(rid)] = used[key] = cache[key]
    cache_file.write_text(json.dumps(used), encoding="utf-8")
    return geoms


def _add_base_maps(fmap: folium.Map, cfg: dict) -> None:
    """Carto styles (needs CARTO_API_KEY in config) + keyless Esri styles, switchable in the layer control."""
    key = cfg.get("CARTO_API_KEY")
    if key:
        for i, (label, provider) in enumerate(CARTO_STYLES.items()):
            folium.TileLayer(provider(apikey=key), name=label, show=i == 0).add_to(fmap)
    for i, (label, provider) in enumerate(KEYLESS_STYLES.items()):
        folium.TileLayer(provider, name=label, show=not key and i == 0).add_to(fmap)


def build_map(cfg: dict, out_dir: Path) -> folium.Map:
    routes = pd.read_csv(out_dir / "routes.csv")
    stops = pd.read_csv(out_dir / "stops.csv", dtype={"node_id": str})
    geoms = route_geometries(cfg, stops, out_dir)
    dep = cfg["depot"]
    names = {v["type"]: v["name"] for v in cfg["vehicles"]}
    palette = [to_hex(c) for c in colormaps["tab20"].colors]

    fmap = folium.Map(location=[stops["lat"].mean(), stops["lng"].mean()], zoom_start=10, tiles=None)
    _add_base_maps(fmap, cfg)
    layers = {}
    for t in [v["type"] for v in cfg["vehicles"]]:
        n = int((routes["vehicle_type"] == t).sum())
        layers[t] = folium.FeatureGroup(name=f"{t}: {names[t]} ({n} routes)").add_to(fmap)

    for _, r in routes.iterrows():
        color = palette[(r["route_id"] - 1) % len(palette)]
        layer = layers[r["vehicle_type"]]
        info = (f"Route {r['route_id']} | {r['vehicle_name']}<br>Stops: {r['n_stops']} | "
                f"Load: {r['load_crates']}/{r['capacity_crates']} ({r['utilization_pct']}%)<br>"
                f"{r['distance_km']} km | Rs {r['total_cost_inr']:,.0f}")
        folium.PolyLine(geoms[r["route_id"]], color=color, weight=3, opacity=0.85, tooltip=info).add_to(layer)
        for _, s in stops[stops["route_id"] == r["route_id"]].iterrows():
            folium.CircleMarker(
                [s["lat"], s["lng"]], radius=5, color=color, fill=True, fill_opacity=0.9,
                popup=folium.Popup(
                    f"Store {s['node_id']}<br>Route {s['route_id']} ({s['vehicle_type']}), stop {s['stop_seq']}<br>"
                    f"Delivered: {s['demand_crates']} crates<br>On board on arrival: {s['load_on_arrival']}",
                    max_width=250,
                ),
            ).add_to(layer)

    zones = folium.FeatureGroup(name="Restricted zones", show=False).add_to(fmap)
    for z in cfg["stores"]["restricted_zones"]:
        folium.Circle([z["lat"], z["lng"]], radius=z["radius_km"] * 1000, color="red", dash_array="6",
                      fill=True, fill_opacity=0.05, tooltip=f"{z['name']}: max vehicle {z['max_vehicle']}").add_to(zones)

    folium.Marker([dep["lat"], dep["lng"]], tooltip=dep["name"],
                  icon=folium.Icon(color="black", icon="warehouse", prefix="fa")).add_to(fmap)
    folium.LayerControl(collapsed=False).add_to(fmap)
    fmap.save(out_dir / "route_map.html")
    return fmap
