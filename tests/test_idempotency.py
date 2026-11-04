"""
Tests for Idempotency, Safe Reruns, and Backfill handling.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from src.config import get_db_connection
from src.pipeline import DataEngineeringPipeline


def test_pipeline_idempotent_rerun():
    """Verify that running the same batch multiple times does not produce duplicate records."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "idempotency.db"
        pipe = DataEngineeringPipeline(db_path=db_path)

        customers = [
            {
                "customer_id": 1,
                "customer_code": "CUST-01",
                "customer_name": "Acme Inc",
                "segment": "ENTERPRISE",
                "country": "US",
                "signup_date": "2024-01-01",
                "is_active": 1,
                "updated_at": "2025-01-01T00:00:00Z",
            }
        ]
        products = [
            {
                "product_id": 10,
                "sku": "SKU-01",
                "product_name": "Keyboard",
                "category": "Electronics",
                "subcategory": "Accessories",
                "base_price": 50.0,
                "is_discontinued": 0,
                "updated_at": "2025-01-01T00:00:00Z",
            }
        ]
        pipe.load_dimensions(customers, products)

        orders_file = Path(tmp_dir) / "orders.jsonl"
        items_file = Path(tmp_dir) / "items.jsonl"

        orders_file.write_text(
            json.dumps({
                "order_id": 500,
                "order_number": "ORD-500",
                "customer_id": 1,
                "order_date": "2025-04-01",
                "order_status": "COMPLETED",
                "total_amount": 100.0,
            }) + "\n"
        )
        items_file.write_text(
            json.dumps({
                "item_id": 5001,
                "order_id": 500,
                "product_id": 10,
                "quantity": 2,
                "unit_price": 50.0,
                "line_total": 100.0,
            }) + "\n"
        )

        # Run 1: First Ingestion
        res1 = pipe.execute_batch(orders_file, items_file, run_id="run_batch_initial")
        assert res1.valid_orders == 1
        assert res1.valid_items == 1

        # Verify initial target state
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*), COUNT(DISTINCT order_id) FROM fact_orders;")
            count, distinct_count = cursor.fetchone()
            assert count == 1
            assert distinct_count == 1

        # Run 2: Exact Rerun of the same input file
        res2 = pipe.execute_batch(orders_file, items_file, run_id="run_batch_retry")
        assert res2.valid_orders == 1
        assert res2.valid_items == 1

        # Verify target state remains strictly 1 record, NO duplicates created
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*), COUNT(DISTINCT order_id) FROM fact_orders;")
            count_after, distinct_after = cursor.fetchone()
            assert count_after == 1
            assert distinct_after == 1

            # Verify line items remained strictly 1 record
            cursor.execute("SELECT COUNT(*), COUNT(DISTINCT item_id) FROM fact_order_items;")
            item_count, item_distinct = cursor.fetchone()
            assert item_count == 1
            assert item_distinct == 1


def test_pipeline_late_arriving_backfill():
    """Verify backfilling historical orders seamlessly integrates into fact tables."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "backfill.db"
        pipe = DataEngineeringPipeline(db_path=db_path)

        customers = [
            {
                "customer_id": 1,
                "customer_code": "CUST-01",
                "customer_name": "Backfill Cust",
                "segment": "ENTERPRISE",
                "country": "US",
                "signup_date": "2023-01-01",
                "is_active": 1,
                "updated_at": "2025-01-01T00:00:00Z",
            }
        ]
        pipe.load_dimensions(customers, [])

        # Batch 1: Current orders (2025-06-01)
        b1_orders = Path(tmp_dir) / "b1_orders.jsonl"
        b1_items = Path(tmp_dir) / "b1_items.jsonl"
        b1_orders.write_text(json.dumps({
            "order_id": 1,
            "order_number": "ORD-CURRENT",
            "customer_id": 1,
            "order_date": "2025-06-01",
            "order_status": "COMPLETED",
            "total_amount": 200.0,
        }) + "\n")
        b1_items.write_text("")
        pipe.execute_batch(b1_orders, b1_items, run_id="run_current")

        # Batch 2: Late-arriving historical order from 2024-01-15
        b2_orders = Path(tmp_dir) / "b2_orders.jsonl"
        b2_items = Path(tmp_dir) / "b2_items.jsonl"
        b2_orders.write_text(json.dumps({
            "order_id": 2,
            "order_number": "ORD-HISTORICAL",
            "customer_id": 1,
            "order_date": "2024-01-15",
            "order_status": "COMPLETED",
            "total_amount": 150.0,
        }) + "\n")
        b2_items.write_text("")
        pipe.execute_batch(b2_orders, b2_items, run_id="run_late_arriving")

        # Verify both orders exist with correct timestamps
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT order_id, order_date FROM fact_orders ORDER BY order_date ASC;")
            rows = cursor.fetchall()
            assert len(rows) == 2
            assert rows[0][0] == 2 and rows[0][1] == "2024-01-15"
            assert rows[1][0] == 1 and rows[1][1] == "2025-06-01"
