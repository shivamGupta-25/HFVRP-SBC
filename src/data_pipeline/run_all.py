"""Run the data pipeline: filter NCR stores -> generate synthetic data -> build OSRM matrices.

Usage (from the project root):  .venv\\Scripts\\python.exe src\\data_pipeline\\run_all.py
From the notebook:              ensure_data(cfg) runs only the steps whose files are missing.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import ROOT  # noqa: E402
from data_pipeline import generate_synthetic, osrm_matrix, prepare_stores  # noqa: E402

STEPS = [
    ("1/3 Filter Delhi-NCR dark stores", prepare_stores.main),
    ("2/3 Generate synthetic fleet, store and forecast data", generate_synthetic.main),
    ("3/3 Build OSRM road distance / time matrices", osrm_matrix.main),
]


def main(steps=STEPS) -> None:
    t0 = time.perf_counter()
    for title, step in steps:
        print(f"\n=== {title} ===")
        step()
    print(f"\nData pipeline finished in {time.perf_counter() - t0:.0f}s")


def ensure_data(cfg: dict, force: bool = False) -> None:
    """Generate the data only if it is missing (or if force=True)."""
    proc = ROOT / cfg["paths"]["processed_dir"]
    syn = ROOT / cfg["paths"]["synthetic_dir"]
    outputs = [
        [proc / "stores_ncr.csv"],
        [syn / "vehicles.csv", syn / "store_params.csv",
         *(syn / "forecasts" / f"forecast_{s}.csv" for s in cfg["scenarios"])],
        [proc / "nodes.csv", proc / "distance_km.csv", proc / "duration_min.csv"],
    ]
    missing = [any(not p.exists() for p in files) for files in outputs]

    # a new store list invalidates everything downstream
    if force or missing[0]:
        main()
    elif any(missing):
        main([step for step, m in zip(STEPS, missing) if m])
    else:
        print("Data found in data/ - skipping generation (set REGENERATE_DATA = True to rebuild).")


if __name__ == "__main__":
    main()
