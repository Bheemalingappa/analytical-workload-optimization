"""
Data validation, schema enforcement, and quarantine routing.
Catches malformed records, missing fields, business rule violations,
orphan foreign keys, and intra-batch duplicates.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Set
from pydantic import ValidationError

from src.models import OrderModel, OrderItemModel


@dataclass
class QuarantineEntry:
    quarantine_id: str
    source_entity: str
    raw_record: str
    error_code: str
    error_message: str
    rejected_at: str
    run_id: str
    source_file: str


@dataclass
class ValidationReport:
    total_orders_evaluated: int = 0
    valid_orders_count: int = 0
    total_items_evaluated: int = 0
    valid_items_count: int = 0
    quarantined_count: int = 0
    quarantine_entries: list[QuarantineEntry] = field(default_factory=list)
    valid_orders: list[dict[str, Any]] = field(default_factory=list)
    valid_items: list[dict[str, Any]] = field(default_factory=list)


class PipelineValidator:
    """Validates records, applies domain integrity rules, and separates quarantined rows."""

    def __init__(self, valid_customer_ids: Set[int], valid_product_ids: Set[int]):
        self.valid_customer_ids = valid_customer_ids
        self.valid_product_ids = valid_product_ids

    def _quarantine(
        self,
        entity: str,
        raw_row: dict[str, Any],
        error_code: str,
        error_message: str,
        run_id: str,
        source_file: str,
    ) -> QuarantineEntry:
        return QuarantineEntry(
            quarantine_id=str(uuid.uuid4()),
            source_entity=entity,
            raw_record=json.dumps(raw_row, default=str),
            error_code=error_code,
            error_message=error_message,
            rejected_at=datetime.now(timezone.utc).isoformat(),
            run_id=run_id,
            source_file=source_file,
        )

    def validate_batch(
        self,
        raw_orders: list[dict[str, Any]],
        raw_items: list[dict[str, Any]],
        run_id: str,
        source_file: str,
    ) -> ValidationReport:
        report = ValidationReport()
        report.total_orders_evaluated = len(raw_orders)
        report.total_items_evaluated = len(raw_items)

        seen_order_ids: set[int] = set()
        seen_order_numbers: set[str] = set()
        valid_order_id_set: set[int] = set()

        # 1. Validate Orders
        for row in raw_orders:
            # Check duplicate in batch
            order_id = row.get("order_id")
            order_number = row.get("order_number")

            if order_id in seen_order_ids or (order_number and order_number in seen_order_numbers):
                report.quarantine_entries.append(
                    self._quarantine(
                        entity="fact_orders",
                        raw_row=row,
                        error_code="ERR_DUPLICATE_ORDER_KEY",
                        error_message=f"Duplicate order_id {order_id} or order_number {order_number} in batch",
                        run_id=run_id,
                        source_file=source_file,
                    )
                )
                continue

            # Pydantic validation (schema, types, constraints, status enum)
            try:
                row_copy = dict(row)
                row_copy["run_id"] = run_id
                row_copy["source_file"] = source_file
                row_copy.setdefault("created_at", datetime.now(timezone.utc).isoformat())
                validated_order = OrderModel(**row_copy)
            except (ValidationError, Exception) as e:
                report.quarantine_entries.append(
                    self._quarantine(
                        entity="fact_orders",
                        raw_row=row,
                        error_code="ERR_MALFORMED_ORDER_SCHEMA",
                        error_message=str(e),
                        run_id=run_id,
                        source_file=source_file,
                    )
                )
                continue

            # Referential integrity check: Customer must exist
            if validated_order.customer_id not in self.valid_customer_ids:
                report.quarantine_entries.append(
                    self._quarantine(
                        entity="fact_orders",
                        raw_row=row,
                        error_code="ERR_ORPHAN_CUSTOMER_ID",
                        error_message=f"customer_id {validated_order.customer_id} not found in dim_customers",
                        run_id=run_id,
                        source_file=source_file,
                    )
                )
                continue

            # Order is valid
            seen_order_ids.add(validated_order.order_id)
            if validated_order.order_number:
                seen_order_numbers.add(validated_order.order_number)
            valid_order_id_set.add(validated_order.order_id)
            report.valid_orders.append(validated_order.model_dump())

        report.valid_orders_count = len(report.valid_orders)

        # 2. Validate Order Items
        seen_item_ids: set[int] = set()
        for row in raw_items:
            item_id = row.get("item_id")
            if item_id in seen_item_ids:
                report.quarantine_entries.append(
                    self._quarantine(
                        entity="fact_order_items",
                        raw_row=row,
                        error_code="ERR_DUPLICATE_ITEM_KEY",
                        error_message=f"Duplicate item_id {item_id} in batch",
                        run_id=run_id,
                        source_file=source_file,
                    )
                )
                continue

            try:
                row_copy = dict(row)
                row_copy["run_id"] = run_id
                row_copy["source_file"] = source_file
                row_copy.setdefault("created_at", datetime.now(timezone.utc).isoformat())
                validated_item = OrderItemModel(**row_copy)
            except (ValidationError, Exception) as e:
                report.quarantine_entries.append(
                    self._quarantine(
                        entity="fact_order_items",
                        raw_row=row,
                        error_code="ERR_MALFORMED_ITEM_SCHEMA",
                        error_message=str(e),
                        run_id=run_id,
                        source_file=source_file,
                    )
                )
                continue

            # Foreign key: Parent order must be valid within this batch or existing target
            if validated_item.order_id not in valid_order_id_set:
                report.quarantine_entries.append(
                    self._quarantine(
                        entity="fact_order_items",
                        raw_row=row,
                        error_code="ERR_ORPHAN_ORDER_ID",
                        error_message=f"Parent order_id {validated_item.order_id} is missing or rejected",
                        run_id=run_id,
                        source_file=source_file,
                    )
                )
                continue

            # Foreign key: Product must exist in dim_products
            if validated_item.product_id not in self.valid_product_ids:
                report.quarantine_entries.append(
                    self._quarantine(
                        entity="fact_order_items",
                        raw_row=row,
                        error_code="ERR_ORPHAN_PRODUCT_ID",
                        error_message=f"product_id {validated_item.product_id} not found in dim_products",
                        run_id=run_id,
                        source_file=source_file,
                    )
                )
                continue

            seen_item_ids.add(validated_item.item_id)
            report.valid_items.append(validated_item.model_dump())

        report.valid_items_count = len(report.valid_items)
        report.quarantined_count = len(report.quarantine_entries)
        return report
