"""脏样例的期望值：改动规则或样例时必须同步这里。

所有数字都来自 sample_data/tickets_dirty.csv 的一次真实运行。
"""

DIRTY = {
    "name": "tickets_dirty.csv",
    "raw_records": 55,
    "accepted": 51,
    "quarantine": 3,
    "blank": 1,
    "segments": 2,
    "cells_total": 510,
    "cells_missing": 14,
    "cells_value": 496,
    "cells_ok": 494,
    "cells_format_bad": 2,
    "rows_unique": 50,
    "rows_dup": 1,
    "outlier_cells": 7,
    "tested_cells": 98,
    "score": "65.42",
    "grade": "C",
    "penalty_sum": "34.58",
}

# 加权后扣分：样本比率 → 加权比率（渠道事后分层，总体 = 实际接单结构）
DIRTY_PENALTIES = {
    "missing": ("0.0304", "0.3800", "11.40"),
    "duplicate": ("0.0219", "0.3650", "9.13"),
    "outlier": ("0.0620", "0.6200", "12.40"),
    "format": ("0.0033", "0.0660", "1.65"),
}

# 未加权样本比率：加权口径下仍逐项保留，供对账与追溯
DIRTY_SAMPLE_RATES = {
    "missing": "0.0275",
    "duplicate": "0.0196",
    "outlier": "0.0714",
    "format": "0.0040",
}

# 渠道分层：样本行、样本占比、有效总体占比、权重、加权行
DIRTY_STRATA = {
    "电话": (17, "0.3333", "0.4500", "1.350000", "22.9500"),
    "在线": (16, "0.3137", "0.3500", "1.115625", "17.8500"),
    "邮件": (10, "0.1961", "0.1200", "0.612000", "6.1200"),
    "门店": (8, "0.1569", "0.0800", "0.510000", "4.0800"),
}

QUARANTINE = [(44, "Q_COLUMN_COUNT"), (45, "Q_COLUMN_COUNT"), (57, "Q_TRUNCATED_ROW")]

MISSING_RUNS = {
    "agent": (8, 11, 4),
    "region": (14, 17, 4),
}

MISSING_PATTERNS = {
    ("agent",): 3,
    ("first_response_min",): 3,
    ("region",): 3,
    ("agent", "region"): 2,
    ("satisfaction",): 1,
}

CO_MISSING = {("agent", "region"): 2}

DUPLICATE = {
    "unique_rows": 50,
    "duplicate_rows": 1,
    "hash_representative": 3,
    "hash_duplicate_lineno": 35,
    "key_repeat": "T-2026-000102",
    "conflict_key": "T-2026-000204",
    "conflict_linenos": [36, 37],
    "conflict_fields": ["satisfaction", "agent"],
}

OUTLIERS = {
    "first_response_min": {
        "tested": 47, "outliers": 6, "median": "25", "mad": "12.5",
        "spread": "12.5", "method": "mad",
    },
    "handle_hours": {
        "tested": 51, "outliers": 1, "median": "3", "mad": "1.25",
        "spread": "1.25", "method": "mad",
    },
    "satisfaction": {"tested": 0, "outliers": 0, "method": "degenerate",
                     "floor": "5"},
}

OUTLIER_Z = {
    (3, "1204.5"): "63.6458",
    (55, "1980.0"): "105.4918",
    (55, "9.50"): "3.5074",
}

CLEAN = {"name": "tickets_clean.csv", "accepted": 24, "cells_total": 240,
         "cells_missing": 0, "score": "100.00", "grade": "A"}

CONSTANT = {"name": "tickets_constant.csv", "accepted": 10, "cells_total": 100,
            "cells_missing": 10, "score": "70.00", "grade": "C",
            "missing_penalty": "30.00", "empty_column": "agent",
            "degenerate_column": "handle_hours"}

PROFILE_COLUMNS = {
    "ticket_id": {"missing": 0, "format_bad": 0, "distinct": 49},
    "customer_id": {"missing": 0, "format_bad": 0, "distinct": 49},
    "agent": {"missing": 5, "filled": 46, "fill_rate": "0.9020",
              "missing_rate": "0.0980"},
    "region": {"missing": 5, "filled": 46, "missing_rate": "0.0980"},
    "satisfaction": {"missing": 1, "format_bad": 1, "format_rate": "0.0200",
                     "filled": 50},
    "first_response_min": {"missing": 3, "format_bad": 1, "filled": 48,
                           "missing_rate": "0.0588", "min": "7.0",
                           "max": "1980.0"},
}
