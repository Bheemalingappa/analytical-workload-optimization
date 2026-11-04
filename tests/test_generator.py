"""
Unit tests for Synthetic Data Generator.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from src.generator import SyntheticDataGenerator


def test_generator_deterministic_seed():
    """Verify that using the same seed produces identical outputs."""
    gen1 = SyntheticDataGenerator(seed=123)
    c1, p1 = gen1.generate_dimensions(num_customers=10, num_products=5)
    o1, i1, _ = gen1.generate_workload(num_orders=20, customers=c1, products=p1)

    gen2 = SyntheticDataGenerator(seed=123)
    c2, p2 = gen2.generate_dimensions(num_customers=10, num_products=5)
    o2, i2, _ = gen2.generate_workload(num_orders=20, customers=c2, products=p2)

    assert c1 == c2
    assert p1 == p2
    assert o1 == o2
    assert i1 == i2


def test_generator_anomalies_injected():
    """Verify that anomaly injection produces expected failure categories."""
    gen = SyntheticDataGenerator(seed=42)
    c, p = gen.generate_dimensions(num_customers=50, num_products=20)
    orders, items, ground_truth = gen.generate_workload(
        num_orders=300,
        customers=c,
        products=p,
        inject_anomalies=True,
        anomaly_rate=0.10,
    )

    anomalies = ground_truth["anomalies_injected"]
    assert sum(anomalies.values()) > 0
    assert anomalies["malformed_date"] >= 0
    assert anomalies["missing_field"] >= 0
    assert anomalies["duplicate_key"] >= 0


def test_generator_file_persistence():
    """Verify generated dataset files can be correctly written to disk."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        gen = SyntheticDataGenerator(seed=99)
        c, p = gen.generate_dimensions(num_customers=5, num_products=5)
        o, i, gt = gen.generate_workload(num_orders=10, customers=c, products=p)

        saved = gen.save_dataset(tmp_path, c, p, o, i, gt, prefix="test")
        assert saved["orders_jsonl"].exists()
        assert saved["items_jsonl"].exists()
        assert saved["expected_metrics"].exists()
        assert saved["customers"].exists()
        assert saved["products"].exists()
