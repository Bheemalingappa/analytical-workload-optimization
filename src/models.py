"""
Data models, schema definitions, and DDL scripts for baseline and optimized databases.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator


# ==========================================
# Pydantic Schemas for Validation
# ==========================================

class CustomerModel(BaseModel):
    customer_id: int = Field(..., gt=0, description="Unique customer primary key")
    customer_code: str = Field(..., min_length=3, max_length=20)
    customer_name: str = Field(..., min_length=1, max_length=100)
    segment: str = Field(..., min_length=1, max_length=50)
    country: str = Field(..., min_length=2, max_length=50)
    signup_date: str = Field(..., description="ISO signup date YYYY-MM-DD")
    is_active: int = Field(1, ge=0, le=1)
    updated_at: str

    @field_validator("signup_date")
    @classmethod
    def validate_date(cls, v: str) -> str:
        date.fromisoformat(v)
        return v


class ProductModel(BaseModel):
    product_id: int = Field(..., gt=0, description="Unique product primary key")
    sku: str = Field(..., min_length=3, max_length=30)
    product_name: str = Field(..., min_length=1, max_length=150)
    category: str = Field(..., min_length=1, max_length=50)
    subcategory: str = Field(..., min_length=1, max_length=50)
    base_price: float = Field(..., gt=0.0)
    is_discontinued: int = Field(0, ge=0, le=1)
    updated_at: str


class OrderModel(BaseModel):
    model_config = {"extra": "forbid"}

    order_id: int = Field(..., gt=0, description="Unique order primary key")
    order_number: str = Field(..., min_length=5, max_length=30)
    customer_id: int = Field(..., gt=0)
    order_date: str = Field(..., description="ISO order date YYYY-MM-DD")
    order_status: str = Field(..., description="Order lifecycle status")
    total_amount: float = Field(..., ge=0.0)
    discount_amount: float = Field(0.0, ge=0.0)
    run_id: str
    source_file: str
    created_at: str

    @field_validator("order_date")
    @classmethod
    def validate_order_date(cls, v: str) -> str:
        date.fromisoformat(v)
        return v

    @field_validator("order_status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        allowed = {"COMPLETED", "PENDING", "CANCELLED", "RETURNED", "SHIPPED"}
        if v not in allowed:
            raise ValueError(f"Invalid order_status '{v}'. Allowed: {allowed}")
        return v


class OrderItemModel(BaseModel):
    model_config = {"extra": "forbid"}

    item_id: int = Field(..., gt=0)
    order_id: int = Field(..., gt=0)
    product_id: int = Field(..., gt=0)
    quantity: int = Field(..., gt=0)
    unit_price: float = Field(..., gt=0.0)
    line_total: float = Field(..., ge=0.0)
    run_id: str
    source_file: str
    created_at: str

    @field_validator("line_total")
    @classmethod
    def validate_line_total(cls, v: float, info) -> float:
        values = info.data
        if "quantity" in values and "unit_price" in values:
            expected = round(values["quantity"] * values["unit_price"], 2)
            if abs(v - expected) > 0.05:
                raise ValueError(f"line_total {v} does not match quantity * unit_price {expected}")
        return v


# ==========================================
# SQL DDL Definitions
# ==========================================

METADATA_TABLES_DDL = """
-- Pipeline execution logging for observability
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,
    pipeline_name TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT,
    status TEXT NOT NULL, -- RUNNING, SUCCESS, FAILED, PARTIAL_SUCCESS
    source_file TEXT,
    input_count INTEGER DEFAULT 0,
    output_count INTEGER DEFAULT 0,
    rejected_count INTEGER DEFAULT 0,
    error_summary TEXT
);

-- Dead-letter quarantine table for rejected records
CREATE TABLE IF NOT EXISTS quarantine_records (
    quarantine_id TEXT PRIMARY KEY,
    source_entity TEXT NOT NULL,
    source_row_index INTEGER NOT NULL DEFAULT 0,
    source_record_id TEXT,
    raw_record TEXT NOT NULL,
    error_code TEXT NOT NULL,
    error_message TEXT NOT NULL,
    rejected_at TEXT NOT NULL,
    run_id TEXT NOT NULL,
    source_file TEXT NOT NULL
);
"""

BASELINE_SCHEMA_DDL = METADATA_TABLES_DDL + """
-- Customer Dimension (Baseline: No secondary indexes)
CREATE TABLE IF NOT EXISTS dim_customers (
    customer_id INTEGER PRIMARY KEY,
    customer_code TEXT NOT NULL UNIQUE,
    customer_name TEXT NOT NULL,
    segment TEXT NOT NULL,
    country TEXT NOT NULL,
    signup_date TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

-- Product Dimension (Baseline: No secondary indexes)
CREATE TABLE IF NOT EXISTS dim_products (
    product_id INTEGER PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    product_name TEXT NOT NULL,
    category TEXT NOT NULL,
    subcategory TEXT NOT NULL,
    base_price REAL NOT NULL,
    is_discontinued INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

-- Fact Orders (Baseline: No secondary indexes)
CREATE TABLE IF NOT EXISTS fact_orders (
    order_id INTEGER PRIMARY KEY,
    order_number TEXT NOT NULL UNIQUE,
    customer_id INTEGER NOT NULL,
    order_date TEXT NOT NULL,
    order_status TEXT NOT NULL,
    total_amount REAL NOT NULL,
    discount_amount REAL NOT NULL DEFAULT 0.0,
    run_id TEXT NOT NULL,
    source_file TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES dim_customers (customer_id)
);

-- Fact Order Items (Baseline: No secondary indexes)
CREATE TABLE IF NOT EXISTS fact_order_items (
    item_id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price REAL NOT NULL,
    line_total REAL NOT NULL,
    run_id TEXT NOT NULL,
    source_file TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (order_id) REFERENCES fact_orders (order_id),
    FOREIGN KEY (product_id) REFERENCES dim_products (product_id)
);
"""

# Optimized Schema includes:
# 1. Foreign Key indexes to avoid nested table scans on joins
# 2. Composite Covering indexes for common analytical filters and scans
# 3. Categorical indexes for aggregation pushdown
OPTIMIZED_SCHEMA_DDL = BASELINE_SCHEMA_DDL + """
-- Optimization 1: Filter & Scan Optimization
-- Composite covering index to accelerate order filtering by date & status and covering customer/amount
CREATE INDEX IF NOT EXISTS idx_fact_orders_date_status 
ON fact_orders (order_date, order_status, customer_id, total_amount);

-- Optimization 2: Join FK Indexes
CREATE INDEX IF NOT EXISTS idx_fact_orders_customer_id 
ON fact_orders (customer_id);

CREATE INDEX IF NOT EXISTS idx_fact_order_items_order_id 
ON fact_order_items (order_id);

CREATE INDEX IF NOT EXISTS idx_fact_order_items_product_id 
ON fact_order_items (product_id);

-- Optimization 3: Dimension Category & Segment Indexes
CREATE INDEX IF NOT EXISTS idx_dim_products_category 
ON dim_products (category, product_id);

CREATE INDEX IF NOT EXISTS idx_dim_customers_segment 
ON dim_customers (segment, customer_id);

-- Optimization 4: Order Items Covering Index for Aggregations
CREATE INDEX IF NOT EXISTS idx_fact_order_items_covering
ON fact_order_items (product_id, order_id, quantity, line_total);
"""
