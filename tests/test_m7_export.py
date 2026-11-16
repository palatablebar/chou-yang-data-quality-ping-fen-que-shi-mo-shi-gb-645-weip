"""M7 报告导出：CSV 编码、内容稳定、证据行、清单、空态。"""
from __future__ import annotations

import copy
import json

from app import config
from app.modules.m6_score import runstore
from app.modules.m6_score import service as score_service
from fixtures import expected


def get_csv(client, name: str) -> str:
    response = client.get(f"/api/export/{name}")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    return response.content.decode("utf-8")


def test_report_csv_has_bom_and_core_rows(client_with_run):
    text = get_csv(client_with_run, "report.csv")
    assert text.startswith("\ufeff")
    assert "\r\n" in text
    assert f"总分,{expected.DIRTY['score']},分" in text
    assert f"扣分合计,{expected.DIRTY['penalty_sum']},分" in text
    assert f"等级,{expected.DIRTY['grade']}" in text
    assert "加权口径,渠道事后分层" in text
    assert "加权比率 0.0304" in text and "样本比率 0.0275" in text


def test_csv_output_is_stable_across_requests(client_with_run):
    first = get_csv(client_with_run, "report.csv")
    second = get_csv(client_with_run, "report.csv")
    assert first == second
    profile_first = get_csv(client_with_run, "profile.csv")
    assert profile_first == get_csv(client_with_run, "profile.csv")


def test_quarantine_csv_carries_lineno_and_code(client_with_run):
    text = get_csv(client_with_run, "quarantine.csv")
    lines = [line for line in text.splitlines() if line.strip()]
    assert lines[0].lstrip("\ufeff").startswith("行号,段,原因码")
    assert len(lines) == expected.DIRTY["quarantine"] + 1
    assert "44,2,Q_COLUMN_COUNT" in text
    assert "57,2,Q_TRUNCATED_ROW" in text


def test_profile_csv_lists_contract_columns(client_with_run):
    text = get_csv(client_with_run, "profile.csv")
    lines = [line for line in text.splitlines() if line.strip()]
    assert len(lines) == len(config.COLUMN_NAMES) + 1
    for name in config.COLUMN_NAMES:
        assert name in text


def test_duplicates_csv_lists_conflict_fields(client_with_run):
    text = get_csv(client_with_run, "duplicates.csv")
    assert "业务键冲突,T-2026-000204,2" in text
    assert "satisfaction agent" in text
    assert "全行哈希" in text


def test_outliers_csv_matches_cells(client_with_run):
    text = get_csv(client_with_run, "outliers.csv")
    lines = [line for line in text.splitlines() if line.strip()]
    assert len(lines) == expected.DIRTY["outlier_cells"] + 1
    assert "1204.5" in text
    assert "K_OUTLIER" in text


def test_missing_csv_has_runs_columns(client_with_run):
    text = get_csv(client_with_run, "missing.csv")
    assert "列名,列标题,缺失,有值,缺失率,连续段数,最长段" in text
    assert "agent,受理人,5,46,0.0980,1,4" in text


def test_manifest_json_matches_run(client_with_run):
    latest = client_with_run.get("/api/score/latest").json()
    response = client_with_run.get("/api/export/manifest.json")
    assert response.status_code == 200
    payload = json.loads(response.content)
    assert payload["run_id"] == latest["run"]["run_id"]
    assert payload["input_digest"] == latest["run"]["digest"]
    assert payload["score"] == expected.DIRTY["score"]
    assert len(payload["accounts"]) == 7
    assert payload["all_balanced"] is True
    assert isinstance(payload["weights"], list) and len(payload["weights"]) == 4
    assert payload["weighting"]["scheme"] == "post-stratification"
    assert {item["channel"] for item in payload["population"]} == {
        "电话", "在线", "邮件", "门店"}
    assert len(payload["strata"]) == 4
    assert payload["strata_totals"]["weighted_rows"] == "51.0000"


def test_strata_csv_reconciles_with_counters(client_with_run):
    """分层权重表与报告同一口径：合计行逐项等于计数器，页面导出一致。"""
    text = get_csv(client_with_run, "strata.csv")
    lines = [line for line in text.splitlines() if line.strip()]
    assert lines[0].lstrip("﻿") == ("渠道,样本行,样本占比,有效总体占比,权重,"
                                    "加权行,缺失格,重复行,离群格,参检格,格式不合格格")
    assert len(lines) == len(expected.DIRTY_STRATA) + 2  # 表头 + 4 层 + 合计
    assert "邮件,10,0.1961,0.1200,0.612000,6.1200,3,0,4,20,0" in text
    counters = expected.DIRTY
    total = lines[-1].split(",")
    assert total[0] == "合计"
    assert total[1] == str(counters["accepted"])
    assert total[2] == "1.0000"
    assert total[3] == "1.0000"
    assert total[5] == "51.0000"
    assert total[6] == str(counters["cells_missing"])
    assert total[7] == str(counters["rows_dup"])
    assert total[8] == str(counters["outlier_cells"])
    assert total[9] == str(counters["tested_cells"])
    assert total[10] == str(counters["cells_format_bad"])


def test_export_without_run_returns_404(client):
    response = client.get("/api/export/report.csv")
    assert response.status_code == 404
    assert response.json()["error"] == "E_RUN_NOT_FOUND"


def test_legacy_snapshot_without_weighting_degrades(client, settings, store):
    """旧规则快照（无分层口径）：页面降级渲染，分层导出明确拒绝并提示重跑。"""
    result = score_service.run(settings, store, name="tickets_dirty.csv")
    legacy = copy.deepcopy(result)
    for key in ("weighting", "strata", "population", "strata_totals"):
        legacy["score"].pop(key, None)
    for dim in legacy["score"]["dims"]:
        for key in ("sample_rate", "weighted_numerator", "weighted_denominator"):
            dim.pop(key, None)
    legacy["run"]["run_id"] = "QS-LEGACY-0001"
    legacy["run"]["name"] = "legacy.csv"
    legacy["run"]["version"] = 1
    runstore.persist(store, legacy)

    page = client.get("/?run_id=QS-LEGACY-0001")
    assert page.status_code == 200, page.text
    assert "有效总体占比" not in page.text

    response = client.get("/api/export/strata.csv?run_id=QS-LEGACY-0001")
    assert response.status_code == 400
    assert response.json()["error"] == "E_STRATA_MISSING"

    report = client.get("/api/export/report.csv?run_id=QS-LEGACY-0001")
    assert report.status_code == 200
    assert "加权口径" not in report.content.decode("utf-8")


def test_report_page_renders_score_and_bars(client_with_run):
    response = client_with_run.get("/")
    assert response.status_code == 200
    html = response.text
    assert expected.DIRTY["score"] in html
    assert html.count('class="bar"') == 4
    assert html.count('class="chart__col"') == 7
    assert 'src="http' not in html
    assert "没有运行记录" not in html


def test_empty_report_page_shows_single_empty_state(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.text.count("还没有运行记录。") == 1
