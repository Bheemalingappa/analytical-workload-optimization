# AI Use Disclosure & Prompt History

In accordance with the **YottaFlex AI Candidate Project Submission Guidelines**, this document transparently details the utilization of AI coding assistance, prompt history, human oversight, architectural validation, and engineering judgment exercised throughout the development of this project.

---

## 1. Transparency Summary

| Assessment Metric | Candidate Details |
|:---|:---|
| **Candidate Name** | Bheema Lingappa |
| **Email Address** | bheemalingappaboler@gmail.com |
| **Assessment** | YottaFlex AI Round 3: 72-Hour Code Challenge |
| **Problem Statement** | Analytical Workload Optimization & Robust Data Pipeline |
| **AI Assistants Used** | Google Antigravity / Gemini 3.8 Flash (Pair Programming & Code Assistant) |
| **Submission Deadline** | Friday, November 6, 2026 at 5:19:44 PM IST |

---

## 2. Where AI Accelerated the Work
1. **Boilerplate and DDL Generation:** Rapid scaffolding of Pydantic models, SQL DDL schema statements, and starter test fixtures.
2. **Synthetic Data Anomaly Injection:** Automated scripting of randomized permutations for negative edge cases (e.g., calendar bounds, orphan keys, math mismatches).
3. **Markdown Documentation Formatting:** Structuring comprehensive markdown documentation, ASCII/Mermaid flowcharts, and markdown tables.

---

## 3. Where Human Oversight, Review, and Judgment Were Critical
1. **Analytical Workload Inefficiencies Identification:**
   - Designed 4 distinct categories representing real-world query bottlenecks:
     - Scan: Full table sequential scan vs. Composite covering index scan.
     - Join: Unindexed foreign key nested loop vs. Indexed foreign key seek.
     - Aggregation: Naive multi-table join with high-volume string grouping vs. Integer pushdown pre-aggregation over covering index.
     - Filtering: Non-sargable scalar function call (`strftime('%Y', ...)`) vs. Sargable date range predicate (`order_date >= ... AND order_date < ...`).
2. **Engine Selection & Sandbox Feasibility:**
   - Recognized that external cloud infrastructure or heavy database servers (like Dockerized Postgres) introduce network and authentication barriers for evaluators. Chose SQLite in Write-Ahead-Logging (WAL) mode to guarantee 100% zero-credential, local reproducibility across Windows, Linux, and macOS.
3. **Operating System File Lock Resolution:**
   - Identified Windows-specific SQLite file-locking behavior on `tempfile.TemporaryDirectory` during automated tests. Implemented explicit `@contextmanager` teardown with `conn.close()` to ensure clean cleanup across all platforms.
4. **Benchmarking Invariant Verification:**
   - Validated that baseline and optimized queries return identical result sets, aggregate totals, and row counts, ensuring speedups were achieved without sacrificing query accuracy.
5. **Reconciliation & Idempotency Invariants:**
   - Verified that rerunning identical batches updates target lineage without introducing duplicate keys or inflating revenue aggregates.

---

## 4. Prompt History Log

### Prompt Session: Architectural Planning & Scaffolding
* **Prompt 1:**
  > *"Analyze the YottaFlex AI problem statement: 'Use a supplied analytical workload and synthetic tables to identify inefficient scans, joins, aggregations, and filtering. Document a baseline and demonstrate measurable improvements.' Design a modular, production-ready Data Engineering project adhering to 0-3 years experience target with ingestion, schema validation, quarantine dead-letter repository, idempotent loading, automated reconciliation, and benchmark runner."*
* **Candidate Validation & Action:** Evaluated architectural pattern; selected star schema model (`dim_customers`, `dim_products`, `fact_orders`, `fact_order_items`) and segregated metadata tables (`pipeline_runs`, `quarantine_records`).

### Prompt Session: Core Pipeline & Validation Engine
* **Prompt 2:**
  > *"Implement a Python ingestion engine with Pydantic schema validation that checks missing fields, malformed dates, invalid foreign keys, intra-batch duplicate keys, and line total math integrity. Invalid rows must be routed to a quarantine dead-letter table with full raw record and diagnostic code."*
* **Candidate Validation & Action:** Reviewed validation logic; verified that parent order failures properly propagate to quarantine corresponding child order items without crashing the ingestion batch.

### Prompt Session: Analytical Workload Benchmarking Suite
* **Prompt 3:**
  > *"Create an analytical benchmarking suite that runs 4 workload categories on both baseline (unindexed) and optimized databases: Scan, Join, Aggregation, and Filtering. Collect EXPLAIN QUERY PLAN, measure warm latency across multiple iterations, and format a markdown comparative report."*
* **Candidate Validation & Action:** Refined Workload 3 (Aggregation) to use integer pre-aggregation over covering index rather than naive CTE materialization, ensuring measurable 6x to 7x speedup at scale.

### Prompt Session: Test Automation & Idempotency
* **Prompt 4:**
  > *"Generate a comprehensive pytest suite covering happy path, empty input, malformed input, orphan foreign keys, duplicates, idempotent reruns, and benchmark plan verification."*
* **Candidate Validation & Action:** Executed `pytest -v`; resolved Windows SQLite connection locking by wrapping database sessions in a safe context manager. Confirmed 17 out of 17 tests passed cleanly.
