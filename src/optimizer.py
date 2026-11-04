"""
Analytical Workload Optimizer & Benchmarking Suite.
Measures baseline vs optimized performance across:
1. Inefficient Scans (Full Table Scan vs Covering Index)
2. Inefficient Joins (Unindexed Nested Loop vs Indexed Joins)
3. Inefficient Aggregations (High-volume Join First vs Pushdown / CTE Pre-aggregation)
4. Inefficient Filtering (Non-sargable functions vs Sargable Range Predicates)
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List


@dataclass
class QueryBenchmarkResult:
    workload_id: str
    workload_name: str
    category: str
    baseline_query: str
    optimized_query: str
    baseline_plan: list[str]
    optimized_plan: list[str]
    baseline_avg_ms: float
    optimized_avg_ms: float
    speedup_ratio: float
    percentage_improvement: float
    row_count: int


# Workload Queries Definitions
WORKLOAD_QUERIES: Dict[str, Dict[str, Any]] = {
    "WORKLOAD_1_SCAN": {
        "name": "Date Range & Status High-Value Orders",
        "category": "Inefficient Scan vs Covering Index Scan",
        "description": (
            "Baseline executes a sequential table scan across all fact rows and uses a temporary B-tree for sorting. "
            "Optimized uses a composite covering index (order_date, order_status, customer_id, total_amount) avoiding heap lookups."
        ),
        "baseline_sql": """
            SELECT customer_id, order_date, total_amount 
            FROM fact_orders 
            WHERE order_date >= '2025-06-01' AND order_date <= '2025-08-31' 
              AND order_status = 'COMPLETED' 
            ORDER BY total_amount DESC 
            LIMIT 100;
        """,
        "optimized_sql": """
            SELECT customer_id, order_date, total_amount 
            FROM fact_orders 
            WHERE order_date >= '2025-06-01' AND order_date <= '2025-08-31' 
              AND order_status = 'COMPLETED' 
            ORDER BY total_amount DESC 
            LIMIT 100;
        """,
    },
    "WORKLOAD_2_JOIN": {
        "name": "Enterprise Customers Electronics Purchase History",
        "category": "Inefficient Join (Unindexed Scan) vs Indexed Foreign Key Join",
        "description": (
            "Baseline joins unindexed fact_orders and fact_order_items against dimensions, scanning orders. "
            "Optimized uses foreign key indexes (idx_fact_order_items_order_id, idx_fact_orders_customer_id) "
            "and filtered segment/category dimension indexes."
        ),
        "baseline_sql": """
            SELECT c.customer_name, p.product_name, fo.order_date, foi.quantity, foi.line_total
            FROM fact_order_items foi
            JOIN fact_orders fo ON foi.order_id = fo.order_id
            JOIN dim_customers c ON fo.customer_id = c.customer_id
            JOIN dim_products p ON foi.product_id = p.product_id
            WHERE c.segment = 'ENTERPRISE' AND p.category = 'Electronics'
            LIMIT 200;
        """,
        "optimized_sql": """
            SELECT c.customer_name, p.product_name, fo.order_date, foi.quantity, foi.line_total
            FROM fact_order_items foi
            JOIN fact_orders fo ON foi.order_id = fo.order_id
            JOIN dim_customers c ON fo.customer_id = c.customer_id
            JOIN dim_products p ON foi.product_id = p.product_id
            WHERE c.segment = 'ENTERPRISE' AND p.category = 'Electronics'
            LIMIT 200;
        """,
    },
    "WORKLOAD_3_AGGREGATION": {
        "name": "Product Category Revenue & Volume Aggregation",
        "category": "Inefficient String Grouping vs Pushdown Pre-aggregation & Covering Index",
        "description": (
            "Baseline performs an expensive join across 100,000+ unaggregated fact items and product dimensions "
            "before executing a high-cardinality string GROUP BY on p.category. "
            "Optimized pushes down the aggregation to numeric product_id directly over the covering index "
            "(idx_fact_order_items_covering), collapsing 100,000+ rows into ~1,000 rows before joining dimensions."
        ),
        "baseline_sql": """
            SELECT p.category, COUNT(foi.item_id) AS items_sold, ROUND(SUM(foi.line_total), 2) AS total_revenue
            FROM fact_order_items foi
            JOIN dim_products p ON foi.product_id = p.product_id
            GROUP BY p.category
            ORDER BY total_revenue DESC;
        """,
        "optimized_sql": """
            SELECT p.category, SUM(sub.items_sold) AS items_sold, ROUND(SUM(sub.total_revenue), 2) AS total_revenue
            FROM (
                SELECT product_id, COUNT(*) AS items_sold, SUM(line_total) AS total_revenue
                FROM fact_order_items
                GROUP BY product_id
            ) sub
            JOIN dim_products p ON sub.product_id = p.product_id
            GROUP BY p.category
            ORDER BY total_revenue DESC;
        """,
    },
    "WORKLOAD_4_FILTERING": {
        "name": "Annual Revenue Summary (Sargable vs Non-Sargable Filtering)",
        "category": "Non-Sargable Function Filter vs Sargable Index Range Filter",
        "description": (
            "Baseline wraps the date column in strftime('%Y', order_date), preventing index usage. "
            "Optimized rewrites to a sargable range condition (order_date >= ... AND order_date < ...) "
            "which immediately leverages the index."
        ),
        "baseline_sql": """
            SELECT COUNT(*) AS order_count, ROUND(SUM(total_amount), 2) AS gross_sales
            FROM fact_orders
            WHERE strftime('%Y', order_date) = '2025' AND order_status = 'COMPLETED';
        """,
        "optimized_sql": """
            SELECT COUNT(*) AS order_count, ROUND(SUM(total_amount), 2) AS gross_sales
            FROM fact_orders
            WHERE order_date >= '2025-01-01' AND order_date < '2026-01-01' AND order_status = 'COMPLETED';
        """,
    },
}


class WorkloadBenchmarkRunner:
    """Executes query plans and measures latency improvements between baseline and optimized engines."""

    def __init__(self, baseline_conn: sqlite3.Connection, optimized_conn: sqlite3.Connection):
        self.baseline_conn = baseline_conn
        self.optimized_conn = optimized_conn

    def get_query_plan(self, conn: sqlite3.Connection, sql: str) -> list[str]:
        """Runs EXPLAIN QUERY PLAN and returns formatted plan lines."""
        cursor = conn.cursor()
        cursor.execute(f"EXPLAIN QUERY PLAN {sql}")
        plan_rows = cursor.fetchall()
        return [f"id={r[0]}, parent={r[1]}, detail={r[3]}" for r in plan_rows]

    def measure_latency_ms(
        self, conn: sqlite3.Connection, sql: str, iterations: int = 5, warmup: int = 2
    ) -> tuple[float, int]:
        """Runs warmup and timed iterations, returning (avg_ms, row_count)."""
        cursor = conn.cursor()

        # Warmup runs to populate buffer cache
        for _ in range(warmup):
            cursor.execute(sql)
            _ = cursor.fetchall()

        timings: list[float] = []
        last_row_count = 0
        for _ in range(iterations):
            start = time.perf_counter()
            cursor.execute(sql)
            rows = cursor.fetchall()
            end = time.perf_counter()
            timings.append((end - start) * 1000.0)
            last_row_count = len(rows)

        avg_ms = sum(timings) / len(timings)
        return round(avg_ms, 3), last_row_count

    def run_all_benchmarks(self, iterations: int = 5) -> list[QueryBenchmarkResult]:
        """Runs all 4 workload benchmarks against baseline and optimized databases."""
        results: list[QueryBenchmarkResult] = []

        for w_id, w_info in WORKLOAD_QUERIES.items():
            base_sql = w_info["baseline_sql"].strip()
            opt_sql = w_info["optimized_sql"].strip()

            base_plan = self.get_query_plan(self.baseline_conn, base_sql)
            opt_plan = self.get_query_plan(self.optimized_conn, opt_sql)

            base_ms, base_count = self.measure_latency_ms(self.baseline_conn, base_sql, iterations)
            opt_ms, opt_count = self.measure_latency_ms(self.optimized_conn, opt_sql, iterations)

            # Prevent zero-division on ultra-fast queries
            safe_opt_ms = max(opt_ms, 0.001)
            speedup = round(base_ms / safe_opt_ms, 2)
            pct_improvement = round(max(0.0, ((base_ms - opt_ms) / max(base_ms, 0.001)) * 100.0), 2)

            results.append(
                QueryBenchmarkResult(
                    workload_id=w_id,
                    workload_name=w_info["name"],
                    category=w_info["category"],
                    baseline_query=base_sql,
                    optimized_query=opt_sql,
                    baseline_plan=base_plan,
                    optimized_plan=opt_plan,
                    baseline_avg_ms=base_ms,
                    optimized_avg_ms=opt_ms,
                    speedup_ratio=speedup,
                    percentage_improvement=pct_improvement,
                    row_count=opt_count,
                )
            )

        return results

    @staticmethod
    def format_markdown_report(results: list[QueryBenchmarkResult]) -> str:
        """Generates a professional markdown performance report with EXPLAIN plans."""
        md = []
        md.append("# Analytical Workload Performance Benchmark Report\n")
        md.append("Comparative analysis of baseline (unoptimized) vs. optimized data storage & query plans.\n")
        md.append("## Executive Summary\n")
        md.append("| Workload ID | Optimization Category | Baseline (ms) | Optimized (ms) | Speedup | Latency Reduction |")
        md.append("|:---|:---|:---:|:---:|:---:|:---:|")

        for r in results:
            md.append(
                f"| **{r.workload_id}** | {r.category} | {r.baseline_avg_ms:.2f} ms | "
                f"**{r.optimized_avg_ms:.2f} ms** | **{r.speedup_ratio}x** | **{r.percentage_improvement:.1f}%** |"
            )

        md.append("\n---\n")
        md.append("## Detailed Plan & Execution Breakdown\n")

        for r in results:
            md.append(f"### {r.workload_id}: {r.workload_name}")
            md.append(f"**Optimization Focus:** {r.category}\n")
            md.append(f"- **Baseline Latency:** `{r.baseline_avg_ms:.3f} ms`")
            md.append(f"- **Optimized Latency:** `{r.optimized_avg_ms:.3f} ms`")
            md.append(f"- **Measured Speedup:** `{r.speedup_ratio}x` ({r.percentage_improvement:.1f}% latency drop)")
            md.append(f"- **Result Rows:** `{r.row_count}`\n")

            md.append("#### Query Execution Plans Comparison\n")
            md.append("```text")
            md.append("--- BASELINE EXPLAIN QUERY PLAN ---")
            for line in r.baseline_plan:
                md.append(f"  {line}")
            md.append("\n--- OPTIMIZED EXPLAIN QUERY PLAN ---")
            for line in r.optimized_plan:
                md.append(f"  {line}")
            md.append("```\n")

            md.append("#### Query Text Comparison\n")
            md.append("<details><summary>Click to expand queries</summary>\n")
            md.append("**Baseline SQL:**\n```sql\n" + r.baseline_query + "\n```\n")
            md.append("**Optimized SQL:**\n```sql\n" + r.optimized_query + "\n```\n")
            md.append("</details>\n\n")

        return "\n".join(md)
