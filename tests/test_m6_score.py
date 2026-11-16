"""M6 评分与批次：封顶扣分、分数闭合、等级、分层加权、幂等、版本闸门、清单。"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app import config, manifest
from app.errors import Conflict
from app.modules.m6_score import grade, score, service, strata
from fixtures import dirty_cases, expected

ACCOUNT_KEYS = ["rows", "accepted", "cells", "value", "weight", "population",
                "score"]


def dims_map(payload: dict) -> dict[str, dict]:
    return {item["key"]: item for item in payload["score"]["dims"]}


def test_penalty_rates_and_penalties(dirty_result):
    """TRAP-10：比率与扣分都走 Decimal，量化到 0.0001 / 0.01。"""
    dims = dims_map(dirty_result)
    for key, (rate, ratio, penalty) in expected.DIRTY_PENALTIES.items():
        assert dims[key]["rate"] == rate, key
        assert dims[key]["ratio"] == ratio, key
        assert dims[key]["penalty"] == penalty, key
        assert Decimal(dims[key]["penalty"]) <= Decimal(dims[key]["cap"])
        assert dims[key]["sample_rate"] == expected.DIRTY_SAMPLE_RATES[key], key


def test_score_closure_and_grade(dirty_result):
    payload = dirty_result["score"]
    assert payload["score"] == expected.DIRTY["score"]
    assert payload["grade"] == expected.DIRTY["grade"]
    assert payload["penalty_sum"] == expected.DIRTY["penalty_sum"]
    assert Decimal(payload["score"]) + Decimal(payload["penalty_sum"]) == Decimal("100")
    assert payload["closed"] is True


def test_capped_dimension_flagged(settings):
    counts = dirty_cases.score_of(
        dirty_cases.pipeline(settings, dirty_cases.constant_case()))
    dims = {item["key"]: item for item in counts["dims"]}
    assert counts["score"] == expected.CONSTANT["score"]
    assert counts["grade"] == expected.CONSTANT["grade"]
    assert dims["missing"]["penalty"] == expected.CONSTANT["missing_penalty"]
    assert dims["missing"]["capped"] is True
    assert dims["missing"]["severity"] == "severe"


def test_grade_thresholds():
    assert grade.of(Decimal("90.00"))["grade"] == "A"
    assert grade.of(Decimal("89.99"))["grade"] == "B"
    assert grade.of(Decimal("75.00"))["grade"] == "B"
    assert grade.of(Decimal("74.99"))["grade"] == "C"
    assert grade.of(Decimal("60.00"))["grade"] == "C"
    assert grade.of(Decimal("59.99"))["grade"] == "D"


def test_severity_bands():
    assert grade.severity(Decimal("30.00"), Decimal("30"))["key"] == "severe"
    assert grade.severity(Decimal("18.00"), Decimal("30"))["key"] == "severe"
    assert grade.severity(Decimal("10.31"), Decimal("30"))["key"] == "warn"
    assert grade.severity(Decimal("2.00"), Decimal("25"))["key"] == "hint"
    assert grade.severity(Decimal("0.00"), Decimal("25"))["key"] == "pass"


def test_penalty_is_monotonic_in_rate():
    base = {"cells_total": 100, "cells_missing": 0, "rows_accepted": 100,
            "rows_dup": 0, "outlier_cells": 0, "tested_cells": 100, "format_bad": 0}
    lighter = score.evaluate({**base, "cells_missing": 2})
    heavier = score.evaluate({**base, "cells_missing": 6})
    assert Decimal(heavier["score"]) < Decimal(lighter["score"])


def test_idempotent_rerun_reuses_run(settings, store):
    first = service.run(settings, store, name="tickets_dirty.csv")
    second = service.run(settings, store, name="tickets_dirty.csv")
    assert second["replay"] is True
    assert second["run"]["run_id"] == first["run"]["run_id"]
    assert second["run"]["score"] == first["run"]["score"]
    assert store.count_runs() == 1


def test_replay_becomes_latest_run(settings, store):
    """重跑后导航回页面仍停在这条记录：最近查看优先于创建时间。"""
    dirty = service.run(settings, store, name="tickets_dirty.csv")
    service.run(settings, store, name="tickets_clean.csv")
    assert store.latest_run()["run_id"] != dirty["run"]["run_id"]
    service.run(settings, store, name="tickets_dirty.csv")
    assert store.latest_run()["run_id"] == dirty["run"]["run_id"]


def test_version_increments_with_new_content(settings, store):
    first = dirty_cases.run_text(settings, store, dirty_cases.frame(dirty_cases.count_rows(6)),
                                 name="case.csv")
    changed = dirty_cases.count_rows(6, agent="")
    second = dirty_cases.run_text(settings, store, dirty_cases.frame(changed),
                                  name="case.csv", expected_version=1)
    assert first["run"]["version"] == 1
    assert second["run"]["version"] == 2
    assert second["run"]["digest"] != first["run"]["digest"]
    assert store.latest_version("case.csv") == 2


def test_stale_submission_rejected(settings, store):
    """TRAP-26：旧版本页面提交必须 409，不许覆盖新结果。"""
    first_text = dirty_cases.frame(dirty_cases.count_rows(6))
    dirty_cases.run_text(settings, store, first_text, name="case.csv")
    dirty_cases.run_text(settings, store, dirty_cases.frame(dirty_cases.count_rows(6, agent="")),
                         name="case.csv", expected_version=1)
    with pytest.raises(Conflict) as info:
        dirty_cases.run_text(settings, store, first_text, name="case.csv", expected_version=1)
    assert info.value.code == "E_STALE_RUN"
    assert info.value.status_code == 409
    assert info.value.detail["latest_version"] == 2


def test_manifest_carries_digest_and_thresholds(dirty_result):
    manifest = dirty_result["manifest"]
    assert len(manifest["input_digest"]) == 64
    assert len(manifest["rules_digest"]) == 64
    assert manifest["rule_version"] == config.RULE_VERSION
    assert manifest["thresholds"] == {
        item["key"]: {"cap": item["cap"], "tolerance": item["tolerance"]}
        for item in config.WEIGHTS}
    assert manifest["all_balanced"] is True
    assert [stage["stage"] for stage in manifest["stages"]] == list(config.STAGES)


def test_seven_accounts_balanced(dirty_result):
    reconcile = dirty_result["reconcile"]
    assert [item["key"] for item in reconcile["accounts"]] == ACCOUNT_KEYS
    for item in reconcile["accounts"]:
        assert Decimal(str(item["diff"])) == 0, item["key"]
        assert item["balanced"] is True
    assert reconcile["diff_count"] == 0
    assert reconcile["all_balanced"] is True


def test_batches_api_lists_runs(client):
    client.post("/api/score", json={"sample": "tickets_dirty.csv"})
    client.post("/api/score", json={"sample": "tickets_clean.csv"})
    body = client.get("/api/batches").json()
    assert len(body["runs"]) == 2
    first = body["runs"][0]
    for key in ("run_id", "name", "digest", "version", "score", "grade",
                "rows_accepted", "rows_unique", "rows_dup", "cells_total",
                "cells_missing", "created_at"):
        assert key in first
    assert {item["name"] for item in body["runs"]} == {"tickets_dirty.csv",
                                                       "tickets_clean.csv"}


def test_reconcile_api_returns_accounts(client_with_run):
    body = client_with_run.get("/api/reconcile").json()
    assert [item["key"] for item in body["accounts"]] == ACCOUNT_KEYS
    assert body["all_balanced"] is True


def strata_map(payload: dict) -> dict[str, dict]:
    return {item["channel"]: item for item in payload["score"]["strata"]}


def test_strata_weights_follow_population(dirty_result):
    """层权重 = 有效总体占比 ÷ 样本占比：超抽渠道 < 1，欠抽渠道 > 1。"""
    layers = strata_map(dirty_result)
    assert set(layers) == set(expected.DIRTY_STRATA)
    for channel, (rows, sample, pop, weight, weighted_rows) in \
            expected.DIRTY_STRATA.items():
        item = layers[channel]
        assert item["rows"] == rows, channel
        assert item["sample_share"] == sample, channel
        assert item["pop_share"] == pop, channel
        assert item["weight"] == weight, channel
        assert item["weighted_rows"] == weighted_rows, channel
    assert Decimal(layers["邮件"]["weight"]) < 1 < Decimal(layers["电话"]["weight"])


def test_weighted_score_corrects_oversampled_channels(dirty_result):
    """邮件超抽且离群集中：加权离群率必须低于样本离群率，主评分随之变化。"""
    dims = dims_map(dirty_result)
    assert Decimal(dims["outlier"]["rate"]) < Decimal(dims["outlier"]["sample_rate"])
    assert Decimal(dims["missing"]["rate"]) > Decimal(dims["missing"]["sample_rate"])
    unweighted = score.evaluate({
        "cells_total": expected.DIRTY["cells_total"],
        "cells_missing": expected.DIRTY["cells_missing"],
        "rows_accepted": expected.DIRTY["accepted"],
        "rows_dup": expected.DIRTY["rows_dup"],
        "outlier_cells": expected.DIRTY["outlier_cells"],
        "tested_cells": expected.DIRTY["tested_cells"],
        "format_bad": expected.DIRTY["cells_format_bad"],
    })
    assert unweighted["score"] == "65.24"
    assert dirty_result["score"]["score"] == expected.DIRTY["score"]
    assert Decimal(dirty_result["score"]["score"]) != Decimal(unweighted["score"])


def test_strata_partition_conserves_counts(dirty_result):
    """分层只改汇总权重：各层整数计数合计必须等于总数，一条异常都不丢不拆。"""
    layers = dirty_result["score"]["strata"]
    counters = dirty_result["manifest"]["counters"]
    for field, key in (("rows", "accepted"), ("cells_missing", "cells_missing"),
                       ("rows_dup", "rows_dup"), ("outlier_cells", "outlier_cells"),
                       ("tested_cells", "tested_cells"),
                       ("format_bad", "cells_format_bad")):
        assert sum(item[field] for item in layers) == counters[key], key
        for item in layers:
            assert isinstance(item[field], int), (key, item["channel"])


def test_weight_account_conserves_rows(dirty_result):
    """权重账：Σ(层权重 × 样本行) = 采纳行（全精度权重，按 0.0001 闭合）。"""
    layers = dirty_result["score"]["strata"]
    accepted = dirty_result["manifest"]["counters"]["accepted"]
    weighted_sum = sum(Decimal(item["weight_exact"]) * item["rows"]
                       for item in layers)
    assert weighted_sum.quantize(Decimal("0.0001")) == Decimal(accepted)


def test_unknown_channel_stratum_weight_one(settings):
    """渠道缺失/不合格的行进「未明」层：权重 1 不外推，权重账仍闭合。"""
    rows = dirty_cases.count_rows(6)
    rows.append(dirty_cases.make_row(ticket="T-2026-950001", customer="00951",
                                     channel=""))
    rows.append(dirty_cases.make_row(ticket="T-2026-950002", customer="00952",
                                     channel="电话X"))
    payload = dirty_cases.pipeline(settings, dirty_cases.frame(rows))
    evaluated = dirty_cases.score_of(payload)
    layers = {item["channel"]: item for item in evaluated["strata"]}
    unknown = layers[config.UNKNOWN_CHANNEL]
    assert unknown["rows"] == 2
    assert unknown["weight"] == "1.000000"
    assert unknown["pop_share"] == unknown["sample_share"]
    totals = evaluated["strata_totals"]
    assert totals["unknown_rows"] == 2
    assert Decimal(totals["weighted_rows"]) == Decimal(totals["rows"])
    assert Decimal(totals["pop_sum"]) == Decimal("1.0000")


def test_population_change_alters_rules_digest(monkeypatch):
    """总体结构参与规则摘要：改接单结构 = 换规则，同批重跑不会命中旧记录。"""
    base = manifest.rules_digest(config.RULE_VERSION)
    shifted = tuple({"channel": item["channel"], "share": "0.25"}
                    for item in config.CHANNEL_POPULATION)
    monkeypatch.setattr(config, "CHANNEL_POPULATION", shifted)
    assert manifest.rules_digest(config.RULE_VERSION) != base


def test_population_config_is_complete():
    """结构账的前提：配置的实际接单结构必须合计为 1 且覆盖渠道枚举。"""
    assert sum(config.POPULATION_MAP.values(), Decimal("0")) == Decimal("1")
    enum_channels = set(config.COLUMN_MAP["channel"].enums)
    assert set(config.POPULATION_MAP) == enum_channels


def test_rerun_same_batch_no_version_no_double_count(settings, store):
    """同批重跑：版本不增、运行记录不增、计数与分数不重复累计。"""
    first = service.run(settings, store, name="tickets_dirty.csv")
    second = service.run(settings, store, name="tickets_dirty.csv")
    assert second["replay"] is True
    assert second["run"]["run_id"] == first["run"]["run_id"]
    assert first["run"]["version"] == second["run"]["version"] == 1
    assert store.latest_version("tickets_dirty.csv") == 1
    assert store.count_runs() == 1
    assert second["manifest"]["counters"] == first["manifest"]["counters"]
    assert second["score"]["strata"] == first["score"]["strata"]
    assert second["score"]["score"] == first["score"]["score"]


def test_score_api_returns_closed_payload(client):
    response = client.post("/api/score", json={"sample": "tickets_dirty.csv"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["score"] == expected.DIRTY["score"]
    assert body["grade"] == expected.DIRTY["grade"]
    assert body["closed"] is True
    assert len(body["dims"]) == 4
    assert body["replay"] is False
