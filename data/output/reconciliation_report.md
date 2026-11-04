# Pipeline Data Reconciliation Report (PASSED)

Auditing source-to-target row balances, referential integrity, and mathematical invariants.

## 1. Exact Source-to-Target Row Balance Audit
| Metric | Source Input | Target Published | Quarantined | Reconciled Status |
|:---|:---:|:---:|:---:|:---:|
| **Orders** | 100,000 | 96,875 | 3,125 | PASSED (Exact) |
| **Order Items** | 249,808 | 241,268 | 8,540 | PASSED (Exact) |

## 2. Integrity & Invariant Audit
| Invariant Assertion | Violation Count | Result |
|:---|:---:|:---:|
| Orphan Customer Foreign Keys | `0` | PASSED |
| Orphan Product Foreign Keys | `0` | PASSED |
| Orphan Order Parent Keys | `0` | PASSED |
| Fact Orders Duplicate Primary Keys | `0` | PASSED |
| Fact Order Items Duplicate Primary Keys | `0` | PASSED |
| Line Total Math Discrepancies | `0` | PASSED |
| Unaccounted Source Orders | `0` | PASSED |
| Double-Counted Source Orders | `0` | PASSED |

**Published Target Gross Revenue:** `$547,304,805.99` (Expected Valid: `$547,304,805.99`, Diff: `$0.00`)

## 3. Quarantined Orders Diagnostic Breakdown
| Error Code | Rejected Record Count |
|:---|:---:|
| `ERR_DUPLICATE_ORDER_KEY` | 642 |
| `ERR_MALFORMED_ORDER_SCHEMA` | 1,876 |
| `ERR_ORPHAN_CUSTOMER_ID` | 607 |

## 4. Quarantined Items Diagnostic Breakdown
| Error Code | Rejected Record Count |
|:---|:---:|
| `ERR_MALFORMED_ITEM_SCHEMA` | 1,257 |
| `ERR_ORPHAN_ORDER_ID` | 6,088 |
| `ERR_ORPHAN_PRODUCT_ID` | 1,195 |

## 5. Notes & Verification Findings:
- PASSED: 100% of 100,000 source orders accounted for (96,875 published, 3,125 quarantined). Zero double-counts.
- PASSED: 100% of 249,808 source order items accounted for (241,268 published, 8,540 quarantined). Zero double-counts.
- PASSED: Revenue reconciled to $547,304,805.99.