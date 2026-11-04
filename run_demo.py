"""
Unified Command-Line Interface and Demo Runner.
Executes the complete end-to-end data engineering lifecycle:
1. Data Generation (Starter or Scalable)
2. Pipeline Ingestion & Validation
3. Idempotent Target Publishing
4. Data Quality Reconciliation
5. Analytical Workload Benchmarking (Baseline vs Optimized)
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Add project root to path
base_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(base_dir))

from src.config import PipelineConfig, get_db_connection
from src.generator import SyntheticDataGenerator
from src.logger import get_logger
from src.optimizer import WorkloadBenchmarkRunner
from src.pipeline import DataEngineeringPipeline
from src.reconciliation import DataReconciler


def main():
    parser = argparse.ArgumentParser(description="Analytical Workload Optimization Suite")
    parser.add_argument(
        "--scale",
        action="store_true",
        help="Run large-scale benchmark (10,000+ orders). Default runs starter sample (1,000 orders).",
    )
    parser.add_argument(
        "--orders",
        type=int,
        default=None,
        help="Custom order count for generation.",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=5,
        help="Number of iterations for query benchmarking. (Default: 5)",
    )
    args = parser.parse_args()

    config = PipelineConfig()
    config.ensure_directories()
    logger = get_logger("demo_runner", log_file=config.log_file)

    logger.info("=" * 70)
    logger.info("STARTING ANALYTICAL WORKLOAD OPTIMIZATION PIPELINE DEMO")
    logger.info("=" * 70)

    # 1. Determine dataset scale
    if args.orders:
        num_orders = args.orders
        num_customers = min(5000, max(50, num_orders // 10))
        num_products = min(1000, max(25, num_orders // 20))
    elif args.scale:
        num_orders = 25000  # fast and measurable
        num_customers = 2000
        num_products = 500
    else:
        num_orders = 2000
        num_customers = 200
        num_products = 100

    logger.info(f"Target Workload Scale: {num_orders:,} orders, {num_customers:,} customers, {num_products:,} products")

    # 2. Generate Reproducible Synthetic Data
    logger.info("\n--- STEP 1: GENERATING SYNTHETIC DATA & INJECTING ANOMALIES ---")
    gen = SyntheticDataGenerator(seed=config.random_seed)
    customers, products = gen.generate_dimensions(num_customers=num_customers, num_products=num_products)
    orders, items, ground_truth = gen.generate_workload(
        num_orders=num_orders,
        customers=customers,
        products=products,
        inject_anomalies=True,
        anomaly_rate=0.03,
    )

    dataset_paths = gen.save_dataset(
        output_dir=config.sample_dir,
        customers=customers,
        products=products,
        orders=orders,
        items=items,
        ground_truth=ground_truth,
        prefix="batch_01",
    )
    logger.info(f"Saved dataset files to {config.sample_dir}")
    logger.info(f"Injected deliberate anomalies: {ground_truth['anomalies_injected']}")

    # 3. Clean and Initialize Databases
    if config.baseline_db_path.exists():
        config.baseline_db_path.unlink()
    if config.optimized_db_path.exists():
        config.optimized_db_path.unlink()

    # 4. Ingest and Publish to Baseline Pipeline
    logger.info("\n--- STEP 2: RUNNING INGESTION PIPELINE (BASELINE DATABASE) ---")
    baseline_pipe = DataEngineeringPipeline(
        db_path=config.baseline_db_path,
        is_optimized=False,
        log_file=config.log_file,
    )
    baseline_pipe.load_dimensions(customers, products)
    base_res = baseline_pipe.execute_batch(
        orders_file=dataset_paths["orders_jsonl"],
        items_file=dataset_paths["items_jsonl"],
        run_id="run_baseline_batch_01",
    )
    logger.info(f"Baseline Ingestion: {base_res.status} in {base_res.duration_seconds:.2f}s | "
                f"Valid Orders: {base_res.valid_orders:,} | Valid Items: {base_res.valid_items:,} | "
                f"Quarantined: {base_res.quarantined_count:,}")

    # 5. Ingest and Publish to Optimized Pipeline
    logger.info("\n--- STEP 3: RUNNING INGESTION PIPELINE (OPTIMIZED DATABASE) ---")
    optimized_pipe = DataEngineeringPipeline(
        db_path=config.optimized_db_path,
        is_optimized=True,
        log_file=config.log_file,
    )
    optimized_pipe.load_dimensions(customers, products)
    opt_res = optimized_pipe.execute_batch(
        orders_file=dataset_paths["orders_jsonl"],
        items_file=dataset_paths["items_jsonl"],
        run_id="run_optimized_batch_01",
    )
    logger.info(f"Optimized Ingestion: {opt_res.status} in {opt_res.duration_seconds:.2f}s | "
                f"Valid Orders: {opt_res.valid_orders:,} | Valid Items: {opt_res.valid_items:,} | "
                f"Quarantined: {opt_res.quarantined_count:,}")

    # 6. Test Idempotency (Safe Rerun)
    logger.info("\n--- STEP 4: VERIFYING IDEMPOTENCY (SAFE RERUN OF SAME INPUT) ---")
    rerun_res = optimized_pipe.execute_batch(
        orders_file=dataset_paths["orders_jsonl"],
        items_file=dataset_paths["items_jsonl"],
        run_id="run_optimized_batch_01_rerun",
    )
    logger.info(f"Rerun Completed: Status={rerun_res.status} | Total Valid Orders remains: {rerun_res.valid_orders:,}")

    # 7. Data Quality & Reconciliation
    logger.info("\n--- STEP 5: RUNNING DATA QUALITY & RECONCILIATION AUDIT ---")
    with get_db_connection(config.optimized_db_path) as conn:
        reconciler = DataReconciler(conn)
        rec_report = reconciler.reconcile(raw_orders=orders, raw_items=items)

    rec_md = DataReconciler.format_report_markdown(rec_report)
    rec_file = config.output_dir / "reconciliation_report.md"
    rec_file.write_text(rec_md, encoding="utf-8")
    logger.info(f"Reconciliation Status: {'PASSED' if rec_report.is_valid else 'FAILED'}")
    logger.info(f"Saved Reconciliation Report to: {rec_file}")

    # 8. Analytical Workload Benchmarking
    logger.info("\n--- STEP 6: BENCHMARKING ANALYTICAL WORKLOAD (BASELINE VS OPTIMIZED) ---")
    with get_db_connection(config.baseline_db_path) as b_conn, \
         get_db_connection(config.optimized_db_path) as o_conn:
        runner = WorkloadBenchmarkRunner(b_conn, o_conn)
        bench_results = runner.run_all_benchmarks(iterations=args.iterations)

    bench_md = WorkloadBenchmarkRunner.format_markdown_report(bench_results)
    bench_file = config.output_dir / "benchmark_report.md"
    bench_file.write_text(bench_md, encoding="utf-8")
    logger.info(f"Saved Workload Benchmark Report to: {bench_file}\n")

    # 9. Print Formatted Table to stdout
    print("\n" + "=" * 90)
    print("ANALYTICAL WORKLOAD BENCHMARK SUMMARY")
    print("=" * 90)
    print(f"{'Workload ID':<22} | {'Baseline (ms)':<15} | {'Optimized (ms)':<15} | {'Speedup':<10} | {'Latency Reduction':<18}")
    print("-" * 90)
    for r in bench_results:
        print(f"{r.workload_id:<22} | {r.baseline_avg_ms:>11.2f} ms | {r.optimized_avg_ms:>11.2f} ms | {r.speedup_ratio:>8.2f}x | {r.percentage_improvement:>16.1f}%")
    print("=" * 90)

    logger.info("\nPipeline execution, reconciliation, and benchmarking completed successfully!")


if __name__ == "__main__":
    main()
