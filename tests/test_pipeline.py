"""
Integration tests for Pipeline Ingestion and Run Observability.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from src.config import get_db_connection
from src.pipeline import DataEngineeringPipeline


def test_pipeline_happy_path():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test.db"
        pipe = DataEngineeringPipeline(db_path=db_path, is_optimized=True)

        customers = [
            {
                "customer_id": 1,
                "customer_code": "CUST-0001",
                "customer_name": "Test Cust",
                "segment": "SMB",
                "country": "US",
                "signup_date": "2024-01-01",
                "is_active": 1,
                "updated_at": "2025-01-01T00:00:00Z",
            }
        ]
        products = [
            {
                "product_id": 10,
                "sku": "SKU-TEST-01",
                "product_name": "Test Prod",
                "category": "Electronics",
                "subcategory": "Laptops",
                "base_price": 500.0,
                "is_discontinued": 0,
                "updated_at": "2025-01-01T00:00:00Z",
            }
        ]
        pipe.load_dimensions(customers, products)

        # Write sample orders and items files
        orders_file = Path(tmp_dir) / "orders.jsonl"
        items_file = Path(tmp_dir) / "items.jsonl"

        orders_file.write_text(
            json.dumps({
                "order_id": 1,
                "order_number": "ORD-001",
                "customer_id": 1,
                "order_date": "2025-03-01",
                "order_status": "COMPLETED",
                "total_amount": 500.0,
                "discount_amount": 0.0,
            }) + "\n"
        )
        items_file.write_text(
            json.dumps({
                "item_id": 101,
                "order_id": 1,
                "product_id": 10,
                "quantity": 1,
                "unit_price": 500.0,
                "line_total": 500.0,
            }) + "\n"
        )

        res = pipe.execute_batch(orders_file, items_file, run_id="run_happy_01")
        assert res.status == "SUCCESS"
        assert res.valid_orders == 1
        assert res.valid_items == 1
        assert res.quarantined_count == 0

        # Verify DB content
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM fact_orders;")
            assert cursor.fetchone()[0] == 1
            cursor.execute("SELECT COUNT(*) FROM pipeline_runs WHERE run_id = 'run_happy_01';")
            assert cursor.fetchone()[0] == 1


def test_pipeline_empty_batch():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "empty.db"
        pipe = DataEngineeringPipeline(db_path=db_path)

        empty_orders = Path(tmp_dir) / "empty_orders.jsonl"
        empty_items = Path(tmp_dir) / "empty_items.jsonl"
        empty_orders.write_text("")
        empty_items.write_text("")

        res = pipe.execute_batch(empty_orders, empty_items, run_id="run_empty")
        assert res.status == "SUCCESS"
        assert res.valid_orders == 0
        assert res.input_orders == 0
        assert res.quarantined_count == 0


def test_pipeline_quarantine_isolation():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "quarantine.db"
        pipe = DataEngineeringPipeline(db_path=db_path)
        pipe.load_dimensions(
            customers=[{
                "customer_id": 1,
                "customer_code": "CUST-01",
                "customer_name": "Cust",
                "segment": "SMB",
                "country": "US",
                "signup_date": "2024-01-01",
                "is_active": 1,
                "updated_at": "2025-01-01T00:00:00Z",
            }],
            products=[]
        )

        # 1 valid order, 1 invalid order (orphan customer)
        orders_file = Path(tmp_dir) / "mixed_orders.jsonl"
        orders_file.write_text(
            json.dumps({
                "order_id": 1,
                "order_number": "ORD-001",
                "customer_id": 1,
                "order_date": "2025-03-01",
                "order_status": "COMPLETED",
                "total_amount": 100.0,
            }) + "\n" +
            json.dumps({
                "order_id": 2,
                "order_number": "ORD-002",
                "customer_id": 999999,  # Orphan customer
                "order_date": "2025-03-01",
                "order_status": "COMPLETED",
                "total_amount": 100.0,
            }) + "\n"
        )
        items_file = Path(tmp_dir) / "empty_items.jsonl"
        items_file.write_text("")

        res = pipe.execute_batch(orders_file, items_file, run_id="run_partial")
        assert res.status == "PARTIAL_SUCCESS"
        assert res.valid_orders == 1
        assert res.quarantined_count == 1

        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM fact_orders;")
            assert cursor.fetchone()[0] == 1
            cursor.execute("SELECT COUNT(*) FROM quarantine_records;")
            assert cursor.fetchone()[0] == 1
            cursor.execute("SELECT error_code FROM quarantine_records;")
            assert cursor.fetchone()[0] == "ERR_ORPHAN_CUSTOMER_ID"
