"""
Tests for Data Quality Reconciliation: Strict Equality, Zero Double-Counting, and Failure Reporting.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from src.config import get_db_connection
from src.pipeline import DataEngineeringPipeline
from src.reconciliation import DataReconciler


def test_reconciliation_exact_accounting():
    """Verify that a batch with valid and invalid records reconciles 100% exactly."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "rec_test.db"
        pipe = DataEngineeringPipeline(db_path=db_path)

        customers = [
            {
                "customer_id": 1,
                "customer_code": "CUST-01",
                "customer_name": "Test Company",
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
                "sku": "SKU-01",
                "product_name": "Widget",
                "category": "Electronics",
                "subcategory": "Gadgets",
                "base_price": 50.0,
                "is_discontinued": 0,
                "updated_at": "2025-01-01T00:00:00Z",
            }
        ]
        pipe.load_dimensions(customers, products)

        # 3 orders: 2 valid, 1 invalid (orphan customer)
        raw_orders = [
            {
                "order_id": 1,
                "order_number": "ORD-001",
                "customer_id": 1,
                "order_date": "2025-02-01",
                "order_status": "COMPLETED",
                "total_amount": 100.0,
            },
            {
                "order_id": 2,
                "order_number": "ORD-002",
                "customer_id": 1,
                "order_date": "2025-02-02",
                "order_status": "COMPLETED",
                "total_amount": 50.0,
            },
            {
                "order_id": 3,
                "order_number": "ORD-003",
                "customer_id": 9999,  # Orphan customer
                "order_date": "2025-02-03",
                "order_status": "COMPLETED",
                "total_amount": 75.0,
            },
        ]

        raw_items = [
            {
                "item_id": 101,
                "order_id": 1,
                "product_id": 10,
                "quantity": 2,
                "unit_price": 50.0,
                "line_total": 100.0,
            },
            {
                "item_id": 102,
                "order_id": 2,
                "product_id": 10,
                "quantity": 1,
                "unit_price": 50.0,
                "line_total": 50.0,
            },
            {
                "item_id": 103,
                "order_id": 3,  # Parent order is rejected
                "product_id": 10,
                "quantity": 1,
                "unit_price": 50.0,
                "line_total": 50.0,
            },
        ]

        orders_file = Path(tmp_dir) / "orders.jsonl"
        items_file = Path(tmp_dir) / "items.jsonl"
        with open(orders_file, "w", encoding="utf-8") as f:
            for r in raw_orders:
                f.write(json.dumps(r) + "\n")
        with open(items_file, "w", encoding="utf-8") as f:
            for r in raw_items:
                f.write(json.dumps(r) + "\n")

        res = pipe.execute_batch(orders_file, items_file, run_id="run_rec_01")
        assert res.status == "PARTIAL_SUCCESS"
        assert res.valid_orders == 2
        assert res.valid_items == 2
        assert res.quarantined_count == 2  # 1 order + 1 item

        with get_db_connection(db_path) as conn:
            reconciler = DataReconciler(conn)
            report = reconciler.reconcile(raw_orders, raw_items, run_id="run_rec_01")

        assert report.is_valid is True
        assert report.order_count_reconciled is True
        assert report.item_count_reconciled is True
        assert report.target_order_count == 2
        assert report.quarantined_order_count == 1
        assert report.source_order_count == 3
        assert report.target_item_count == 2
        assert report.quarantined_item_count == 1
        assert report.source_item_count == 3
        assert len(report.unaccounted_orders) == 0
        assert len(report.double_counted_orders) == 0


def test_reconciliation_fails_on_missing_records():
    """Verify reconciliation visibly fails when target rows are missing (data loss)."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "rec_missing.db"
        pipe = DataEngineeringPipeline(db_path=db_path)
        pipe.load_dimensions(
            customers=[{
                "customer_id": 1, "customer_code": "C1", "customer_name": "Cust",
                "segment": "SMB", "country": "US", "signup_date": "2024-01-01",
                "is_active": 1, "updated_at": "2025-01-01T00:00:00Z"
            }],
            products=[]
        )

        raw_orders = [
            {"order_id": 1, "order_number": "ORD-01", "customer_id": 1, "order_date": "2025-01-01", "order_status": "COMPLETED", "total_amount": 10.0},
            {"order_id": 2, "order_number": "ORD-02", "customer_id": 1, "order_date": "2025-01-02", "order_status": "COMPLETED", "total_amount": 20.0},
        ]
        orders_file = Path(tmp_dir) / "orders.jsonl"
        items_file = Path(tmp_dir) / "items.jsonl"
        orders_file.write_text("\n".join(json.dumps(r) for r in raw_orders) + "\n")
        items_file.write_text("")

        pipe.execute_batch(orders_file, items_file, run_id="run_loss")

        # Simulate data corruption/loss: delete 1 order from target database
        with get_db_connection(db_path) as conn:
            conn.execute("DELETE FROM fact_orders WHERE order_id = 2;")
            conn.commit()

        # Reconcile must visibly fail
        with get_db_connection(db_path) as conn:
            reconciler = DataReconciler(conn)
            report = reconciler.reconcile(raw_orders, [], run_id="run_loss")

        assert report.is_valid is False
        assert report.order_count_reconciled is False
        assert len(report.unaccounted_orders) == 1
        assert 1 in report.unaccounted_orders  # row index 1 (order_id 2) is missing
        assert any("Order reconciliation mismatch" in note for note in report.notes)


def test_reconciliation_fails_on_double_counting():
    """Verify reconciliation visibly fails when an order is both published and quarantined."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "rec_double.db"
        pipe = DataEngineeringPipeline(db_path=db_path)
        pipe.load_dimensions(
            customers=[{
                "customer_id": 1, "customer_code": "C1", "customer_name": "Cust",
                "segment": "SMB", "country": "US", "signup_date": "2024-01-01",
                "is_active": 1, "updated_at": "2025-01-01T00:00:00Z"
            }],
            products=[]
        )

        raw_orders = [
            {"order_id": 1, "order_number": "ORD-01", "customer_id": 1, "order_date": "2025-01-01", "order_status": "COMPLETED", "total_amount": 10.0},
        ]
        orders_file = Path(tmp_dir) / "orders.jsonl"
        items_file = Path(tmp_dir) / "items.jsonl"
        orders_file.write_text(json.dumps(raw_orders[0]) + "\n")
        items_file.write_text("")

        pipe.execute_batch(orders_file, items_file, run_id="run_double")

        # Artificially inject a duplicate quarantine entry for the same order row index
        with get_db_connection(db_path) as conn:
            conn.execute(
                """
                INSERT INTO quarantine_records (
                    quarantine_id, source_entity, source_row_index, source_record_id,
                    raw_record, error_code, error_message, rejected_at, run_id, source_file
                ) VALUES ('fake_q_id', 'fact_orders', 0, '1', '{}', 'ERR_FAKE', 'Fake error', '2025-01-01', 'run_double', 'test');
                """
            )
            conn.commit()

        # Reconcile must detect double count and fail
        with get_db_connection(db_path) as conn:
            reconciler = DataReconciler(conn)
            report = reconciler.reconcile(raw_orders, [], run_id="run_double")

        assert report.is_valid is False
        assert report.order_count_reconciled is False
        assert len(report.double_counted_orders) == 1
        assert 0 in report.double_counted_orders


def test_reconciliation_rerun_idempotency():
    """Verify that rerunning the same batch preserves exact reconciliation and does not duplicate quarantine."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "rec_rerun.db"
        pipe = DataEngineeringPipeline(db_path=db_path)
        pipe.load_dimensions(
            customers=[{
                "customer_id": 1, "customer_code": "C1", "customer_name": "Cust",
                "segment": "SMB", "country": "US", "signup_date": "2024-01-01",
                "is_active": 1, "updated_at": "2025-01-01T00:00:00Z"
            }],
            products=[]
        )

        # 1 valid, 1 invalid order
        raw_orders = [
            {"order_id": 1, "order_number": "ORD-01", "customer_id": 1, "order_date": "2025-01-01", "order_status": "COMPLETED", "total_amount": 50.0},
            {"order_id": 2, "order_number": "ORD-02", "customer_id": 9999, "order_date": "2025-01-01", "order_status": "COMPLETED", "total_amount": 25.0},
        ]
        orders_file = Path(tmp_dir) / "orders.jsonl"
        items_file = Path(tmp_dir) / "items.jsonl"
        orders_file.write_text("\n".join(json.dumps(r) for r in raw_orders) + "\n")
        items_file.write_text("")

        # First run
        pipe.execute_batch(orders_file, items_file, run_id="run_1")
        # Second run (exact rerun of same file)
        pipe.execute_batch(orders_file, items_file, run_id="run_2")

        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM fact_orders;")
            assert cursor.fetchone()[0] == 1
            cursor.execute("SELECT COUNT(*) FROM quarantine_records WHERE source_entity = 'fact_orders';")
            assert cursor.fetchone()[0] == 1  # Exactly 1 quarantine record, not 2!

            reconciler = DataReconciler(conn)
            report = reconciler.reconcile(raw_orders, [], run_id="run_2")

        assert report.is_valid is True
        assert report.order_count_reconciled is True
        assert report.target_order_count == 1
        assert report.quarantined_order_count == 1
        assert report.source_order_count == 2
