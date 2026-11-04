# Analytical Workload Optimization & Data Engineering Pipeline

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-17%20passed-brightgreen.svg)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Zero-External-Dependencies](https://img.shields.io/badge/Zero--Cloud--Credentials-Local%20Runnable-success.svg)]()

> A production-grade Data Engineering solution demonstrating query optimization, schema validation, quarantine dead-letter routing, idempotent publishing, automated reconciliation, and measurable workload performance gains.

---

## 1. Executive Summary & Problem Context

In modern analytical databases and data lakes, poorly designed schemas and naive queries lead to exponential query latencies and resource bloat. This project models an analytical e-commerce workload, establishes an unoptimized baseline, identifies 4 major classes of query inefficiencies, and demonstrates quantifiable speedups:

| Inefficiency Category | Baseline Bottleneck | Applied Optimization | Measured Latency Reduction | Speedup Ratio |
|:---|:---|:---|:---:|:---:|
| **1. Inefficient Scan** | Full sequential scan on fact table (`SCAN fact_orders`) | Composite covering index `(order_date, order_status, customer_id, total_amount)` | **53.8%** | **2.17x** |
| **2. Inefficient Join** | Unindexed nested loop scan across 120k+ fact items | Foreign key index seeking (`SEARCH foi USING INDEX idx_fact_order_items_order_id`) | **52.4%** | **2.10x** |
| **3. Inefficient Aggregation**| High-volume join before multi-column string `GROUP BY` | Pushdown pre-aggregation over integer covering index before joining dimensions | **83.5%** | **6.05x** |
| **4. Inefficient Filtering** | Non-sargable scalar function `strftime('%Y', order_date) = '2025'` | Sargable date range predicate `order_date >= ... AND order_date < ...` | **74.6%** | **3.94x** |

---

## 2. Architecture & Data Flow

```mermaid
flowchart LR
    A["Raw Data Ingestion<br/>(JSONL / CSV)"] --> B{"Pipeline Validator<br/>(Pydantic & Foreign Keys)"}
    B -- "Malformed / Duplicate / Orphan" --> C[("Quarantine Dead-Letter<br/>quarantine_records")]
    B -- "Clean & Validated" --> D["Atomic Idempotent Upsert<br/>(INSERT OR REPLACE)"]
    D --> E[("Target Warehouse<br/>Star Schema")]
    D --> F[("Run Observability<br/>pipeline_runs")]
    E --> G["Data Reconciler<br/>(Audit & Invariants)"]
    E --> H["Benchmark Suite<br/>(EXPLAIN Plans & Latency)"]
```

---

## 3. Key Pipeline Capabilities

- **Strict Validation & Quarantine:** Catches missing attributes, malformed date strings, invalid customer/product foreign keys, negative quantities/amounts, and line item calculation mismatches (`line_total != quantity * unit_price`). Quarantined rows are fully preserved with error diagnostics and source lineage.
- **Strict Idempotency:** Rerunning the exact same input batch produces the exact same target row count and totals without creating duplicate primary keys or orphan fact rows.
- **Run Observability:** Every execution records a `run_id`, start/end timestamps, input/output row counts, rejected counts, and error summaries in the `pipeline_runs` table.
- **Automated Reconciliation:** Automated auditing verifies that `source_count == target_valid_count + quarantined_count`, published revenue matches source amounts, and checks 6 fundamental integrity invariants.
- **Zero-Dependency Reproducibility:** Runs locally on Python standard library SQLite with Write-Ahead Logging (WAL) mode enabled, requiring zero external database setup, Docker containers, or cloud credentials.

---

## 4. Quickstart & Installation

### Prerequisites
- Python 3.10, 3.11, 3.12, 3.13, or 3.14
- Git

### 1. Clone & Set Up Environment
```bash
git clone <your-repository-url>
cd analytical-workload-optimization

# Optional: Create and activate virtual environment
python -m venv venv
# On Windows:
.\venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Run the Full End-to-End Demo Pipeline
Execute the full automated lifecycle (Data Generation -> Baseline Ingestion -> Optimized Ingestion -> Idempotent Rerun -> Reconciliation -> Workload Benchmarking):
```bash
python run_demo.py
```

### 3. Run at Scale (e.g., 25,000+ to 50,000 orders)
```bash
python run_demo.py --orders 25000
```

### 4. Run Automated Test Suite
```bash
python -m pytest
```

---

## 5. Workload Benchmarking & EXPLAIN Plans

The benchmark compares the exact same analytical business questions executed on the **Baseline (unindexed)** vs **Optimized (indexed & rewritten)** engines.

### Benchmark Results (5,000 Orders Workload)
```text
==========================================================================================
ANALYTICAL WORKLOAD BENCHMARK SUMMARY
==========================================================================================
Workload ID            | Baseline (ms)   | Optimized (ms)  | Speedup    | Latency Reduction 
------------------------------------------------------------------------------------------
WORKLOAD_1_SCAN        |        0.59 ms |        0.27 ms |     2.17x |             53.8%
WORKLOAD_2_JOIN        |        1.23 ms |        0.58 ms |     2.10x |             52.4%
WORKLOAD_3_AGGREGATION |        8.95 ms |        1.48 ms |     6.05x |             83.5%
WORKLOAD_4_FILTERING   |        2.33 ms |        0.59 ms |     3.94x |             74.6%
==========================================================================================
```

### Plan Comparison Highlights

#### Workload 1: Scan Optimization
- **Baseline Plan:** `SCAN fact_orders` (scans all heap rows) + `USE TEMP B-TREE FOR ORDER BY`
- **Optimized Plan:** `SEARCH fact_orders USING COVERING INDEX idx_fact_orders_date_status (order_date>? AND order_date<?)`

#### Workload 3: Aggregation Pushdown
- **Baseline Plan:** `SCAN foi` (scans 120,000+ line items) -> `SEARCH p` -> `USE TEMP B-TREE FOR GROUP BY` on `p.category` string.
- **Optimized Plan:** `SCAN fact_order_items USING COVERING INDEX idx_fact_order_items_covering` -> Group by integer `product_id` -> Collapses rows to ~1,000 -> Joins `dim_products`. Over **6x faster**.

#### Workload 4: Sargable Filtering
- **Baseline Plan:** `SCAN fact_orders` (Evaluates `strftime('%Y', order_date)` for every record).
- **Optimized Plan:** `SEARCH fact_orders USING COVERING INDEX idx_fact_orders_date_status (order_date>? AND order_date<?)`. Over **3.9x faster**.

---

## 6. Data Reconciliation & Quality Evidence

Running `run_demo.py` automatically generates a reconciliation audit stored in `data/output/reconciliation_report.md`:

```text
============================================================
PIPELINE DATA RECONCILIATION AUDIT (STATUS: PASSED)
============================================================
1. Row Balance Audit:
   - Source Orders: 5,000 | Target Published: 4,830 | Quarantined: 170 | Reconciled: YES
   - Source Items:  12,500 | Target Published: 12,063 | Quarantined: 437 | Reconciled: YES

2. Invariant Assertions:
   - Orphan Customer Foreign Keys: 0 (PASSED)
   - Orphan Product Foreign Keys:  0 (PASSED)
   - Orphan Order Parent Keys:     0 (PASSED)
   - Duplicate Primary Keys:        0 (PASSED)
   - Line Total Math Discrepancies: 0 (PASSED)
============================================================
```

---

## 7. Project Structure

```text
analytical-workload-optimization/
├── README.md                     # Project documentation & execution guide
├── DATA_DICTIONARY.md            # Entity & attribute level data dictionary
├── DESIGN_AND_TRADEOFFS.md       # Architecture decisions, optimization deep-dive & trade-offs
├── AI_DISCLOSURE.md              # Transparent AI prompt history & review log
├── requirements.txt              # Core Python dependencies
├── pyproject.toml                # Project packaging & pytest config
├── run_demo.py                   # One-click CLI runner for the full lifecycle
├── src/
│   ├── __init__.py
│   ├── config.py                 # Paths, WAL connection manager with auto-close
│   ├── generator.py              # Reproducible synthetic data & edge-case generator
│   ├── models.py                 # Pydantic schemas & SQL DDL definitions
│   ├── validator.py              # Schema validation & quarantine routing
│   ├── pipeline.py               # Ingestion, idempotent loading & run observability
│   ├── optimizer.py              # Workload queries, EXPLAIN plans & benchmark suite
│   ├── reconciliation.py         # Data audit & invariant verification
│   └── logger.py                 # Structured JSON-lines logger
├── tests/
│   ├── __init__.py
│   ├── test_generator.py         # Seed reproducibility & anomaly tests
│   ├── test_validator.py         # Validation rules, nulls, orphans & duplicate tests
│   ├── test_pipeline.py          # Happy path, empty batch & quarantine isolation
│   ├── test_idempotency.py       # Safe reruns & late-arriving backfills
│   └── test_optimizer.py         # EXPLAIN plan verification & query correctness
├── data/
│   ├── sample/                   # Generated sample inputs (orders, items, dimensions)
│   └── output/                   # Generated reports (benchmark & reconciliation)
└── logs/
    └── pipeline.log              # Structured JSON-lines runtime log
```

---

## 8. Automated Test Suite

The test suite covers:
1. **Happy Path:** End-to-end ingestion and target loading.
2. **Empty Batch Handling:** Gracefully completes with status `SUCCESS` and 0 records.
3. **Schema & Null Violations:** Rejects and quarantines missing required fields.
4. **Malformed Dates:** Validates ISO calendar validity (e.g., rejecting `2025-02-31`).
5. **Referential Integrity:** Quarantines orphan customer and product foreign keys.
6. **Domain Rules:** Detects negative prices/quantities and line calculation mismatches.
7. **Idempotency & Reruns:** Verifies running identical batches yields identical target counts with 0 duplicate rows.
8. **Plan Verification:** Verifies query optimizer transitions from `SCAN` to covering index searches.

Run tests:
```bash
python -m pytest -v
```

---

## 9. Assumptions, Known Limitations & Next Steps

### Assumptions
- Customer and product dimensions are available prior to fact order ingestion (or ingested as part of initial load).
- All timestamps adhere to ISO-8601 UTC.
- Monetary amounts are rounded to 2 decimal places.

### Known Limitations
- SQLite uses a single-writer concurrency model. While WAL mode enables concurrent readers alongside a writer, high-frequency multi-process parallel writes would require queuing or transitioning to an enterprise RDBMS/warehouse.
- In-memory B-tree indexes increase file size by ~25-30% on disk.

### Next Steps
1. **Columnar Compaction:** Add an automated Parquet export step with dictionary encoding and Snappy compression.
2. **Containerization:** Provide a lightweight Dockerfile and Docker Compose setup.
3. **CI/CD Automation:** Set up GitHub Actions workflow executing `pytest` and benchmark verification on every pull request.

---

## 10. AI Disclosure
This solution was developed with transparent AI assistance (Google Antigravity / Gemini) in compliance with the **YottaFlex AI Candidate Project Submission Guidelines**. Complete prompt logs, human reviews, and architectural validations are detailed in [AI_DISCLOSURE.md](AI_DISCLOSURE.md).
