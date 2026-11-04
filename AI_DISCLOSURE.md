# AI Use Disclosure & Prompt History

In accordance with the **YottaFlex AI Candidate Project Submission Guidelines**, this document transparently details the utilization of AI coding assistance, prompt history, human oversight, architectural validation, and engineering judgment exercised throughout the development of this project.

---

## 1. Transparency Summary

| Assessment Metric | Candidate Details |
|:---|:---|
| **Candidate Name** | Bheema Lingappa |
| **Assessment** | YottaFlex AI Round 3: 72-Hour Code Challenge |
| **Problem Statement** | Analytical Workload Optimization & Robust Data Pipeline |
| **AI Assistants Used** | ChatGPT (OpenAI) and Google Antigravity |
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

### Prompt Session: Senior Data Engineer Code Review & Reconciliation Hardening
* **Prompt 5:**
  > *"Audit the existing Data Engineering project: investigate quarantine generation, replace the '>=' reconciliation heuristic with exact source-to-target accounting, prevent duplicate quarantine entries across reruns, implement row-level traceability, add tests for unexpected schema changes, partial failure/rollback/retry, and incremental items, and verify scalability at 2k, 25k, and 100k records."*
* **Candidate Validation & Action:**
  - Diagnosed that append-only quarantine inserts caused double-counting on reruns. Designed deterministic composite keys (`source_file:entity:row_index`) with `INSERT OR REPLACE` to ensure complete rerun idempotency for both fact tables and quarantine logs.
  - Implemented exact equality reconciliation (`source == published + quarantined`), verifying that every source row has exactly one destination and reporting full error code diagnostics.
  - Added schema constraint `model_config = {"extra": "forbid"}` to reject unexpected schema fields.
  - Expanded automated test suite from 17 to 24 tests, verifying all pass with 100% success.
  - Benchmarked at 2,000, 25,000, and 100,000 records, validating scalability and query acceleration up to 9.84x.

### Additional Prompt Record: Original YottaFlex Assignment Brief

**AI Tool:** Google Antigravity  
**Status:** Confirmed submitted by candidate  
**Type:** Original assignment brief

The complete YottaFlex assignment brief was provided to Google Antigravity. It included:

- Business Context / Problem
- Objective and Scope
- Primary Actors / Systems
- Business Workflow and Rules
- Entities and Relationships
- ID / Nomenclature Conventions
- Implementation Instructions
- Suggested Volume and Coverage
- Required Edge Cases / Negative Scenarios
- Validation Rules / Acceptance Criteria
- Evaluation Focus
- 72-Hour Deliverables
- Scope Guidance

**Purpose:** To establish the project requirements, expected implementation, validation rules, and evaluation criteria.

**Note:** This records the original assignment brief and does not represent a complete transcript of all subsequent AI interactions.
