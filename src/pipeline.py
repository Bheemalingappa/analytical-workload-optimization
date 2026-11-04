"""
Core Pipeline Engine: Ingestion, Validation, Target Publishing, and Observability.
Guarantees idempotent reruns, quarantine isolation, and run logging.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.config import get_db_connection
from src.logger import get_logger
from src.models import BASELINE_SCHEMA_DDL, OPTIMIZED_SCHEMA_DDL
from src.validator import PipelineValidator, ValidationReport


@dataclass
class PipelineRunResult:
    run_id: str
    status: str
    input_orders: int
    valid_orders: int
    input_items: int
    valid_items: int
    quarantined_count: int
    duration_seconds: float
    error_summary: Optional[str] = None


class DataEngineeringPipeline:
    """Production-grade pipeline executing ingestion, validation, and idempotent loading."""

    def __init__(self, db_path: Path, is_optimized: bool = False, log_file: Optional[Path] = None):
        self.db_path = db_path
        self.is_optimized = is_optimized
        self.logger = get_logger("data_pipeline", log_file=log_file)
        self.init_database()

    def init_database(self) -> None:
        """Initializes tables and indexes based on optimization mode."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        ddl = OPTIMIZED_SCHEMA_DDL if self.is_optimized else BASELINE_SCHEMA_DDL
        with get_db_connection(self.db_path) as conn:
            conn.executescript(ddl)
            conn.commit()
        self.logger.info(
            f"Initialized database schema at {self.db_path} (optimized={self.is_optimized})"
        )

    def load_dimensions(
        self,
        customers: list[dict[str, Any]],
        products: list[dict[str, Any]],
    ) -> tuple[int, int]:
        """Idempotently populates customer and product dimensions."""
        with get_db_connection(self.db_path) as conn:
            cursor = conn.cursor()
            # Upsert customers
            cursor.executemany(
                """
                INSERT OR REPLACE INTO dim_customers (
                    customer_id, customer_code, customer_name, segment, 
                    country, signup_date, is_active, updated_at
                ) VALUES (
                    :customer_id, :customer_code, :customer_name, :segment,
                    :country, :signup_date, :is_active, :updated_at
                );
                """,
                customers,
            )
            # Upsert products
            cursor.executemany(
                """
                INSERT OR REPLACE INTO dim_products (
                    product_id, sku, product_name, category, 
                    subcategory, base_price, is_discontinued, updated_at
                ) VALUES (
                    :product_id, :sku, :product_name, :category,
                    :subcategory, :base_price, :is_discontinued, :updated_at
                );
                """,
                products,
            )
            conn.commit()

        self.logger.info(f"Loaded {len(customers)} customers and {len(products)} products.")
        return len(customers), len(products)

    def get_dimension_keys(self) -> tuple[set[int], set[int]]:
        """Retrieves existing customer and product primary keys for referential integrity."""
        with get_db_connection(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT customer_id FROM dim_customers;")
            cust_keys = {row[0] for row in cursor.fetchall()}

            cursor.execute("SELECT product_id FROM dim_products;")
            prod_keys = {row[0] for row in cursor.fetchall()}

        return cust_keys, prod_keys

    def get_existing_order_ids(self) -> set[int]:
        """Retrieves existing order primary keys to support incremental line items and backfills."""
        with get_db_connection(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT order_id FROM fact_orders;")
            return {row[0] for row in cursor.fetchall()}

    def execute_batch(
        self,
        orders_file: Path,
        items_file: Path,
        run_id: Optional[str] = None,
    ) -> PipelineRunResult:
        """
        Executes a complete batch ingestion run:
        1. Parse input files
        2. Validate & quarantine invalid rows
        3. Idempotently write valid records to target
        4. Log run status and counts
        """
        run_id = run_id or f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        start_time = datetime.now(timezone.utc)
        start_iso = start_time.isoformat()
        source_file_label = f"{orders_file.name}+{items_file.name}"

        self._record_run_start(run_id, start_iso, source_file_label)

        try:
            # 1. Read input records
            raw_orders = self._read_jsonl(orders_file)
            raw_items = self._read_jsonl(items_file)

            # Handle empty batch scenario
            if not raw_orders and not raw_items:
                self.logger.warning(f"Batch {run_id} is completely empty. Marking as SUCCESS.")
                duration = (datetime.now(timezone.utc) - start_time).total_seconds()
                self._record_run_end(run_id, "SUCCESS", 0, 0, 0, duration, None)
                return PipelineRunResult(
                    run_id=run_id,
                    status="SUCCESS",
                    input_orders=0,
                    valid_orders=0,
                    input_items=0,
                    valid_items=0,
                    quarantined_count=0,
                    duration_seconds=duration,
                )

            # 2. Referential integrity keys & existing parent orders
            cust_keys, prod_keys = self.get_dimension_keys()
            existing_orders = self.get_existing_order_ids()
            validator = PipelineValidator(
                valid_customer_ids=cust_keys,
                valid_product_ids=prod_keys,
                existing_order_ids=existing_orders,
            )

            # 3. Validate
            val_report: ValidationReport = validator.validate_batch(
                raw_orders=raw_orders,
                raw_items=raw_items,
                run_id=run_id,
                source_file=source_file_label,
            )

            # 4. Commit Valid & Quarantined records in a single atomic transaction
            self._persist_to_target(val_report, run_id)

            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            status = "PARTIAL_SUCCESS" if val_report.quarantined_count > 0 else "SUCCESS"

            self._record_run_end(
                run_id=run_id,
                status=status,
                input_count=len(raw_orders) + len(raw_items),
                output_count=val_report.valid_orders_count + val_report.valid_items_count,
                rejected_count=val_report.quarantined_count,
                duration=duration,
                error_summary=f"Quarantined {val_report.quarantined_count} records" if val_report.quarantined_count else None,
            )

            return PipelineRunResult(
                run_id=run_id,
                status=status,
                input_orders=len(raw_orders),
                valid_orders=val_report.valid_orders_count,
                input_items=len(raw_items),
                valid_items=val_report.valid_items_count,
                quarantined_count=val_report.quarantined_count,
                duration_seconds=duration,
            )

        except Exception as ex:
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            self.logger.error(f"Pipeline run {run_id} failed: {ex}", exc_info=True)
            self._record_run_end(
                run_id=run_id,
                status="FAILED",
                input_count=0,
                output_count=0,
                rejected_count=0,
                duration=duration,
                error_summary=str(ex),
            )
            raise

    def _persist_to_target(self, report: ValidationReport, run_id: str) -> None:
        """Writes valid orders, items, and quarantine entries into the database."""
        with get_db_connection(self.db_path) as conn:
            cursor = conn.cursor()

            # Insert / Replace Valid Orders
            if report.valid_orders:
                cursor.executemany(
                    """
                    INSERT OR REPLACE INTO fact_orders (
                        order_id, order_number, customer_id, order_date, 
                        order_status, total_amount, discount_amount, 
                        run_id, source_file, created_at
                    ) VALUES (
                        :order_id, :order_number, :customer_id, :order_date,
                        :order_status, :total_amount, :discount_amount,
                        :run_id, :source_file, :created_at
                    );
                    """,
                    report.valid_orders,
                )

            # Insert / Replace Valid Items
            if report.valid_items:
                cursor.executemany(
                    """
                    INSERT OR REPLACE INTO fact_order_items (
                        item_id, order_id, product_id, quantity, 
                        unit_price, line_total, run_id, source_file, created_at
                    ) VALUES (
                        :item_id, :order_id, :product_id, :quantity,
                        :unit_price, :line_total, :run_id, :source_file, :created_at
                    );
                    """,
                    report.valid_items,
                )

            # Insert / Replace Quarantined rows (idempotent per quarantine_id)
            if report.quarantine_entries:
                q_rows = [
                    (
                        q.quarantine_id,
                        q.source_entity,
                        q.source_row_index,
                        q.source_record_id,
                        q.raw_record,
                        q.error_code,
                        q.error_message,
                        q.rejected_at,
                        q.run_id,
                        q.source_file,
                    )
                    for q in report.quarantine_entries
                ]
                cursor.executemany(
                    """
                    INSERT OR REPLACE INTO quarantine_records (
                        quarantine_id, source_entity, source_row_index, source_record_id,
                        raw_record, error_code, error_message, rejected_at, run_id, source_file
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    q_rows,
                )

            conn.commit()

    def _record_run_start(self, run_id: str, start_iso: str, source_file: str) -> None:
        with get_db_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO pipeline_runs (
                    run_id, pipeline_name, start_time, status, source_file
                ) VALUES (?, ?, ?, ?, ?);
                """,
                (run_id, "retail_analytics_ingest", start_iso, "RUNNING", source_file),
            )
            conn.commit()

    def _record_run_end(
        self,
        run_id: str,
        status: str,
        input_count: int,
        output_count: int,
        rejected_count: int,
        duration: float,
        error_summary: Optional[str],
    ) -> None:
        end_iso = datetime.now(timezone.utc).isoformat()
        with get_db_connection(self.db_path) as conn:
            conn.execute(
                """
                UPDATE pipeline_runs SET
                    end_time = ?,
                    status = ?,
                    input_count = ?,
                    output_count = ?,
                    rejected_count = ?,
                    error_summary = ?
                WHERE run_id = ?;
                """,
                (end_iso, status, input_count, output_count, rejected_count, error_summary, run_id),
            )
            conn.commit()

    def _read_jsonl(self, filepath: Path) -> list[dict[str, Any]]:
        if not filepath.exists():
            return []
        rows = []
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    rows.append(json.loads(stripped))
        return rows
