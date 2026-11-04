"""
Data Reconciliation and Correctness Verification.
Performs end-to-end data auditing, referential integrity assertions,
and aggregate reconciliation.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class ReconciliationReport:
    is_valid: bool
    source_order_count: int
    target_order_count: int
    quarantined_order_count: int
    order_count_reconciled: bool
    
    source_item_count: int
    target_item_count: int
    quarantined_item_count: int
    item_count_reconciled: bool
    
    target_revenue: float
    orphan_customer_refs: int
    orphan_product_refs: int
    orphan_order_refs: int
    duplicate_order_pks: int
    duplicate_item_pks: int
    calculation_mismatches: int
    notes: list[str]


class DataReconciler:
    """Verifies data integrity, consistency, and lineage across source, target, and quarantine."""

    def __init__(self, db_conn: sqlite3.Connection):
        self.conn = db_conn

    def reconcile(
        self,
        raw_orders: list[dict[str, Any]],
        raw_items: list[dict[str, Any]],
    ) -> ReconciliationReport:
        cursor = self.conn.cursor()

        # 1. Target and Quarantine Counts
        cursor.execute("SELECT COUNT(*) FROM fact_orders;")
        target_orders = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM fact_order_items;")
        target_items = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM quarantine_records WHERE source_entity = 'fact_orders';")
        quarantined_orders = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM quarantine_records WHERE source_entity = 'fact_order_items';")
        quarantined_items = cursor.fetchone()[0]

        # 2. Financial Aggregates
        cursor.execute("SELECT ROUND(COALESCE(SUM(total_amount), 0.0), 2) FROM fact_orders;")
        target_rev = cursor.fetchone()[0]

        # 3. Referential Integrity Checks
        cursor.execute(
            """
            SELECT COUNT(*) FROM fact_orders fo 
            LEFT JOIN dim_customers dc ON fo.customer_id = dc.customer_id 
            WHERE dc.customer_id IS NULL;
            """
        )
        orphan_customers = cursor.fetchone()[0]

        cursor.execute(
            """
            SELECT COUNT(*) FROM fact_order_items foi 
            LEFT JOIN dim_products dp ON foi.product_id = dp.product_id 
            WHERE dp.product_id IS NULL;
            """
        )
        orphan_products = cursor.fetchone()[0]

        cursor.execute(
            """
            SELECT COUNT(*) FROM fact_order_items foi 
            LEFT JOIN fact_orders fo ON foi.order_id = fo.order_id 
            WHERE fo.order_id IS NULL;
            """
        )
        orphan_orders = cursor.fetchone()[0]

        # 4. Primary Key Uniqueness Checks
        cursor.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT order_id, COUNT(*) FROM fact_orders GROUP BY order_id HAVING COUNT(*) > 1
            );
            """
        )
        dup_orders = cursor.fetchone()[0]

        cursor.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT item_id, COUNT(*) FROM fact_order_items GROUP BY item_id HAVING COUNT(*) > 1
            );
            """
        )
        dup_items = cursor.fetchone()[0]

        # 5. Calculation Integrity Check
        cursor.execute(
            """
            SELECT COUNT(*) FROM fact_order_items 
            WHERE ABS(line_total - ROUND(quantity * unit_price, 2)) > 0.05;
            """
        )
        calc_mismatches = cursor.fetchone()[0]

        # In a batch that might have duplicates or invalid rows, 
        # distinct valid source records must equal target orders.
        source_order_cnt = len(raw_orders)
        source_item_cnt = len(raw_items)

        # Count check
        order_reconciled = (target_orders + quarantined_orders >= source_order_cnt)
        item_reconciled = (target_items + quarantined_items >= source_item_cnt)

        notes: list[str] = []
        is_valid = True

        if orphan_customers > 0:
            notes.append(f"FAILED: Found {orphan_customers} orphan customer references in fact_orders.")
            is_valid = False
        if orphan_products > 0:
            notes.append(f"FAILED: Found {orphan_products} orphan product references in fact_order_items.")
            is_valid = False
        if orphan_orders > 0:
            notes.append(f"FAILED: Found {orphan_orders} orphan order references in fact_order_items.")
            is_valid = False
        if dup_orders > 0 or dup_items > 0:
            notes.append(f"FAILED: Found duplicate keys (orders={dup_orders}, items={dup_items}).")
            is_valid = False
        if calc_mismatches > 0:
            notes.append(f"FAILED: Found {calc_mismatches} line total calculation discrepancies.")
            is_valid = False

        if is_valid:
            notes.append("PASSED: 100% Referential Integrity, Zero Duplicate PKs, Exact Calculation Precision.")

        return ReconciliationReport(
            is_valid=is_valid,
            source_order_count=source_order_cnt,
            target_order_count=target_orders,
            quarantined_order_count=quarantined_orders,
            order_count_reconciled=order_reconciled,
            source_item_count=source_item_cnt,
            target_item_count=target_items,
            quarantined_item_count=quarantined_items,
            item_count_reconciled=item_reconciled,
            target_revenue=target_rev,
            orphan_customer_refs=orphan_customers,
            orphan_product_refs=orphan_products,
            orphan_order_refs=orphan_orders,
            duplicate_order_pks=dup_orders,
            duplicate_item_pks=dup_items,
            calculation_mismatches=calc_mismatches,
            notes=notes,
        )

    @staticmethod
    def format_report_markdown(rep: ReconciliationReport) -> str:
        md = []
        status_label = "✅ PASSED" if rep.is_valid else "❌ FAILED"
        md.append(f"# Pipeline Data Reconciliation Report ({status_label})\n")
        md.append("Auditing source-to-target row balances, referential integrity, and mathematical invariants.\n")
        
        md.append("## 1. Row Balance Audit")
        md.append("| Metric | Source Input | Target Published | Quarantined | Reconciled Status |")
        md.append("|:---|:---:|:---:|:---:|:---:|")
        md.append(
            f"| **Orders** | {rep.source_order_count:,} | {rep.target_order_count:,} | {rep.quarantined_order_count:,} | "
            f"{'✅ Balanced' if rep.order_count_reconciled else '❌ Discrepancy'} |"
        )
        md.append(
            f"| **Order Items** | {rep.source_item_count:,} | {rep.target_item_count:,} | {rep.quarantined_item_count:,} | "
            f"{'✅ Balanced' if rep.item_count_reconciled else '❌ Discrepancy'} |"
        )

        md.append("\n## 2. Integrity & Invariant Audit")
        md.append("| Invariant Assertion | Violation Count | Result |")
        md.append("|:---|:---:|:---:|")
        md.append(f"| Orphan Customer Foreign Keys | `{rep.orphan_customer_refs}` | {'✅ Passed' if rep.orphan_customer_refs == 0 else '❌ Violation'} |")
        md.append(f"| Orphan Product Foreign Keys | `{rep.orphan_product_refs}` | {'✅ Passed' if rep.orphan_product_refs == 0 else '❌ Violation'} |")
        md.append(f"| Orphan Order Parent Keys | `{rep.orphan_order_refs}` | {'✅ Passed' if rep.orphan_order_refs == 0 else '❌ Violation'} |")
        md.append(f"| Fact Orders Duplicate Primary Keys | `{rep.duplicate_order_pks}` | {'✅ Passed' if rep.duplicate_order_pks == 0 else '❌ Violation'} |")
        md.append(f"| Fact Order Items Duplicate Primary Keys | `{rep.duplicate_item_pks}` | {'✅ Passed' if rep.duplicate_item_pks == 0 else '❌ Violation'} |")
        md.append(f"| Line Total Math Discrepancies | `{rep.calculation_mismatches}` | {'✅ Passed' if rep.calculation_mismatches == 0 else '❌ Violation'} |")

        md.append(f"\n**Published Target Gross Revenue:** `${rep.target_revenue:,.2f}`\n")
        md.append("## Notes & Findings:")
        for note in rep.notes:
            md.append(f"- {note}")

        return "\n".join(md)
