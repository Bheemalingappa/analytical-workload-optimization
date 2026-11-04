"""
Tests for Query Optimization, Execution Plans, and Speedup verification.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from src.config import get_db_connection
from src.generator import SyntheticDataGenerator
from src.optimizer import WorkloadBenchmarkRunner, WORKLOAD_QUERIES
from src.pipeline import DataEngineeringPipeline


def test_query_plans_and_correctness():
    """Verify that optimized schema produces covering index / search plans and matching query outputs."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        base_db = Path(tmp_dir) / "baseline.db"
        opt_db = Path(tmp_dir) / "optimized.db"

        # Generate small workload
        gen = SyntheticDataGenerator(seed=42)
        customers, products = gen.generate_dimensions(num_customers=50, num_products=25)
        orders, items, gt = gen.generate_workload(
            num_orders=500,
            customers=customers,
            products=products,
            inject_anomalies=False,
        )

        paths = gen.save_dataset(Path(tmp_dir), customers, products, orders, items, gt, prefix="bench")

        # Load into baseline (no indexes)
        base_pipe = DataEngineeringPipeline(db_path=base_db, is_optimized=False)
        base_pipe.load_dimensions(customers, products)
        base_pipe.execute_batch(paths["orders_jsonl"], paths["items_jsonl"])

        # Load into optimized (with indexes)
        opt_pipe = DataEngineeringPipeline(db_path=opt_db, is_optimized=True)
        opt_pipe.load_dimensions(customers, products)
        opt_pipe.execute_batch(paths["orders_jsonl"], paths["items_jsonl"])

        with get_db_connection(base_db) as b_conn, get_db_connection(opt_db) as o_conn:
            runner = WorkloadBenchmarkRunner(b_conn, o_conn)

            # Test Workload 1: Scan Optimization
            w1 = WORKLOAD_QUERIES["WORKLOAD_1_SCAN"]
            b_plan = runner.get_query_plan(b_conn, w1["baseline_sql"])
            o_plan = runner.get_query_plan(o_conn, w1["optimized_sql"])

            # Baseline must SCAN table, Optimized must SEARCH using COVERING INDEX
            assert any("SCAN fact_orders" in line for line in b_plan)
            assert any("COVERING INDEX" in line or "USING INDEX" in line for line in o_plan)

            # Test Workload 4: Filtering Optimization (Sargable rewrite)
            w4 = WORKLOAD_QUERIES["WORKLOAD_4_FILTERING"]
            b_plan_4 = runner.get_query_plan(b_conn, w4["baseline_sql"])
            o_plan_4 = runner.get_query_plan(o_conn, w4["optimized_sql"])

            # Baseline wraps in strftime so it must SCAN
            assert any("SCAN fact_orders" in line for line in b_plan_4)
            # Optimized sargable query uses INDEX
            assert any("USING INDEX" in line or "COVERING INDEX" in line for line in o_plan_4)

            # Verify analytical results match identically between baseline and optimized
            b_cursor = b_conn.cursor()
            o_cursor = o_conn.cursor()

            b_cursor.execute(w4["baseline_sql"])
            o_cursor.execute(w4["optimized_sql"])
            assert b_cursor.fetchone() == o_cursor.fetchone()


def test_workload_benchmark_runner_execution():
    """Verify that the benchmark runner executes all workloads without exceptions."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        base_db = Path(tmp_dir) / "baseline.db"
        opt_db = Path(tmp_dir) / "optimized.db"

        gen = SyntheticDataGenerator(seed=42)
        customers, products = gen.generate_dimensions(num_customers=20, num_products=10)
        orders, items, gt = gen.generate_workload(
            num_orders=100, customers=customers, products=products, inject_anomalies=False
        )
        paths = gen.save_dataset(Path(tmp_dir), customers, products, orders, items, gt, prefix="bench_quick")

        base_pipe = DataEngineeringPipeline(db_path=base_db, is_optimized=False)
        base_pipe.load_dimensions(customers, products)
        base_pipe.execute_batch(paths["orders_jsonl"], paths["items_jsonl"])

        opt_pipe = DataEngineeringPipeline(db_path=opt_db, is_optimized=True)
        opt_pipe.load_dimensions(customers, products)
        opt_pipe.execute_batch(paths["orders_jsonl"], paths["items_jsonl"])

        with get_db_connection(base_db) as b_conn, get_db_connection(opt_db) as o_conn:
            runner = WorkloadBenchmarkRunner(b_conn, o_conn)
            results = runner.run_all_benchmarks(iterations=2)
            assert len(results) == 4
            for r in results:
                assert r.baseline_avg_ms >= 0
                assert r.optimized_avg_ms >= 0
                assert len(r.baseline_plan) > 0
                assert len(r.optimized_plan) > 0
