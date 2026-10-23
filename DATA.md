# Data Documentation — Blinkit Delhi-NCR Warehouse → Dark Store Replenishment (HFVRP)

This document describes every dataset used in the project: **where it comes from, how it is generated, what each column means, and what the data says**.

All synthetic data is generated **reproducibly** (random seed `42` in [config.yaml](config.yaml)). Every number below can be regenerated with the pipeline in Section 7.

---

## 1. Data Overview

| # | File | Type | Source | Used for |
|---|------|------|--------|----------|
| 1 | `blinkit-darkstores.csv` | Raw, real | Provided by the project team (public Blinkit dark-store locations, all-India) | Store locations |
| 2 | `data/processed/stores_ncr.csv` | Processed, real | Filtered from #1 | Stores in scope |
| 3 | `data/processed/nodes.csv` | Processed, real + assumed depot | #2 + depot from `config.yaml` + OSRM snapping | Node index of the network |
| 4 | `data/processed/distance_km.csv` | Real (OSRM) | OSRM Table API | Road distance $d_{ij}$ (cost) |
| 5 | `data/processed/duration_min.csv` | Real (OSRM) | OSRM Table API | Free-flow drive time (reporting only) |
| 6 | `data/synthetic/vehicles.csv` | Synthetic | `config.yaml` (market-informed assumptions) | Fleet $Q_k, F_k, c_k, m_k$ |
| 7 | `data/synthetic/store_params.csv` | Synthetic | `generate_synthetic.py` | Storage capacity, stock, vehicle access |
| 8 | `data/synthetic/forecasts/forecast_<scenario>.csv` | Synthetic (placeholder for real forecast) | `generate_synthetic.py` | Replenishment requirement |

```
blinkit-darkstores.csv ──► prepare_stores.py ──► stores_ncr.csv ──┬──► osrm_matrix.py ──► nodes.csv, distance_km.csv, duration_min.csv
                                                                  └──► generate_synthetic.py ──► vehicles.csv, store_params.csv, forecasts/*.csv
```

---

## 2. Real Data

### 2.1 Raw dark store locations — `blinkit-darkstores.csv`

- **Source:** provided by the team; contains publicly listed Blinkit dark-store coordinates across India.
- **Size:** 1,954 stores, no missing values, no duplicate IDs, no duplicate coordinates.

| Column | Type | Meaning |
|--------|------|---------|
| `id` | int | Blinkit store identifier |
| `accuracy` | int | Location accuracy value as reported by the source (interpreted as metres; not used in the model) |
| `lat` | float | Latitude (WGS84) |
| `lng` | float | Longitude (WGS84) |

### 2.2 Delhi-NCR stores — `data/processed/stores_ncr.csv`

- **Source:** `prepare_stores.py` filters the raw file with the bounding box in `config.yaml → region`:

  | | Min | Max |
  |---|---|---|
  | Latitude | 28.30 | 28.95 |
  | Longitude | 76.80 | 77.60 |

- **Scope definition:** "Delhi-NCR" = **Delhi + core NCR cities** (Gurugram, Noida, Greater Noida, Ghaziabad, Faridabad). Outer NCR districts (Sonipat, Panipat, Meerut, Rohtak, etc.) are excluded.
- **Result:** **361 dark stores**. Actual extent: lat 28.310–28.922, lng 76.889–77.578.

| Column | Type | Meaning |
|--------|------|---------|
| `store_id` | int | Blinkit store ID (from `id`) |
| `lat`, `lng` | float | Store coordinates |
| `location_accuracy` | int | From raw `accuracy` (0–56 in NCR; 126 stores report 0). Informational only |

### 2.3 Network nodes — `data/processed/nodes.csv`

| Column | Type | Meaning |
|--------|------|---------|
| `node` | int | Model index: `0` = warehouse, `1..361` = stores |
| `node_id` | str | `WH_OKHLA` or the Blinkit store ID |
| `node_type` | str | `depot` / `store` |
| `lat`, `lng` | float | Coordinates |
| `snap_distance_m` | float | Distance (m) from the coordinate to the nearest drivable road, as reported by OSRM |

**What it says:** median snapping distance is 11 m and the maximum is 168 m, so every store lies on or very close to the road network. The coordinates are reliable for routing.

**Warehouse (depot) — assumed:** single mother warehouse at **Okhla Industrial Area, New Delhi (28.5310, 77.2710)**. It was chosen because it is an established logistics/industrial zone, centrally placed between South Delhi, Noida and Faridabad. Blinkit's actual warehouse locations are not used. The depot can be changed in `config.yaml → depot`.

### 2.4 Road distance & time matrices — `distance_km.csv`, `duration_min.csv`

- **Source:** [OSRM](http://project-osrm.org/) **Table service** (`/table/v1/driving/...`, `annotations=distance,duration`) on OpenStreetMap road data. Server URL is set in `config.yaml → osrm.base_url` (default: public OSRM server).
- **Method:** 362 × 362 matrix requested in 64 blocks of ≤ 50 × 50 (server limit ≤ 100 coordinates per request), 1 s delay between calls.
- **Format:** square CSV, row = origin `node_id`, column = destination `node_id`.

| File | Unit | Meaning |
|------|------|---------|
| `distance_km.csv` | km | Shortest **road** distance from $i$ to $j$ by car (this is $d_{ij}$ in the model) |
| `duration_min.csv` | minutes | Free-flow drive time (no traffic). **Not used in the model** (no time constraints); reported as a KPI only |

**What the data says:**

| Statistic | Value |
|-----------|-------|
| Store-to-store distance (min / mean / max) | 0.21 / 31.37 / 130.35 km |
| Warehouse → store distance (min / median / max) | 1.24 / 24.46 / 66.06 km |
| Warehouse → store drive time (min / median / max) | 3.3 / 28.0 / 59.1 min |
| Pairs where $d_{ij}$ and $d_{ji}$ differ by > 1 km | 42.4 % |
| Mean $\lvert d_{ij} - d_{ji} \rvert$ | 1.39 km |
| Unreachable pairs | 0 |

The matrix is **asymmetric** because of one-way roads, flyovers, U-turns and divided highways. This is why real road data is used instead of straight-line distance. The model treats $d_{ij} \neq d_{ji}$.

---

## 3. Synthetic Data — Fleet: `data/synthetic/vehicles.csv`

- **Source:** assumptions in `config.yaml → vehicles`, informed by common Indian intra-city goods vehicles. Costs are **indicative values for academic modelling**, not quoted market rates.
- **Capacity unit:** standard plastic crate (600 × 400 × 300 mm, about 12 kg loaded). Crate capacity is limited by cargo-bed volume. Check: weight at full crate load (60 × 12 = 720 kg, 1,440 kg, 2,880 kg) stays below each vehicle's payload, so **volume (crates) is the binding capacity**.

| Column | Unit | S | M | L |
|--------|------|---|---|---|
| `name` | – | Tata Ace (mini truck) | Tata 407 (LCV) | Eicher 14 ft (MCV) |
| `capacity_crates` ($Q_k$) | crates | 60 | 120 | 240 |
| `payload_kg` | kg | 750 | 2,500 | 4,000 |
| `fixed_cost_inr` ($F_k$) | ₹ / vehicle / night | 1,200 | 2,000 | 3,200 |
| `cost_per_km_inr` ($c_k$) | ₹ / km | 9 | 14 | 20 |
| `available` ($m_k$) | vehicles | 40 | 50 | 50 |

**Design logic:** fixed cost per crate of capacity falls with vehicle size (₹20 → ₹16.7 → ₹13.3), while cost per km rises. The model therefore faces a real trade-off: **large trucks are cheaper per crate when well filled, small trucks are cheaper when the load is small or the stores are spread out**.

Total fleet capacity = **20,400 crates**.

---

## 4. Synthetic Data — Store Parameters: `data/synthetic/store_params.csv`

| Column | Type | Meaning | Generation rule |
|--------|------|---------|-----------------|
| `store_id` | int | Blinkit store ID | From `stores_ncr.csv` |
| `tier` | S/M/L | Store size class | Random: 30 % S, 50 % M, 20 % L |
| `storage_capacity_crates` ($C_i$) | crates | Max replenishable stock the store can hold | S = 150, M = 250, L = 400 |
| `on_hand_crates` ($H_i$) | crates | Stock already in the store at planning time | $C_i \times U(0.55, 0.90)$, rounded |
| `max_vehicle_type` | S/M/L | Largest vehicle that can physically reach / unload at the store | `L` by default; reduced inside restricted zones (below) |
| `restricted_zone` | str | Name of the restricted area containing the store, or `none` | Haversine distance to zone centre ≤ radius |

**Restricted zones** (dense, narrow-lane markets where larger trucks cannot operate). Each is a 1.5 km radius around the given centre:

| Zone | Centre (lat, lng) | Max vehicle |
|------|-------------------|-------------|
| Chandni Chowk | 28.6506, 77.2303 | S |
| Karol Bagh | 28.6519, 77.1909 | M |
| Lajpat Nagar | 28.5677, 77.2433 | M |
| Laxmi Nagar | 28.6304, 77.2773 | M |
| Shahdara | 28.6735, 77.2894 | M |

**What the data says:**

| Statistic | Value |
|-----------|-------|
| Tier mix | S = 111, M = 182, L = 68 stores |
| Vehicle access | L allowed: 345 · max M: 15 · max S: 1 |
| Stores per zone | Karol Bagh 6, Lajpat Nagar 5, Laxmi Nagar 2, Shahdara 2, Chandni Chowk 1 |
| On-hand stock (min / mean / max) | 84 / 181 / 359 crates |
| Free space $C_i - H_i$ (min / median / max) | 15 / 59 / 178 crates |

The compatibility parameter used in the model is derived from this file: $a_{ik} = 1$ if $Q_k \le Q_{\text{max\_vehicle}(i)}$, else 0.

---

## 5. Synthetic Data — Forecasts: `data/synthetic/forecasts/forecast_<scenario>.csv`

Forecasting is **out of scope**. These files are a **placeholder for the output of a forecasting system**. Any file with the same two columns can be plugged in through `config.yaml → model.forecast_file`, and the model re-plans with no code change.

| Column | Type | Meaning |
|--------|------|---------|
| `store_id` | int | Blinkit store ID |
| `forecast_crates` ($R_i$) | int | Crates the store needs in tonight's replenishment |

**Generation rule:**

1. Base forecast per store ~ **Lognormal** with mean = tier mean (S = 22, M = 35, L = 55 crates) and coefficient of variation 0.25. Lognormal is used because demand is positive and right-skewed.
2. Each scenario multiplies the base by a store-level random factor:

| Scenario | Multiplier | Interpretation |
|----------|------------|----------------|
| `base` | 1.00 | Normal day |
| `festive` | U(1.20, 1.40) | Festival / sale spike (e.g., Diwali) |
| `low` | U(0.75, 0.85) | Low-demand day |

**What the data says:**

| Scenario | Total forecast | Mean / store | Min – Max per store |
|----------|---------------:|-------------:|--------------------:|
| base | 12,400 | 34.3 | 9 – 89 |
| festive | 16,148 | 44.7 | 11 – 120 |
| low | 9,913 | 27.5 | 7 – 69 |

---

## 6. Derived Model Input — Effective Demand $q_i$

The quantity actually delivered to store $i$ is computed in `src/common.py → effective_demand()`:

$$
q_i = \min\big(\,R_i,\;\; C_i - H_i,\;\; Q_{\text{max\_vehicle}(i)}\,\big)
$$

- $R_i$: forecast requirement
- $C_i - H_i$: free storage space (cannot deliver more than the store can hold)
- $Q_{\text{max\_vehicle}(i)}$: one visit per store, so the load cannot exceed the largest vehicle allowed there

Unserved quantity $R_i - q_i$ is reported as a KPI (it signals the store needs more space or a second trip).

| Scenario | Forecast | Deliverable $\sum q_i$ | Unserved | Stores limited by space | Stores limited by vehicle | Min. vehicles (all L) |
|----------|---------:|-----------------------:|---------:|------------------------:|--------------------------:|----------------------:|
| base | 12,400 | 11,988 | 412 | 44 | 0 | 50 |
| festive | 16,148 | 14,867 | 1,281 | 96 | 1 | 62 |
| low | 9,913 | 9,803 | 110 | 23 | 0 | 41 |

All scenarios are feasible against the total fleet capacity of 20,400 crates.

---

## 7. Reproducing the Data

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe src\data_pipeline\run_all.py    # filter stores -> synthetic data -> OSRM matrices (64 OSRM requests)
```

The scripts live in `src/data_pipeline/` (`prepare_stores.py`, `generate_synthetic.py`, `osrm_matrix.py`). `run_all.py` runs them in order.

All tunable values (region box, depot, OSRM server, fleet, tiers, zones, scenarios, seed) are in [config.yaml](config.yaml).
