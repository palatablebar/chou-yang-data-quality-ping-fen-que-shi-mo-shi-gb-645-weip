"""渠道事后分层：把采纳行按渠道分层，算出层权重与各维加权分子分母。

口径（主评分唯一权重来源）：
- 每个采纳行恰好进一层：渠道格为有效枚举值进对应渠道层，缺失或格式不合格
  进「未明」层；
- 层权重 = 有效总体占比 ÷ 样本占比；有效总体 = 实际接单结构（config）：
  未明层按自身样本占比自代表（权重 1，不外推），本期未抽中的渠道占比
  按在样渠道的配置占比折算，保证分布完整；
- 权重只乘在层汇总上：每条异常仍是整数计数、可追原始行号，记录不打小数；
- 恒等式：Σ(层权重 × 样本行) = 采纳行，Σ 有效总体占比 = 1，两式进对账。
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from ... import config

ZERO = Decimal("0")
ONE = Decimal("1")

# 维度 → (分子字段, 分母字段)，字段名与 assign 的层计数对应
_DIM_COUNTS = {
    "missing": ("cells_missing", "cells_total"),
    "duplicate": ("rows_dup", "rows"),
    "outlier": ("outlier_cells", "tested_cells"),
    "format": ("format_bad", "cells_value"),
}

_ZERO_LAYER = {"rows": 0, "cells_total": 0, "cells_missing": 0, "cells_value": 0,
               "format_bad": 0, "rows_dup": 0, "tested_cells": 0,
               "outlier_cells": 0}


def assign(profile: dict, duplicates: dict, outliers: dict) -> list[dict]:
    """按渠道分层，逐层汇总整数计数（行、缺失格、重复行、离群格、参检格、格式格）。

    只读画像行与两套证据的行号，不改写任何上游结果。
    """
    rows = profile["rows"]
    strata: dict[str, dict] = {}

    def bucket(channel: str) -> dict:
        return strata.setdefault(channel, {
            "channel": channel, "rows": 0, "cells_total": 0, "cells_missing": 0,
            "cells_value": 0, "format_bad": 0, "rows_dup": 0, "tested_cells": 0,
            "outlier_cells": 0,
        })

    lineno_channel: dict[int, str] = {}
    tested_columns = {item["name"] for item in outliers["columns"]
                      if item["tested"] > 0}
    width = len(profile["columns"])
    for row in rows:
        cell = row["cells"].get("channel", {})
        channel = cell.get("value")
        if cell.get("state") != "value" or channel not in config.POPULATION_MAP:
            channel = config.UNKNOWN_CHANNEL
        lineno_channel[row["lineno"]] = channel
        entry = bucket(channel)
        entry["rows"] += 1
        entry["cells_total"] += width
        for name, cell in row["cells"].items():
            state = cell["state"]
            if state == "missing":
                entry["cells_missing"] += 1
            elif state == "format_bad":
                entry["format_bad"] += 1
            elif state == "value" and name in tested_columns:
                entry["tested_cells"] += 1
    for entry in strata.values():
        entry["cells_value"] = entry["cells_total"] - entry["cells_missing"]

    for group in duplicates["hash_groups"]:
        for lineno in group["duplicate_linenos"]:
            strata[lineno_channel[lineno]]["rows_dup"] += 1
    for cell in outliers["cells"]:
        strata[lineno_channel[cell["lineno"]]]["outlier_cells"] += 1

    ordered = [strata[name] for name in config.POPULATION_MAP if name in strata]
    if config.UNKNOWN_CHANNEL in strata:
        ordered.append(strata[config.UNKNOWN_CHANNEL])
    return ordered


def weigh(layers: list[dict]) -> dict:
    """层计数 → 层权重与各维加权分子分母。

    权重保留全精度（Decimal 直除），展示刻度 0.000001 单独给；
    Σ(权重×样本行) 与采纳行的差只在 1e-28 量级，对账按 0.0001 闭合。
    """
    by_channel = {layer["channel"]: layer for layer in layers}
    total_rows = sum(layer["rows"] for layer in layers)
    unknown_rows = by_channel.get(config.UNKNOWN_CHANNEL, _ZERO_LAYER)["rows"]
    if total_rows > 0:
        unknown_share = Decimal(unknown_rows) / Decimal(total_rows)
    else:
        unknown_share = ZERO
    # 在样已知渠道的配置占比合计；未抽中渠道的占比按在样渠道折算（rescale）
    represented_pop = sum((config.POPULATION_MAP[name] for name in by_channel
                           if name in config.POPULATION_MAP), ZERO)
    if total_rows > 0 and represented_pop > 0:
        rescale = (ONE - unknown_share) / represented_pop
    else:
        rescale = ZERO

    ordered_channels = [item["channel"] for item in config.CHANNEL_POPULATION]
    if unknown_rows:
        ordered_channels.append(config.UNKNOWN_CHANNEL)

    rows = []
    weighted_rows_sum = ZERO
    pop_sum = ZERO
    weighted = {key: {"numerator": ZERO, "denominator": ZERO}
                for key in _DIM_COUNTS}
    for channel in ordered_channels:
        layer = by_channel.get(channel, {**_ZERO_LAYER, "channel": channel})
        n = layer["rows"]
        if total_rows > 0:
            sample_share = Decimal(n) / Decimal(total_rows)
        else:
            sample_share = ZERO
        if channel == config.UNKNOWN_CHANNEL:
            configured = None
            pop_exact = sample_share
        elif n == 0:
            # 本期未抽中：配置占比已由 rescale 摊给在样渠道，本层有效占比 0
            configured = config.POPULATION_MAP[channel]
            pop_exact = ZERO
        else:
            configured = config.POPULATION_MAP[channel]
            pop_exact = configured * rescale
        pop_sum += pop_exact
        if n == 0:
            weight = ZERO
            weight_text = ""
        else:
            weight = ONE if sample_share == 0 else pop_exact / sample_share
            weight_text = _text(weight, config.WEIGHT_QUANT)
        weighted_rows = weight * n
        weighted_rows_sum += weighted_rows
        for key, (num_field, den_field) in _DIM_COUNTS.items():
            weighted[key]["numerator"] += weight * layer[num_field]
            weighted[key]["denominator"] += weight * layer[den_field]
        rows.append({
            "channel": channel,
            "label": channel if configured is not None else
            f"{channel}（渠道缺失/不合格）",
            "rows": n,
            "sample_share": _text(sample_share, config.RATE_QUANT),
            "pop_share": _text(pop_exact, config.RATE_QUANT),
            "configured_share": (str(configured) if configured is not None else ""),
            "weight": weight_text,
            "weight_exact": str(weight),
            "weighted_rows": _text(weighted_rows, config.RATE_QUANT),
            "cells_total": layer["cells_total"],
            "cells_missing": layer["cells_missing"],
            "cells_value": layer["cells_value"],
            "format_bad": layer["format_bad"],
            "rows_dup": layer["rows_dup"],
            "tested_cells": layer["tested_cells"],
            "outlier_cells": layer["outlier_cells"],
        })

    population = []
    for item in config.CHANNEL_POPULATION:
        channel = item["channel"]
        represented = by_channel.get(channel, _ZERO_LAYER)["rows"] > 0
        population.append({
            "channel": channel, "share": item["share"],
            "effective_share": _text(config.POPULATION_MAP[channel] * rescale
                                     if represented else ZERO, config.RATE_QUANT),
        })
    if unknown_rows:
        population.append({
            "channel": config.UNKNOWN_CHANNEL,
            "share": "",
            "effective_share": _text(unknown_share, config.RATE_QUANT),
        })
    if total_rows == 0:
        # 空批没有可加权的行：结构账按 1 闭合（折算未发生），权重账 0 = 0
        pop_sum = ONE
    totals = {
        "rows": total_rows,
        "weighted_rows": weighted_rows_sum,
        "pop_sum": pop_sum,
        "weighted": weighted,
        "unknown_rows": unknown_rows,
        "rescale": rescale,
    }
    return {"rows": rows, "population": population, "totals": totals}


def _text(value: Decimal, quant: Decimal) -> str:
    return str(value.quantize(quant, rounding=ROUND_HALF_UP))
