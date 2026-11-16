"""评分：五个维度各自封顶，总分 = 100 − 各维扣分。

每维扣分 = 封顶分 × min(1, 实际比率 / 容忍度)，量化到 0.01（ROUND_HALF_UP）。
封顶是硬上限：比率再高也不会把该维扣成负分，所以总分恒在 [0, 100]，
且「总分 + 扣分之和 = 100」逐位成立。

主评分的实际比率是渠道事后分层加权比率：层权重只进层汇总（见 strata.py），
每维仍保留整数分子/分母，异常计数可逐条追回原始行号，记录不打小数。
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from ... import config
from . import grade

ZERO = Decimal("0")


def rate(numerator, denominator) -> Decimal:
    numerator = Decimal(str(numerator))
    denominator = Decimal(str(denominator))
    if denominator <= 0:
        return Decimal("0.0000")
    return (numerator / denominator).quantize(
        config.RATE_QUANT, rounding=ROUND_HALF_UP)


def ratios(counts: dict) -> dict:
    cells_total = counts.get("cells_total", 0)
    cells_missing = counts.get("cells_missing", 0)
    non_missing = cells_total - cells_missing
    return {
        "missing": rate(cells_missing, cells_total),
        "duplicate": rate(counts.get("rows_dup", 0), counts.get("rows_accepted", 0)),
        "outlier": rate(counts.get("outlier_cells", 0), counts.get("tested_cells", 0)),
        "format": rate(counts.get("format_bad", 0), non_missing),
    }


def penalties(counts: dict, weighted: dict | None = None) -> list[dict]:
    sample = ratios(counts)
    numerators = {
        "missing": counts.get("cells_missing", 0),
        "duplicate": counts.get("rows_dup", 0),
        "outlier": counts.get("outlier_cells", 0),
        "format": counts.get("format_bad", 0),
    }
    denominators = {
        "missing": counts.get("cells_total", 0),
        "duplicate": counts.get("rows_accepted", 0),
        "outlier": counts.get("tested_cells", 0),
        "format": counts.get("cells_total", 0) - counts.get("cells_missing", 0),
    }
    rows = []
    for item in config.WEIGHTS:
        key = item["key"]
        cap = Decimal(item["cap"])
        tolerance = Decimal(item["tolerance"])
        if weighted is not None:
            w_num = weighted[key]["numerator"]
            w_den = weighted[key]["denominator"]
        else:
            w_num = Decimal(str(numerators[key]))
            w_den = Decimal(str(denominators[key]))
        value = rate(w_num, w_den)
        ratio = (value / tolerance) if tolerance > 0 else ZERO
        capped = ratio > 1
        ratio = min(ratio, Decimal("1"))
        penalty = (cap * ratio).quantize(config.SCORE_QUANT, rounding=ROUND_HALF_UP)
        severity = grade.severity(penalty, cap)
        rows.append({
            "key": key,
            "label": item["label"],
            "cap": str(cap),
            "tolerance": str(tolerance),
            "numerator": numerators[key],
            "denominator": denominators[key],
            "sample_rate": str(sample[key]),
            "weighted_numerator": _q4(w_num),
            "weighted_denominator": _q4(w_den),
            "rate": str(value),
            "ratio": str(ratio.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
            "penalty": str(penalty),
            "capped": capped,
            "severity": severity["key"],
            "severity_label": severity["label"],
        })
    return rows


def evaluate(counts: dict, strata: dict | None = None) -> dict:
    """评分主入口：给 strata（strata.weigh 的输出）即按渠道加权，否则按样本直算。"""
    weighted = strata["totals"]["weighted"] if strata is not None else None
    dims = penalties(counts, weighted)
    penalty_sum = sum((Decimal(item["penalty"]) for item in dims), ZERO)
    penalty_sum = penalty_sum.quantize(config.SCORE_QUANT, rounding=ROUND_HALF_UP)
    total = (config.HUNDRED - penalty_sum).quantize(config.SCORE_QUANT,
                                                    rounding=ROUND_HALF_UP)
    total = min(max(total, ZERO), config.HUNDRED)
    graded = grade.of(total)
    applied = strata is not None
    return {
        "score": str(total),
        "grade": graded["grade"],
        "grade_label": graded["label"],
        "grade_floor": graded["floor"],
        "penalty_sum": str(penalty_sum),
        "dims": dims,
        "bands": grade.bands(dims),
        "closed": (total + penalty_sum == config.HUNDRED),
        "weights": [{"key": item["key"], "label": item["label"], "cap": item["cap"],
                     "tolerance": item["tolerance"]} for item in config.WEIGHTS],
        "weighting": {
            "applied": applied,
            "variable": "channel",
            "scheme": "post-stratification",
            "unknown_channel": config.UNKNOWN_CHANNEL,
            "note": "层权重 = 有效总体占比 ÷ 样本占比；权重只进层汇总，"
                    "异常计数保持整数、可追原始行号",
        },
        "strata": strata["rows"] if applied else [],
        "population": strata["population"] if applied else [],
        "strata_totals": _totals_json(strata["totals"]) if applied else None,
        "formula": "100 − 缺失30 − 重复25 − 离群20 − 格式25，每维按容忍度封顶；"
                   "比率按渠道分层加权（总体 = 实际接单结构）",
    }


def _q4(value: Decimal) -> str:
    return str(value.quantize(config.RATE_QUANT, rounding=ROUND_HALF_UP))


def _totals_json(totals: dict) -> dict:
    return {
        "rows": totals["rows"],
        "weighted_rows": _q4(totals["weighted_rows"]),
        "pop_sum": _q4(totals["pop_sum"]),
        "unknown_rows": totals["unknown_rows"],
    }
