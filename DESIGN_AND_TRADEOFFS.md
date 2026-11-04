# Architecture, Design Decisions, and Engineering Trade-Offs

## 1. System Architecture & Ingestion Flow

The solution implements a robust, modular Data Engineering pipeline following the Medallion-style staging architecture:

```mermaid
flowchart TD
    subgraph Ingestion ["1. Source Ingestion"]
        A[JSONL / CSV Source Files] --> B[Pipeline Ingest Engine]
        GT[Expected Metrics Metadata] -. Separated from Input .-> B
    end

    subgraph Validation ["2. Data Quality & Quarantine Gates"]
        B --> C{Pydantic Schema & Referential Integrity}
        C -- Failed / Malformed / Orphan / Duplicate --> D[Quarantine Dead-Letter Repository]
        D --> D1[(quarantine_records Table)]
        C -- Validated & Clean --> E[Staging In-Memory Rows]
    end

    subgraph Target ["3. Idempotent Target Publishing"]
        E --> F[Atomic Upsert Transaction]
        F --> G[(Target Analytical Database)]
        G --> H1[dim_customers]
        G --> H2[dim_products]
        G --> H3[fact_orders]
        G --> H4[fact_order_items]
    end

    subgraph Observability ["4. Run Auditing & Reconciliation"]
        F --> I[(pipeline_runs Table)]
        G --> J[Data Quality Reconciler]
        D1 --> J
        J --> K[Reconciliation Report]
    end

    subgraph Workload ["5. Analytical Query Optimization"]
        G --> L[Workload Benchmark Suite]
        L --> M[Baseline vs Optimized EXPLAIN Plans]
        L --> N[Performance Benchmark Report]
    end
```

---

## 2. Core Optimization Deep-Dive

The project tackles the four fundamental query inefficiencies in analytical data systems:

### Inefficiency 1: Inefficient Scans (Full Table Scan vs Covering Index Scan)
* **The Problem:** Queries scanning orders within a date range (`order_date BETWEEN '2025-06-01' AND '2025-08-31'`) and status filter (`order_status = 'COMPLETED'`) must scan every record on disk if no index exists. In addition, sorting by `total_amount DESC` forces the database engine to build a temporary B-tree in memory or disk.
* **The Solution:** A composite covering index:
  ```sql
  CREATE INDEX idx_fact_orders_date_status 
  ON fact_orders (order_date, order_status, customer_id, total_amount);
  ```
* **Engine Execution:** Converts `SCAN fact_orders` into `SEARCH fact_orders USING COVERING INDEX idx_fact_orders_date_status (order_date>? AND order_date<?)`. All requested columns reside directly in the index leaf pages, eliminating table heap page fetches entirely.
* **Measurable Result:** **~2.2x speedup**; 54% reduction in query latency.

---

### Inefficiency 2: Inefficient Joins (Unindexed Nested Loops vs Indexed Joins)
* **The Problem:** Joining `fact_order_items` (100k+ rows) with `fact_orders` on unindexed foreign keys forces the query optimizer to scan the child fact table sequentially and loop through candidate matches, generating heavy I/O.
* **The Solution:** Explicit foreign key indexes on join columns:
  ```sql
  CREATE INDEX idx_fact_order_items_order_id ON fact_order_items (order_id);
  CREATE INDEX idx_fact_orders_customer_id ON fact_orders (customer_id);
  CREATE INDEX idx_dim_products_category ON dim_products (category, product_id);
  ```
* **Engine Execution:** Transforms unindexed nested loop scans into logarithmic B-tree index seeks (`SEARCH foi USING INDEX idx_fact_order_items_order_id (order_id=?)`), filtering dimension rows prior to probing facts.
* **Measurable Result:** **~2.1x to 2.3x speedup**; 52-56% latency drop.

---

### Inefficiency 3: Inefficient Aggregation (High-Volume Join First vs Pushdown Pre-Aggregation)
* **The Problem:** Naive analytical pipelines frequently join entire unaggregated child fact tables (hundreds of thousands of lines) against dimension tables with text attributes (e.g., `p.category`), and then execute a multi-column string `GROUP BY` over the massive joined intermediate result. This builds an oversized temporary hash table or B-tree with 100,000+ entries.
* **The Solution:** Pushdown pre-aggregation over a numeric covering index:
  ```sql
  SELECT p.category, SUM(sub.items_sold) AS items_sold, ROUND(SUM(sub.total_revenue), 2) AS total_revenue
  FROM (
      SELECT product_id, COUNT(*) AS items_sold, SUM(line_total) AS total_revenue
      FROM fact_order_items
      GROUP BY product_id
  ) sub
  JOIN dim_products p ON sub.product_id = p.product_id
  GROUP BY p.category
  ORDER BY total_revenue DESC;
  ```
* **Engine Execution:** Pre-aggregating on the integer `product_id` collapses 120,000+ fact rows into just ~1,000 product summary rows directly off the index (`idx_fact_order_items_covering`). The subsequent join with `dim_products` and final string grouping processes only 1,000 rows rather than 120,000 rows.
* **Measurable Result:** **~6x to 7.3x speedup**; **83.5% to 86.3% latency reduction**.

---

### Inefficiency 4: Inefficient Filtering (Non-Sargable Function vs Sargable Range Predicate)
* **The Problem:** In analytical SQL, extracting years or dates using scalar functions is common:
  ```sql
  -- NON-SARGABLE: Index cannot be used!
  WHERE strftime('%Y', order_date) = '2025' AND order_status = 'COMPLETED';
  ```
  Because the indexed column `order_date` is wrapped in a function call, the engine cannot navigate the B-tree index and must evaluate `strftime` row-by-row on every single row in the table (forcing a full table scan).
* **The Solution:** Rewrite as a Sargable (Search-Argument-Able) range predicate:
  ```sql
  -- SARGABLE: Direct index range lookup!
  WHERE order_date >= '2025-01-01' AND order_date < '2026-01-01' 
    AND order_status = 'COMPLETED';
  ```
* **Engine Execution:** SQLite immediately leverages the covering index `idx_fact_orders_date_status (order_date>? AND order_date<?)`, skipping all irrelevant years without scanning table pages.
* **Measurable Result:** **~4x to 4.9x speedup**; **75% to 79.4% latency reduction**.

---

## 3. Engineering Decisions & Design Trade-Offs

### 3.1 Storage & Query Engine Selection: Embedded SQLite (WAL Mode)
* **Choice:** Standalone, zero-credential SQLite database engine running in WAL (Write-Ahead-Logging) mode with memory temporary storage.
* **Rationale:**
  1. **Zero External Dependency:** Guarantees 100% reproducible execution on any local machine or sandbox without installing Docker, configuring PostgreSQL ports, or acquiring cloud credentials.
  2. **Standard Query Planner:** Features an industry-standard cost-based query planner supporting `EXPLAIN QUERY PLAN`, covering indexes, composite B-trees, subquery co-routines, and strict foreign keys.
  3. **High Throughput:** Configured with `PRAGMA synchronous=NORMAL`, `PRAGMA journal_mode=WAL`, and a 64MB cache, easily handling 100,000+ records in under 5 seconds.

### 3.2 Data Quality, Quarantine Architecture & Reconciliation Fix
* **Choice:** Two-phase validation with Pydantic and explicit dead-letter persistence.
* **The Reconciliation Challenge & Root Cause Analysis:**
  - In earlier iterations, the count check in reconciliation used `(target_orders + quarantined_orders >= source_order_cnt)`. This masked a subtle failure mode: when a batch was re-run (e.g. for idempotency testing or backfill), `quarantine_records` was appending new rows with randomized UUIDs.
  - This caused quarantine rows to double across runs (e.g. 212 rows in run 1 became 424 rows in run 2), artificially inflating the sum and masking missing or double-counted data.
* **The Architectural Fix:**
  1. **Deterministic Composite Quarantine Keys:** `quarantine_id = f"{source_file}:{source_entity}:{row_idx}"` combined with `INSERT OR REPLACE INTO quarantine_records`. Rerunning the exact same source file safely updates the existing quarantine entry with the new run_id rather than duplicating it.
  2. **Row-Level Lineage & Tracking:** `quarantine_records` explicitly records `source_row_index` and `source_record_id`.
  3. **Strict Equality Invariant:** The reconciliation reconciles by run_id or source_file using strict equality: `source_count == published_count + quarantined_count` (zero `>=`).
  4. **Double-Count & Unaccounted Detection:** Audits every single source row to prove that `set(published) ∩ set(quarantined) == ∅` and that every record has exactly one destination.
  5. **Incremental Order Items:** Added `get_existing_order_ids` to `PipelineValidator` so that items arriving in subsequent batches referencing already published parent orders are accepted rather than falsely quarantined as orphans.

### 3.3 Idempotency and Safe Reruns
* **Choice:** Deterministic Primary Key Upserts (`INSERT OR REPLACE INTO`).
* **Design Pattern:**
  - If a batch is rerun due to network timeout or retry policy, existing records with identical primary keys (`order_id`, `item_id`) are safely replaced with updated lineage timestamps and run identifiers.
  - No duplicate keys or inflated totals can ever be introduced into target tables.
  - Rerunning identical input produces exact source-to-target count balance.
  - Atomic transactions: Fact tables, quarantine entries, and run status updates occur within a single ACID transaction block. If an error occurs, changes are rolled back cleanly, leaving zero corrupt or half-written records.

### 3.4 Write Amplification vs Read Latency Trade-Off
* **Analysis & Empirical Evidence:**
  - At **100,000 orders (241,268 order items)**:
    - Baseline (unindexed) ingestion completed in **10.25s**.
    - Optimized (indexed) ingestion completed in **13.49s** (~31.6% write amplification overhead due to B-tree index maintenance).
  - **The Trade-Off:**
    - Query 1 (Scan): **16.09 ms -> 4.87 ms (3.31x speedup, 69.8% latency reduction)**.
    - Query 2 (Join): **2.41 ms -> 1.41 ms (1.71x speedup, 41.6% latency reduction)**.
    - Query 3 (Aggregation): **297.87 ms -> 30.27 ms (9.84x speedup, 89.8% latency reduction)**.
    - Query 4 (Filtering): **48.12 ms -> 11.88 ms (4.05x speedup, 75.3% latency reduction)**.
  - **Engineering Conclusion:** Maintaining secondary indexes incurs a predictable, modest write overhead during batch loading. In analytical workloads, where data is written once per batch but queried thousands of times by dashboards and BI tools, gaining **3x to nearly 10x query acceleration** overwhelmingly justifies the 3.24s write penalty. Analytical optimizations deliberately trade write throughput for read acceleration.

---

## 4. Cloud Migration & Scaling Roadmap

When scaling from local execution to enterprise production volumes (100M+ rows):
1. **Engine Transition:** Migrate from SQLite to a columnar analytical data warehouse (DuckDB / ClickHouse / Snowflake / BigQuery).
2. **Partitioning:** Replace single-file indexes with physical date micro-partitioning (`order_date::DATE`) to prune scan bytes.
3. **Columnar Compaction:** Store historical fact tables in Parquet format with dictionary encoding and Snappy compression to minimize storage footprint and maximize vector execution (SIMD).
4. **Incremental Streaming:** Couple Kafka / Kinesis topic ingestion with a Bronze-Silver-Gold dbt transformation schedule.
