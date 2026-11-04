"""
Synthetic data generator for analytical workload testing.
Generates reproducible customer, product, order, and order item datasets.
Injects deliberate anomalies and edge cases with fixed random seeds.
"""
from __future__ import annotations

import csv
import json
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Tuple


class SyntheticDataGenerator:
    """Generates synthetic dimensions and facts with reproducible seeds and controlled edge cases."""

    def __init__(self, seed: int = 42):
        self.seed = seed
        self.rng = random.Random(seed)

    def generate_dimensions(
        self, num_customers: int = 100, num_products: int = 50
    ) -> Tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Generates customer and product dimension records."""
        segments = ["ENTERPRISE", "SMB", "CONSUMER", "GOVERNMENT"]
        countries = ["US", "CA", "GB", "DE", "FR", "IN", "JP", "AU"]
        categories = ["Electronics", "Appliances", "Furniture", "Apparel", "Books"]
        subcats = {
            "Electronics": ["Laptops", "Smartphones", "Audio", "Accessories"],
            "Appliances": ["Kitchen", "Cleaning", "Climate"],
            "Furniture": ["Desks", "Chairs", "Storage"],
            "Apparel": ["Men", "Women", "Footwear"],
            "Books": ["Fiction", "Technical", "Business"],
        }

        customers = []
        base_signup = date(2023, 1, 1)
        fixed_updated = "2025-01-01T00:00:00Z"
        for i in range(1, num_customers + 1):
            signup = base_signup + timedelta(days=self.rng.randint(0, 700))
            customers.append({
                "customer_id": i,
                "customer_code": f"CUST-{i:06d}",
                "customer_name": f"Customer Company {i}",
                "segment": self.rng.choice(segments),
                "country": self.rng.choice(countries),
                "signup_date": signup.isoformat(),
                "is_active": 1 if self.rng.random() > 0.05 else 0,
                "updated_at": fixed_updated,
            })

        products = []
        for i in range(1, num_products + 1):
            cat = self.rng.choice(categories)
            sub = self.rng.choice(subcats[cat])
            price = round(self.rng.uniform(10.0, 1500.0), 2)
            products.append({
                "product_id": i,
                "sku": f"SKU-{cat[:3].upper()}-{i:05d}",
                "product_name": f"{cat} Item {i}",
                "category": cat,
                "subcategory": sub,
                "base_price": price,
                "is_discontinued": 1 if self.rng.random() > 0.08 else 0,
                "updated_at": fixed_updated,
            })

        return customers, products

    def generate_workload(
        self,
        num_orders: int,
        customers: list[dict[str, Any]],
        products: list[dict[str, Any]],
        inject_anomalies: bool = True,
        anomaly_rate: float = 0.03,
    ) -> Tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        """
        Generates orders and order items, injecting intentional anomalies and edge cases.
        Returns (orders, items, ground_truth_metrics).
        """
        statuses = ["COMPLETED", "COMPLETED", "COMPLETED", "PENDING", "CANCELLED", "RETURNED"]
        base_order_date = date(2025, 1, 1)

        orders: list[dict[str, Any]] = []
        items: list[dict[str, Any]] = []

        item_counter = 1
        ground_truth = {
            "total_orders_generated": num_orders,
            "expected_valid_orders": 0,
            "expected_quarantined_orders": 0,
            "expected_valid_items": 0,
            "expected_quarantined_items": 0,
            "anomalies_injected": {
                "malformed_date": 0,
                "missing_field": 0,
                "duplicate_key": 0,
                "orphan_customer": 0,
                "orphan_product": 0,
                "negative_amount": 0,
                "mismatched_line_total": 0,
                "late_arriving": 0,
            },
        }

        # Build lookup for product prices
        prod_map = {p["product_id"]: p["base_price"] for p in products}
        prod_ids = list(prod_map.keys())
        cust_ids = [c["customer_id"] for c in customers]

        for i in range(1, num_orders + 1):
            is_anomaly = inject_anomalies and (self.rng.random() < anomaly_rate)
            anomaly_type = None

            if is_anomaly:
                anomaly_type = self.rng.choice([
                    "malformed_date",
                    "missing_field",
                    "duplicate_key",
                    "orphan_customer",
                    "negative_amount",
                ])

            # Normal order attributes
            order_day_offset = self.rng.randint(0, 365)
            order_dt = base_order_date + timedelta(days=order_day_offset)
            order_date_str = order_dt.isoformat()
            customer_id = self.rng.choice(cust_ids)
            status = self.rng.choice(statuses)
            order_num = f"ORD-{order_dt.year}-{i:07d}"

            # Apply anomaly mutations to order
            order_record: dict[str, Any] = {
                "order_id": i,
                "order_number": order_num,
                "customer_id": customer_id,
                "order_date": order_date_str,
                "order_status": status,
                "total_amount": 0.0,
                "discount_amount": round(self.rng.uniform(0, 20), 2),
            }

            if anomaly_type == "malformed_date":
                order_record["order_date"] = "2025-02-31"  # Invalid date
                ground_truth["anomalies_injected"]["malformed_date"] += 1
            elif anomaly_type == "missing_field":
                order_record["customer_id"] = None
                ground_truth["anomalies_injected"]["missing_field"] += 1
            elif anomaly_type == "duplicate_key":
                # Duplicate previous valid order ID if available
                if orders:
                    order_record["order_id"] = orders[-1]["order_id"]
                    order_record["order_number"] = orders[-1]["order_number"]
                    ground_truth["anomalies_injected"]["duplicate_key"] += 1
            elif anomaly_type == "orphan_customer":
                order_record["customer_id"] = 9999999  # Non-existent customer
                ground_truth["anomalies_injected"]["orphan_customer"] += 1
            elif anomaly_type == "negative_amount":
                order_record["total_amount"] = -50.0
                ground_truth["anomalies_injected"]["negative_amount"] += 1

            # Late arriving edge case (valid business scenario, order 180 days in past)
            if not is_anomaly and self.rng.random() < 0.02:
                order_record["order_date"] = (base_order_date - timedelta(days=180)).isoformat()
                ground_truth["anomalies_injected"]["late_arriving"] += 1

            # Generate 1 to 4 items per order
            num_items = self.rng.randint(1, 4)
            order_total = 0.0

            for _ in range(num_items):
                p_id = self.rng.choice(prod_ids)
                unit_price = prod_map[p_id]
                qty = self.rng.randint(1, 5)
                line_total = round(qty * unit_price, 2)

                item_rec: dict[str, Any] = {
                    "item_id": item_counter,
                    "order_id": order_record["order_id"],
                    "product_id": p_id,
                    "quantity": qty,
                    "unit_price": unit_price,
                    "line_total": line_total,
                }

                # In rare cases, inject orphan product or bad calculation
                if inject_anomalies and self.rng.random() < 0.005:
                    item_rec["product_id"] = 8888888  # Non-existent product
                    ground_truth["anomalies_injected"]["orphan_product"] += 1
                elif inject_anomalies and self.rng.random() < 0.005:
                    item_rec["line_total"] = line_total + 100.0  # calculation mismatch
                    ground_truth["anomalies_injected"]["mismatched_line_total"] += 1

                order_total += line_total
                items.append(item_rec)
                item_counter += 1

            if order_record["total_amount"] >= 0:
                order_record["total_amount"] = round(order_total, 2)

            orders.append(order_record)

        return orders, items, ground_truth

    def save_dataset(
        self,
        output_dir: Path,
        customers: list[dict[str, Any]],
        products: list[dict[str, Any]],
        orders: list[dict[str, Any]],
        items: list[dict[str, Any]],
        ground_truth: dict[str, Any],
        prefix: str = "batch",
    ) -> dict[str, Path]:
        """Saves generated dataset to disk in JSON lines and CSV formats."""
        output_dir.mkdir(parents=True, exist_ok=True)

        cust_file = output_dir / f"{prefix}_customers.json"
        prod_file = output_dir / f"{prefix}_products.json"
        orders_file = output_dir / f"{prefix}_orders.jsonl"
        items_file = output_dir / f"{prefix}_items.jsonl"
        metrics_file = output_dir / f"{prefix}_expected_metrics.json"

        # Dimensions (JSON)
        cust_file.write_text(json.dumps(customers, indent=2), encoding="utf-8")
        prod_file.write_text(json.dumps(products, indent=2), encoding="utf-8")

        # Orders (JSON Lines)
        with open(orders_file, "w", encoding="utf-8") as f:
            for row in orders:
                f.write(json.dumps(row) + "\n")

        # Order Items (JSON Lines)
        with open(items_file, "w", encoding="utf-8") as f:
            for row in items:
                f.write(json.dumps(row) + "\n")

        # Ground truth metadata kept separate from raw input files
        metrics_file.write_text(json.dumps(ground_truth, indent=2), encoding="utf-8")

        # Also write CSV representations for broader tool interoperability
        self._write_csv(output_dir / f"{prefix}_orders.csv", orders)
        self._write_csv(output_dir / f"{prefix}_items.csv", items)

        return {
            "customers": cust_file,
            "products": prod_file,
            "orders_jsonl": orders_file,
            "items_jsonl": items_file,
            "expected_metrics": metrics_file,
        }

    def _write_csv(self, filepath: Path, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        fieldnames = list(rows[0].keys())
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
