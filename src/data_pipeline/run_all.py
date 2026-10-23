"""Run the full data pipeline: filter NCR stores -> generate synthetic data -> build OSRM matrices.

Usage (from the project root):  .venv\\Scripts\\python.exe src\\data_pipeline\\run_all.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_pipeline import generate_synthetic, osrm_matrix, prepare_stores  # noqa: E402

STEPS = [
    ("1/3 Filter Delhi-NCR dark stores", prepare_stores.main),
    ("2/3 Generate synthetic fleet, store and forecast data", generate_synthetic.main),
    ("3/3 Build OSRM road distance / time matrices", osrm_matrix.main),
]


def main() -> None:
    t0 = time.perf_counter()
    for title, step in STEPS:
        print(f"\n=== {title} ===")
        step()
    print(f"\nData pipeline finished in {time.perf_counter() - t0:.0f}s")


if __name__ == "__main__":
    main()
