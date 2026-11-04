"""
Unit tests for data validator: schema, nulls, malformed, orphan keys, duplicates, and domain rules.
"""
from __future__ import annotations

import pytest
from src.validator import PipelineValidator


@pytest.fixture
def test_validator():
    return PipelineValidator(valid_customer_ids={1, 2, 3}, valid_product_ids={10, 20, 30})


def test_validator_happy_path(test_validator):
    raw_orders = [
        {
            "order_id": 101,
            "order_number": "ORD-2025-00101",
            "customer_id": 1,
            "order_date": "2025-05-10",
            "order_status": "COMPLETED",
            "total_amount": 100.0,
            "discount_amount": 0.0,
        }
    ]
    raw_items = [
        {
            "item_id": 1001,
            "order_id": 101,
            "product_id": 10,
            "quantity": 2,
            "unit_price": 50.0,
            "line_total": 100.0,
        }
    ]

    report = test_validator.validate_batch(raw_orders, raw_items, "run_01", "test.jsonl")
    assert report.valid_orders_count == 1
    assert report.valid_items_count == 1
    assert report.quarantined_count == 0


def test_validator_malformed_date(test_validator):
    raw_orders = [
        {
            "order_id": 102,
            "order_number": "ORD-2025-00102",
            "customer_id": 1,
            "order_date": "2025-02-31",  # Invalid calendar date
            "order_status": "COMPLETED",
            "total_amount": 50.0,
        }
    ]
    report = test_validator.validate_batch(raw_orders, [], "run_02", "test.jsonl")
    assert report.valid_orders_count == 0
    assert report.quarantined_count == 1
    assert report.quarantine_entries[0].error_code == "ERR_MALFORMED_ORDER_SCHEMA"


def test_validator_missing_required_field(test_validator):
    raw_orders = [
        {
            "order_id": 103,
            "order_number": "ORD-2025-00103",
            "customer_id": None,  # Missing required field
            "order_date": "2025-05-10",
            "order_status": "COMPLETED",
            "total_amount": 50.0,
        }
    ]
    report = test_validator.validate_batch(raw_orders, [], "run_03", "test.jsonl")
    assert report.valid_orders_count == 0
    assert report.quarantined_count == 1
    assert report.quarantine_entries[0].error_code == "ERR_MALFORMED_ORDER_SCHEMA"


def test_validator_orphan_customer(test_validator):
    raw_orders = [
        {
            "order_id": 104,
            "order_number": "ORD-2025-00104",
            "customer_id": 9999,  # Non-existent customer
            "order_date": "2025-05-10",
            "order_status": "COMPLETED",
            "total_amount": 50.0,
        }
    ]
    report = test_validator.validate_batch(raw_orders, [], "run_04", "test.jsonl")
    assert report.valid_orders_count == 0
    assert report.quarantined_count == 1
    assert report.quarantine_entries[0].error_code == "ERR_ORPHAN_CUSTOMER_ID"


def test_validator_duplicate_order_key_in_batch(test_validator):
    raw_orders = [
        {
            "order_id": 105,
            "order_number": "ORD-2025-00105",
            "customer_id": 2,
            "order_date": "2025-05-10",
            "order_status": "COMPLETED",
            "total_amount": 50.0,
        },
        {
            "order_id": 105,  # Duplicate order_id
            "order_number": "ORD-2025-00105-B",
            "customer_id": 2,
            "order_date": "2025-05-11",
            "order_status": "COMPLETED",
            "total_amount": 75.0,
        },
    ]
    report = test_validator.validate_batch(raw_orders, [], "run_05", "test.jsonl")
    assert report.valid_orders_count == 1
    assert report.quarantined_count == 1
    assert report.quarantine_entries[0].error_code == "ERR_DUPLICATE_ORDER_KEY"


def test_validator_mismatched_line_total(test_validator):
    raw_orders = [
        {
            "order_id": 106,
            "order_number": "ORD-2025-00106",
            "customer_id": 2,
            "order_date": "2025-05-10",
            "order_status": "COMPLETED",
            "total_amount": 100.0,
        }
    ]
    raw_items = [
        {
            "item_id": 1006,
            "order_id": 106,
            "product_id": 10,
            "quantity": 2,
            "unit_price": 50.0,
            "line_total": 999.0,  # Calculation mismatch: 2 * 50 != 999
        }
    ]
    report = test_validator.validate_batch(raw_orders, raw_items, "run_06", "test.jsonl")
    assert report.valid_orders_count == 1
    assert report.valid_items_count == 0
    assert report.quarantined_count == 1
    assert report.quarantine_entries[0].error_code == "ERR_MALFORMED_ITEM_SCHEMA"


def test_validator_orphan_product_item(test_validator):
    raw_orders = [
        {
            "order_id": 107,
            "order_number": "ORD-2025-00107",
            "customer_id": 3,
            "order_date": "2025-05-10",
            "order_status": "COMPLETED",
            "total_amount": 50.0,
        }
    ]
    raw_items = [
        {
            "item_id": 1007,
            "order_id": 107,
            "product_id": 99999,  # Product doesn't exist
            "quantity": 1,
            "unit_price": 50.0,
            "line_total": 50.0,
        }
    ]
    report = test_validator.validate_batch(raw_orders, raw_items, "run_07", "test.jsonl")
    assert report.valid_orders_count == 1
    assert report.valid_items_count == 0
    assert report.quarantined_count == 1
    assert report.quarantine_entries[0].error_code == "ERR_ORPHAN_PRODUCT_ID"
