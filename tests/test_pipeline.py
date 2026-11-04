"""
Integration tests for Pipeline Ingestion and Run Observability.
"""
from __future__ import annotations

import json
import sqlite3
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


def test_pipeline_unexpected_schema_change():
    """Verify that records with unexpected/unauthorized schema changes are rejected and quarantined."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "schema_change.db"
        pipe = DataEngineeringPipeline(db_path=db_path)
        pipe.load_dimensions(
            customers=[{
                "customer_id": 1, "customer_code": "C1", "customer_name": "Cust",
                "segment": "SMB", "country": "US", "signup_date": "2024-01-01",
                "is_active": 1, "updated_at": "2025-01-01T00:00:00Z"
            }],
            products=[]
        )

        orders_file = Path(tmp_dir) / "schema_orders.jsonl"
        # Injected unexpected field 'unauthorized_admin_override'
        orders_file.write_text(
            json.dumps({
                "order_id": 1,
                "order_number": "ORD-001",
                "customer_id": 1,
                "order_date": "2025-03-01",
                "order_status": "COMPLETED",
                "total_amount": 100.0,
                "unauthorized_admin_override": True,  # Unexpected schema field
            }) + "\n"
        )
        items_file = Path(tmp_dir) / "empty_items.jsonl"
        items_file.write_text("")

        res = pipe.execute_batch(orders_file, items_file, run_id="run_schema_err")
        assert res.status == "PARTIAL_SUCCESS"
        assert res.valid_orders == 0
        assert res.quarantined_count == 1

        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT error_code FROM quarantine_records;")
            assert cursor.fetchone()[0] == "ERR_UNEXPECTED_SCHEMA_CHANGE"


def test_pipeline_partial_failure_and_retry(monkeypatch):
    """Verify that a mid-transaction pipeline failure cleanly rolls back and allows safe retry."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "retry.db"
        pipe = DataEngineeringPipeline(db_path=db_path)
        pipe.load_dimensions(
            customers=[{
                "customer_id": 1, "customer_code": "C1", "customer_name": "Cust",
                "segment": "SMB", "country": "US", "signup_date": "2024-01-01",
                "is_active": 1, "updated_at": "2025-01-01T00:00:00Z"
            }],
            products=[]
        )

        orders_file = Path(tmp_dir) / "orders.jsonl"
        items_file = Path(tmp_dir) / "items.jsonl"
        orders_file.write_text(json.dumps({
            "order_id": 1, "order_number": "ORD-001", "customer_id": 1,
            "order_date": "2025-03-01", "order_status": "COMPLETED", "total_amount": 100.0
        }) + "\n")
        items_file.write_text("")

        # Simulate failure during persistence
        original_persist = pipe._persist_to_target
        def failing_persist(report, run_id):
            raise sqlite3.OperationalError("Simulated disk I/O failure during commit")

        monkeypatch.setattr(pipe, "_persist_to_target", failing_persist)

        import pytest
        with pytest.raises(sqlite3.OperationalError):
            pipe.execute_batch(orders_file, items_file, run_id="run_fail_01")

        # Verify rollback: 0 published records in target database, status recorded as FAILED
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM fact_orders;")
            assert cursor.fetchone()[0] == 0
            cursor.execute("SELECT status, error_summary FROM pipeline_runs WHERE run_id = 'run_fail_01';")
            status, err_msg = cursor.fetchone()
            assert status == "FAILED"
            assert "Simulated disk I/O failure" in err_msg

        # Now remove failure and retry the same batch with a retry run_id
        monkeypatch.setattr(pipe, "_persist_to_target", original_persist)
        retry_res = pipe.execute_batch(orders_file, items_file, run_id="run_retry_01")
        assert retry_res.status == "SUCCESS"
        assert retry_res.valid_orders == 1

        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM fact_orders;")
            assert cursor.fetchone()[0] == 1


def test_pipeline_incremental_items_for_existing_orders():
    """Verify that late-arriving items referencing orders from a previous batch are accepted."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "incremental.db"
        pipe = DataEngineeringPipeline(db_path=db_path)
        pipe.load_dimensions(
            customers=[{
                "customer_id": 1, "customer_code": "C1", "customer_name": "Cust",
                "segment": "SMB", "country": "US", "signup_date": "2024-01-01",
                "is_active": 1, "updated_at": "2025-01-01T00:00:00Z"
            }],
            products=[{
                "product_id": 10, "sku": "SKU-10", "product_name": "Widget",
                "category": "Electronics", "subcategory": "Audio",
                "base_price": 50.0, "is_discontinued": 0, "updated_at": "2025-01-01T00:00:00Z"
            }]
        )

        # Batch 1: Ingest orders only
        b1_orders = Path(tmp_dir) / "b1_orders.jsonl"
        b1_items = Path(tmp_dir) / "b1_items.jsonl"
        b1_orders.write_text(json.dumps({
            "order_id": 999, "order_number": "ORD-999", "customer_id": 1,
            "order_date": "2025-05-01", "order_status": "COMPLETED", "total_amount": 100.0
        }) + "\n")
        b1_items.write_text("")
        res1 = pipe.execute_batch(b1_orders, b1_items, run_id="run_b1")
        assert res1.valid_orders == 1

        # Batch 2: Ingest items referencing order_id 999 (from previous batch)
        b2_orders = Path(tmp_dir) / "b2_orders.jsonl"
        b2_items = Path(tmp_dir) / "b2_items.jsonl"
        b2_orders.write_text("")
        b2_items.write_text(json.dumps({
            "item_id": 9001, "order_id": 999, "product_id": 10,
            "quantity": 2, "unit_price": 50.0, "line_total": 100.0
        }) + "\n")
        res2 = pipe.execute_batch(b2_orders, b2_items, run_id="run_b2")
        assert res2.valid_items == 1
        assert res2.quarantined_count == 0

        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM fact_order_items WHERE order_id = 999;")
            assert cursor.fetchone()[0] == 1
