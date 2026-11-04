# Analytical Workload Performance Benchmark Report

Comparative analysis of baseline (unoptimized) vs. optimized data storage & query plans.

## Executive Summary

| Workload ID | Optimization Category | Baseline (ms) | Optimized (ms) | Speedup | Latency Reduction |
|:---|:---|:---:|:---:|:---:|:---:|
| **WORKLOAD_1_SCAN** | Inefficient Scan vs Covering Index Scan | 14.35 ms | **4.42 ms** | **3.24x** | **69.2%** |
| **WORKLOAD_2_JOIN** | Inefficient Join (Unindexed Scan) vs Indexed Foreign Key Join | 2.30 ms | **0.87 ms** | **2.63x** | **61.9%** |
| **WORKLOAD_3_AGGREGATION** | Inefficient String Grouping vs Pushdown Pre-aggregation & Covering Index | 272.62 ms | **29.88 ms** | **9.12x** | **89.0%** |
| **WORKLOAD_4_FILTERING** | Non-Sargable Function Filter vs Sargable Index Range Filter | 46.77 ms | **12.18 ms** | **3.84x** | **74.0%** |

---

## Detailed Plan & Execution Breakdown

### WORKLOAD_1_SCAN: Date Range & Status High-Value Orders
**Optimization Focus:** Inefficient Scan vs Covering Index Scan

- **Baseline Latency:** `14.348 ms`
- **Optimized Latency:** `4.424 ms`
- **Measured Speedup:** `3.24x` (69.2% latency drop)
- **Result Rows:** `100`

#### Query Execution Plans Comparison

```text
--- BASELINE EXPLAIN QUERY PLAN ---
  id=4, parent=0, detail=SCAN fact_orders
  id=24, parent=0, detail=USE TEMP B-TREE FOR ORDER BY

--- OPTIMIZED EXPLAIN QUERY PLAN ---
  id=4, parent=0, detail=SEARCH fact_orders USING COVERING INDEX idx_fact_orders_date_status (order_date>? AND order_date<?)
  id=23, parent=0, detail=USE TEMP B-TREE FOR ORDER BY
```

#### Query Text Comparison

<details><summary>Click to expand queries</summary>

**Baseline SQL:**
```sql
SELECT customer_id, order_date, total_amount 
            FROM fact_orders 
            WHERE order_date >= '2025-06-01' AND order_date <= '2025-08-31' 
              AND order_status = 'COMPLETED' 
            ORDER BY total_amount DESC 
            LIMIT 100;
```

**Optimized SQL:**
```sql
SELECT customer_id, order_date, total_amount 
            FROM fact_orders 
            WHERE order_date >= '2025-06-01' AND order_date <= '2025-08-31' 
              AND order_status = 'COMPLETED' 
            ORDER BY total_amount DESC 
            LIMIT 100;
```

</details>


### WORKLOAD_2_JOIN: Enterprise Customers Electronics Purchase History
**Optimization Focus:** Inefficient Join (Unindexed Scan) vs Indexed Foreign Key Join

- **Baseline Latency:** `2.296 ms`
- **Optimized Latency:** `0.874 ms`
- **Measured Speedup:** `2.63x` (61.9% latency drop)
- **Result Rows:** `200`

#### Query Execution Plans Comparison

```text
--- BASELINE EXPLAIN QUERY PLAN ---
  id=6, parent=0, detail=SCAN foi
  id=8, parent=0, detail=SEARCH fo USING INTEGER PRIMARY KEY (rowid=?)
  id=11, parent=0, detail=SEARCH c USING INTEGER PRIMARY KEY (rowid=?)
  id=16, parent=0, detail=SEARCH p USING INTEGER PRIMARY KEY (rowid=?)

--- OPTIMIZED EXPLAIN QUERY PLAN ---
  id=7, parent=0, detail=SEARCH p USING INDEX idx_dim_products_category (category=?)
  id=12, parent=0, detail=SEARCH foi USING COVERING INDEX idx_fact_order_items_covering (product_id=?)
  id=16, parent=0, detail=SEARCH fo USING INTEGER PRIMARY KEY (rowid=?)
  id=19, parent=0, detail=SEARCH c USING INTEGER PRIMARY KEY (rowid=?)
```

#### Query Text Comparison

<details><summary>Click to expand queries</summary>

**Baseline SQL:**
```sql
SELECT c.customer_name, p.product_name, fo.order_date, foi.quantity, foi.line_total
            FROM fact_order_items foi
            JOIN fact_orders fo ON foi.order_id = fo.order_id
            JOIN dim_customers c ON fo.customer_id = c.customer_id
            JOIN dim_products p ON foi.product_id = p.product_id
            WHERE c.segment = 'ENTERPRISE' AND p.category = 'Electronics'
            LIMIT 200;
```

**Optimized SQL:**
```sql
SELECT c.customer_name, p.product_name, fo.order_date, foi.quantity, foi.line_total
            FROM fact_order_items foi
            JOIN fact_orders fo ON foi.order_id = fo.order_id
            JOIN dim_customers c ON fo.customer_id = c.customer_id
            JOIN dim_products p ON foi.product_id = p.product_id
            WHERE c.segment = 'ENTERPRISE' AND p.category = 'Electronics'
            LIMIT 200;
```

</details>


### WORKLOAD_3_AGGREGATION: Product Category Revenue & Volume Aggregation
**Optimization Focus:** Inefficient String Grouping vs Pushdown Pre-aggregation & Covering Index

- **Baseline Latency:** `272.625 ms`
- **Optimized Latency:** `29.882 ms`
- **Measured Speedup:** `9.12x` (89.0% latency drop)
- **Result Rows:** `5`

#### Query Execution Plans Comparison

```text
--- BASELINE EXPLAIN QUERY PLAN ---
  id=8, parent=0, detail=SCAN foi
  id=10, parent=0, detail=SEARCH p USING INTEGER PRIMARY KEY (rowid=?)
  id=13, parent=0, detail=USE TEMP B-TREE FOR GROUP BY
  id=58, parent=0, detail=USE TEMP B-TREE FOR ORDER BY

--- OPTIMIZED EXPLAIN QUERY PLAN ---
  id=2, parent=0, detail=CO-ROUTINE sub
  id=8, parent=2, detail=SCAN fact_order_items USING COVERING INDEX idx_fact_order_items_covering
  id=47, parent=0, detail=SCAN sub
  id=50, parent=0, detail=SEARCH p USING INTEGER PRIMARY KEY (rowid=?)
  id=53, parent=0, detail=USE TEMP B-TREE FOR GROUP BY
  id=96, parent=0, detail=USE TEMP B-TREE FOR ORDER BY
```

#### Query Text Comparison

<details><summary>Click to expand queries</summary>

**Baseline SQL:**
```sql
SELECT p.category, COUNT(foi.item_id) AS items_sold, ROUND(SUM(foi.line_total), 2) AS total_revenue
            FROM fact_order_items foi
            JOIN dim_products p ON foi.product_id = p.product_id
            GROUP BY p.category
            ORDER BY total_revenue DESC;
```

**Optimized SQL:**
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

</details>


### WORKLOAD_4_FILTERING: Annual Revenue Summary (Sargable vs Non-Sargable Filtering)
**Optimization Focus:** Non-Sargable Function Filter vs Sargable Index Range Filter

- **Baseline Latency:** `46.771 ms`
- **Optimized Latency:** `12.184 ms`
- **Measured Speedup:** `3.84x` (74.0% latency drop)
- **Result Rows:** `1`

#### Query Execution Plans Comparison

```text
--- BASELINE EXPLAIN QUERY PLAN ---
  id=3, parent=0, detail=SCAN fact_orders

--- OPTIMIZED EXPLAIN QUERY PLAN ---
  id=3, parent=0, detail=SEARCH fact_orders USING COVERING INDEX idx_fact_orders_date_status (order_date>? AND order_date<?)
```

#### Query Text Comparison

<details><summary>Click to expand queries</summary>

**Baseline SQL:**
```sql
SELECT COUNT(*) AS order_count, ROUND(SUM(total_amount), 2) AS gross_sales
            FROM fact_orders
            WHERE strftime('%Y', order_date) = '2025' AND order_status = 'COMPLETED';
```

**Optimized SQL:**
```sql
SELECT COUNT(*) AS order_count, ROUND(SUM(total_amount), 2) AS gross_sales
            FROM fact_orders
            WHERE order_date >= '2025-01-01' AND order_date < '2026-01-01' AND order_status = 'COMPLETED';
```

</details>

