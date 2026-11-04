"""
Configuration parameters and database connection utilities.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PipelineConfig:
    """Project-wide paths and settings."""
    base_dir: Path = Path(__file__).resolve().parent.parent
    data_dir: Path = base_dir / "data"
    sample_dir: Path = data_dir / "sample"
    output_dir: Path = data_dir / "output"
    log_dir: Path = base_dir / "logs"
    log_file: Path = log_dir / "pipeline.log"
    baseline_db_path: Path = output_dir / "baseline.db"
    optimized_db_path: Path = output_dir / "optimized.db"

    # Default volume parameters
    default_sample_customers: int = 50
    default_sample_products: int = 25
    default_sample_orders: int = 500

    scalable_customers: int = 5000
    scalable_products: int = 1000
    scalable_orders: int = 100000

    random_seed: int = 42

    def ensure_directories(self) -> None:
        """Ensure necessary runtime directories exist."""
        self.sample_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


from contextlib import contextmanager


@contextmanager
def get_db_connection(db_path: Path | str, enforce_fk: bool = True):
    """
    Context manager yielding an optimized SQLite connection with WAL mode and row factory.
    Explicitly closes connection on exit to release locks (critical on Windows).
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.execute("PRAGMA temp_store=MEMORY;")
    cursor.execute("PRAGMA cache_size=-64000;")  # 64MB cache
    if enforce_fk:
        cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()
    try:
        yield conn
    finally:
        conn.close()

