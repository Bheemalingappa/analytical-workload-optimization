"""
Data Reconciliation and Correctness Verification.
Performs end-to-end data auditing, referential integrity assertions,
and strict source-to-target reconciliation with zero double-counting.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


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

    source_gross_revenue: float
    target_revenue: float
    revenue_difference: float

    orphan_customer_refs: int
    orphan_product_refs: int
    orphan_order_refs: int
    duplicate_order_pks: int
    duplicate_item_pks: int
    calculation_mismatches: int

    unaccounted_orders: list[int] = field(default_factory=list)
    unaccounted_items: list[int] = field(default_factory=list)
    double_counted_orders: list[int] = field(default_factory=list)
    double_counted_items: list[int] = field(default_factory=list)

    quarantine_order_breakdown: dict[str, int] = field(default_factory=dict)
    quarantine_item_breakdown: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


class DataReconciler:
    """
    Verifies data integrity, consistency, and lineage across source, target, and quarantine.
    Enforces strict equality (source == published + quarantined) and proves zero double-counting.
    """

    def __init__(self, db_conn: sqlite3.Connection):
        self.conn = db_conn

    def reconcile(
        self,
        raw_orders: list[dict[str, Any]],
        raw_items: list[dict[str, Any]],
        run_id: Optional[str] = None,
        source_file: Optional[str] = None,
    ) -> ReconciliationReport:
        cursor = self.conn.cursor()

        # Build scope filter for runs/source files if specified
        where_order_clause = ""
        where_item_clause = ""
        where_q_order_clause = "WHERE source_entity = 'fact_orders'"
        where_q_item_clause = "WHERE source_entity = 'fact_order_items'"
        params_order: list[Any] = []
        params_item: list[Any] = []
        params_q_order: list[Any] = []
        params_q_item: list[Any] = []

        if run_id:
            where_order_clause = "WHERE run_id = ?"
            where_item_clause = "WHERE run_id = ?"
            where_q_order_clause += " AND run_id = ?"
            where_q_item_clause += " AND run_id = ?"
            params_order.append(run_id)
            params_item.append(run_id)
            params_q_order.append(run_id)
            params_q_item.append(run_id)
        elif source_file:
            where_order_clause = "WHERE source_file = ?"
            where_item_clause = "WHERE source_file = ?"
            where_q_order_clause += " AND source_file = ?"
            where_q_item_clause += " AND source_file = ?"
            params_order.append(source_file)
            params_item.append(source_file)
            params_q_order.append(source_file)
            params_q_item.append(source_file)

        # 1. Target and Quarantine Counts
        cursor.execute(f"SELECT COUNT(*) FROM fact_orders {where_order_clause};", params_order)
        target_orders = cursor.fetchone()[0]

        cursor.execute(f"SELECT COUNT(*) FROM fact_order_items {where_item_clause};", params_item)
        target_items = cursor.fetchone()[0]

        cursor.execute(f"SELECT COUNT(*) FROM quarantine_records {where_q_order_clause};", params_q_order)
        quarantined_orders = cursor.fetchone()[0]

        cursor.execute(f"SELECT COUNT(*) FROM quarantine_records {where_q_item_clause};", params_q_item)
        quarantined_items = cursor.fetchone()[0]

        # 2. Financial Aggregates
        cursor.execute(f"SELECT ROUND(COALESCE(SUM(total_amount), 0.0), 2) FROM fact_orders {where_order_clause};", params_order)
        target_rev = float(cursor.fetchone()[0] or 0.0)

        # Calculate expected valid revenue from raw orders (excluding bad/negative records)
        source_order_cnt = len(raw_orders)
        source_item_cnt = len(raw_items)

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

        # 6. Detailed Quarantine Error Breakdown
        cursor.execute(
            f"SELECT error_code, COUNT(*) FROM quarantine_records {where_q_order_clause} GROUP BY error_code;",
            params_q_order,
        )
        q_order_breakdown = {row[0]: row[1] for row in cursor.fetchall()}

        cursor.execute(
            f"SELECT error_code, COUNT(*) FROM quarantine_records {where_q_item_clause} GROUP BY error_code;",
            params_q_item,
        )
        q_item_breakdown = {row[0]: row[1] for row in cursor.fetchall()}

        # 7. Row-Level Traceability and Double-Count Detection
        cursor.execute(f"SELECT order_id FROM fact_orders {where_order_clause};", params_order)
        published_order_ids: Set[int] = {row[0] for row in cursor.fetchall()}

        cursor.execute(
            f"SELECT source_row_index, source_record_id FROM quarantine_records {where_q_order_clause};",
            params_q_order,
        )
        quarantined_order_rows = cursor.fetchall()
        quarantined_order_indices = {r[0] for r in quarantined_order_rows}

        cursor.execute(f"SELECT item_id FROM fact_order_items {where_item_clause};", params_item)
        published_item_ids: Set[int] = {row[0] for row in cursor.fetchall()}

        cursor.execute(
            f"SELECT source_row_index, source_record_id FROM quarantine_records {where_q_item_clause};",
            params_q_item,
        )
        quarantined_item_rows = cursor.fetchall()
        quarantined_item_indices = {r[0] for r in quarantined_item_rows}

        unaccounted_orders: list[int] = []
        double_counted_orders: list[int] = []

        # Check for multiple quarantine records referencing the same source row index
        q_order_idx_counts = Counter(r[0] for r in quarantined_order_rows)
        for q_idx, count in q_order_idx_counts.items():
            if count > 1:
                double_counted_orders.append(q_idx)

        for idx, o in enumerate(raw_orders):
            oid = o.get("order_id")
            is_quarantined = idx in quarantined_order_indices
            if is_quarantined:
                # Check if this specific order was also published in target without another legitimate source row
                if oid is not None and oid in published_order_ids:
                    other_valid_rows = [
                        i for i, r in enumerate(raw_orders)
                        if i != idx and r.get("order_id") == oid and i not in quarantined_order_indices
                    ]
                    if not other_valid_rows:
                        double_counted_orders.append(idx)
                continue

            # If not quarantined, it must be successfully published in target
            if oid is None or oid not in published_order_ids:
                unaccounted_orders.append(idx)

        unaccounted_items: list[int] = []
        double_counted_items: list[int] = []

        q_item_idx_counts = Counter(r[0] for r in quarantined_item_rows)
        for q_idx, count in q_item_idx_counts.items():
            if count > 1:
                double_counted_items.append(q_idx)

        for idx, it in enumerate(raw_items):
            iid = it.get("item_id")
            is_quarantined = idx in quarantined_item_indices
            if is_quarantined:
                if iid is not None and iid in published_item_ids:
                    other_valid_items = [
                        i for i, r in enumerate(raw_items)
                        if i != idx and r.get("item_id") == iid and i not in quarantined_item_indices
                    ]
                    if not other_valid_items:
                        double_counted_items.append(idx)
                continue

            # If not quarantined, it must be successfully published in target
            if iid is None or iid not in published_item_ids:
                unaccounted_items.append(idx)

        # Strict Exact Equality Check (No >=)
        order_reconciled = (
            (target_orders + quarantined_orders == source_order_cnt)
            and len(unaccounted_orders) == 0
            and len(double_counted_orders) == 0
        )
        item_reconciled = (
            (target_items + quarantined_items == source_item_cnt)
            and len(unaccounted_items) == 0
            and len(double_counted_items) == 0
        )

        # Source valid gross revenue
        valid_orders_revenue = round(
            sum(
                float(o.get("total_amount", 0.0) or 0.0)
                for idx, o in enumerate(raw_orders)
                if idx not in quarantined_order_indices and float(o.get("total_amount", 0.0) or 0.0) >= 0.0
            ),
            2,
        )
        revenue_diff = round(abs(valid_orders_revenue - target_rev), 2)
        revenue_reconciled = (revenue_diff <= 0.05)

        notes: list[str] = []
        is_valid = True

        if not order_reconciled:
            notes.append(
                f"FAILED: Order reconciliation mismatch. Source={source_order_cnt}, "
                f"Published={target_orders}, Quarantined={quarantined_orders} "
                f"(Diff={source_order_cnt - (target_orders + quarantined_orders)}). "
                f"Unaccounted={len(unaccounted_orders)}, Double-counted={len(double_counted_orders)}."
            )
            is_valid = False
        else:
            notes.append(
                f"PASSED: 100% of {source_order_cnt:,} source orders accounted for "
                f"({target_orders:,} published, {quarantined_orders:,} quarantined). Zero double-counts."
            )

        if not item_reconciled:
            notes.append(
                f"FAILED: Item reconciliation mismatch. Source={source_item_cnt}, "
                f"Published={target_items}, Quarantined={quarantined_items} "
                f"(Diff={source_item_cnt - (target_items + quarantined_items)})."
            )
            is_valid = False
        else:
            notes.append(
                f"PASSED: 100% of {source_item_cnt:,} source order items accounted for "
                f"({target_items:,} published, {quarantined_items:,} quarantined). Zero double-counts."
            )

        if not revenue_reconciled:
            notes.append(
                f"FAILED: Revenue mismatch. Expected valid source revenue=${valid_orders_revenue:,.2f}, "
                f"Published target revenue=${target_rev:,.2f} (Diff=${revenue_diff:,.2f})."
            )
            is_valid = False
        else:
            notes.append(f"PASSED: Revenue reconciled to ${target_rev:,.2f}.")

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
            source_gross_revenue=valid_orders_revenue,
            target_revenue=target_rev,
            revenue_difference=revenue_diff,
            orphan_customer_refs=orphan_customers,
            orphan_product_refs=orphan_products,
            orphan_order_refs=orphan_orders,
            duplicate_order_pks=dup_orders,
            duplicate_item_pks=dup_items,
            calculation_mismatches=calc_mismatches,
            unaccounted_orders=unaccounted_orders,
            unaccounted_items=unaccounted_items,
            double_counted_orders=double_counted_orders,
            double_counted_items=double_counted_items,
            quarantine_order_breakdown=q_order_breakdown,
            quarantine_item_breakdown=q_item_breakdown,
            notes=notes,
        )

    @staticmethod
    def format_report_markdown(rep: ReconciliationReport) -> str:
        md = []
        status_label = "PASSED" if rep.is_valid else "FAILED"
        md.append(f"# Pipeline Data Reconciliation Report ({status_label})\n")
        md.append("Auditing source-to-target row balances, referential integrity, and mathematical invariants.\n")

        md.append("## 1. Exact Source-to-Target Row Balance Audit")
        md.append("| Metric | Source Input | Target Published | Quarantined | Reconciled Status |")
        md.append("|:---|:---:|:---:|:---:|:---:|")
        md.append(
            f"| **Orders** | {rep.source_order_count:,} | {rep.target_order_count:,} | {rep.quarantined_order_count:,} | "
            f"{'PASSED (Exact)' if rep.order_count_reconciled else 'FAILED (Discrepancy)'} |"
        )
        md.append(
            f"| **Order Items** | {rep.source_item_count:,} | {rep.target_item_count:,} | {rep.quarantined_item_count:,} | "
            f"{'PASSED (Exact)' if rep.item_count_reconciled else 'FAILED (Discrepancy)'} |"
        )

        md.append("\n## 2. Integrity & Invariant Audit")
        md.append("| Invariant Assertion | Violation Count | Result |")
        md.append("|:---|:---:|:---:|")
        md.append(f"| Orphan Customer Foreign Keys | `{rep.orphan_customer_refs}` | {'PASSED' if rep.orphan_customer_refs == 0 else 'VIOLATION'} |")
        md.append(f"| Orphan Product Foreign Keys | `{rep.orphan_product_refs}` | {'PASSED' if rep.orphan_product_refs == 0 else 'VIOLATION'} |")
        md.append(f"| Orphan Order Parent Keys | `{rep.orphan_order_refs}` | {'PASSED' if rep.orphan_order_refs == 0 else 'VIOLATION'} |")
        md.append(f"| Fact Orders Duplicate Primary Keys | `{rep.duplicate_order_pks}` | {'PASSED' if rep.duplicate_order_pks == 0 else 'VIOLATION'} |")
        md.append(f"| Fact Order Items Duplicate Primary Keys | `{rep.duplicate_item_pks}` | {'PASSED' if rep.duplicate_item_pks == 0 else 'VIOLATION'} |")
        md.append(f"| Line Total Math Discrepancies | `{rep.calculation_mismatches}` | {'PASSED' if rep.calculation_mismatches == 0 else 'VIOLATION'} |")
        md.append(f"| Unaccounted Source Orders | `{len(rep.unaccounted_orders)}` | {'PASSED' if len(rep.unaccounted_orders) == 0 else 'VIOLATION'} |")
        md.append(f"| Double-Counted Source Orders | `{len(rep.double_counted_orders)}` | {'PASSED' if len(rep.double_counted_orders) == 0 else 'VIOLATION'} |")

        md.append(f"\n**Published Target Gross Revenue:** `${rep.target_revenue:,.2f}` (Expected Valid: `${rep.source_gross_revenue:,.2f}`, Diff: `${rep.revenue_difference:,.2f}`)\n")

        if rep.quarantine_order_breakdown:
            md.append("## 3. Quarantined Orders Diagnostic Breakdown")
            md.append("| Error Code | Rejected Record Count |")
            md.append("|:---|:---:|")
            for err_code, cnt in rep.quarantine_order_breakdown.items():
                md.append(f"| `{err_code}` | {cnt:,} |")

        if rep.quarantine_item_breakdown:
            md.append("\n## 4. Quarantined Items Diagnostic Breakdown")
            md.append("| Error Code | Rejected Record Count |")
            md.append("|:---|:---:|")
            for err_code, cnt in rep.quarantine_item_breakdown.items():
                md.append(f"| `{err_code}` | {cnt:,} |")

        md.append("\n## 5. Notes & Verification Findings:")
        for note in rep.notes:
            md.append(f"- {note}")

        return "\n".join(md)
