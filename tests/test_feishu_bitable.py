import json

import httpx
import pytest

from agent.schemas import OpportunityAnalysis, OpportunityFacts
from integrations.feishu_bitable import (
    FeishuAPIError,
    FeishuBitableClient,
    REQUIRED_OPPORTUNITY_FIELDS,
)


@pytest.fixture(autouse=True)
def clear_token_cache():
    FeishuBitableClient._TOKEN_CACHE.clear()
    yield
    FeishuBitableClient._TOKEN_CACHE.clear()


def make_client(handler, **overrides) -> FeishuBitableClient:
    values = {
        "enabled": True,
        "app_id": "cli_test",
        "app_secret": "secret_test",
        "app_token": "base_test",
        "table_id": "tbl_test",
        "max_retries": 0,
        "http_client": httpx.Client(transport=httpx.MockTransport(handler)),
    }
    values.update(overrides)
    client = FeishuBitableClient(**values)
    client._opportunity_field_map_cache = {
        field_name: field_name for field_name in REQUIRED_OPPORTUNITY_FIELDS
    }
    return client


def response(request: httpx.Request, data: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=data, request=request)


def analysis() -> OpportunityAnalysis:
    return OpportunityAnalysis(
        request_id="req-001",
        original_note="这是一段足够长的客户拜访记录，用于测试飞书写入。",
        facts=OpportunityFacts(),
        stage="S0",
        stage_name="线索",
        stage_evidence=[],
        risks=[],
        next_actions=[],
        unconfirmed_information=[],
        confidence=0.45,
    )


def test_token_is_cached_and_fields_are_paginated():
    calls = {"token": 0, "fields": 0}

    def handler(request: httpx.Request):
        if request.url.path.endswith("tenant_access_token/internal"):
            calls["token"] += 1
            return response(request, {"code": 0, "tenant_access_token": "token", "expire": 7200})
        assert request.headers["Authorization"] == "Bearer token"
        calls["fields"] += 1
        if "page_token" not in request.url.params:
            return response(
                request,
                {"code": 0, "data": {"items": [{"field_name": "请求编号"}], "has_more": True, "page_token": "p2"}},
            )
        return response(
            request,
            {"code": 0, "data": {"items": [{"field_name": "客户名称"}], "has_more": False}},
        )

    client = make_client(handler)
    assert [item["field_name"] for item in client.list_fields()] == ["请求编号", "客户名称"]
    assert calls == {"token": 1, "fields": 2}


def test_generic_record_helpers_support_eval_table():
    captured = []

    def handler(request: httpx.Request):
        if request.url.path.endswith("tenant_access_token/internal"):
            return response(request, {"code": 0, "tenant_access_token": "token", "expire": 7200})
        captured.append((request.method, request.url.path, json.loads(request.content)))
        if request.url.path.endswith("/fields"):
            return response(request, {"code": 0, "data": {"field": {"field_id": "fld1"}}})
        return response(request, {"code": 0, "data": {"record": {"record_id": "rec1"}}})

    client = make_client(handler)
    assert client.create_field(table_id="tbl_eval", field_name="模型版本", field_type=1) == "fld1"
    assert client.create_record(table_id="tbl_eval", fields={"用例编号": "E1"}) == "rec1"
    assert client.update_record(table_id="tbl_eval", record_id="rec1", fields={"是否通过": True}) == "rec1"
    assert all("tbl_eval" in path for _, path, _ in captured)


def test_schema_report_lists_missing_fields():
    def handler(request: httpx.Request):
        if request.url.path.endswith("tenant_access_token/internal"):
            return response(request, {"code": 0, "tenant_access_token": "token", "expire": 7200})
        return response(
            request,
            {"code": 0, "data": {"items": [{"field_name": "请求编号"}], "has_more": False}},
        )

    report = make_client(handler).validate_schema()
    assert report.ok is False
    assert "请求编号" in report.available_fields
    assert report.missing_fields == sorted(REQUIRED_OPPORTUNITY_FIELDS - {"请求编号"})


def test_existing_bitable_aliases_are_accepted_and_used_for_payload():
    client = make_client(lambda request: response(request, {"code": 0}))
    available = (REQUIRED_OPPORTUNITY_FIELDS - {"原始拜访记录", "预算", "商机阶段", "AI置信度", "处理状态", "规则版本"}) | {
        "拜访原文",
        "预算信息",
        "AI 建议阶段",
        "AI 置信度",
        "人工确认状态",
        "分析版本",
    }
    resolved = client.resolve_opportunity_field_names(available_fields=available)
    assert resolved["原始拜访记录"] == "拜访原文"
    assert resolved["商机阶段"] == "AI 建议阶段"
    payload = client.opportunity_fields(analysis())
    assert payload["拜访原文"] == analysis().original_note
    assert payload["AI 建议阶段"] == "S0 线索"
    assert payload["人工确认状态"] == "待确认"
    assert payload["未确认信息"] == "无"
    assert payload["矛盾信息"] == "无"


def test_customer_history_uses_exact_field_filter():
    captured = {}

    def handler(request: httpx.Request):
        if request.url.path.endswith("tenant_access_token/internal"):
            return response(request, {"code": 0, "tenant_access_token": "token", "expire": 7200})
        captured.update(json.loads(request.content))
        return response(
            request,
            {"code": 0, "data": {"items": [{"record_id": "rec1", "fields": {"客户名称": "澄明科技"}}], "has_more": False}},
        )

    records = make_client(handler).get_customer_history(" 澄明科技 ")
    condition = captured["filter"]["conditions"][0]
    assert condition == {"field_name": "客户名称", "operator": "is", "value": ["澄明科技"]}
    assert records[0]["record_id"] == "rec1"


def test_upsert_updates_existing_request_instead_of_creating_duplicate():
    methods = []

    def handler(request: httpx.Request):
        if request.url.path.endswith("tenant_access_token/internal"):
            return response(request, {"code": 0, "tenant_access_token": "token", "expire": 7200})
        methods.append((request.method, request.url.path))
        if request.url.path.endswith("records/search"):
            return response(
                request,
                {"code": 0, "data": {"items": [{"record_id": "rec_existing"}], "has_more": False}},
            )
        payload = json.loads(request.content)
        assert payload["fields"]["请求编号"] == "req-001"
        return response(request, {"code": 0, "data": {"record": {"record_id": "rec_existing"}}})

    record_id = make_client(handler).upsert_opportunity(analysis())
    assert record_id == "rec_existing"
    assert methods[-1][0] == "PUT"
    assert methods[-1][1].endswith("/records/rec_existing")


def test_upsert_creates_when_request_id_does_not_exist():
    methods = []

    def handler(request: httpx.Request):
        if request.url.path.endswith("tenant_access_token/internal"):
            return response(request, {"code": 0, "tenant_access_token": "token", "expire": 7200})
        methods.append(request.method)
        if request.url.path.endswith("records/search"):
            return response(request, {"code": 0, "data": {"items": [], "has_more": False}})
        return response(request, {"code": 0, "data": {"record": {"record_id": "rec_new"}}})

    assert make_client(handler).upsert_opportunity(analysis()) == "rec_new"
    assert methods == ["POST", "POST"]


def test_stage_rules_are_loaded_from_bitable_text_cells():
    items = []
    for rank in range(6):
        stage = f"S{rank}"
        requirements = [] if rank == 0 else [{"any_of": ["customer_needs"]}]
        items.append(
            {
                "record_id": f"rec{rank}",
                "fields": {
                    "阶段代码": [{"type": "text", "text": stage}],
                    "阶段名称": f"阶段{rank}",
                    "排序": rank,
                    "条件": "测试条件",
                    "规则JSON": json.dumps(requirements, ensure_ascii=False),
                    "是否兜底": rank == 0,
                    "启用": True,
                },
            }
        )

    def handler(request: httpx.Request):
        if request.url.path.endswith("tenant_access_token/internal"):
            return response(request, {"code": 0, "tenant_access_token": "token", "expire": 7200})
        return response(request, {"code": 0, "data": {"items": items, "has_more": False}})

    rules = make_client(handler, stage_rules_table_id="tbl_rules").load_stage_rules()
    assert [rule.stage for rule in rules] == ["S0", "S1", "S2", "S3", "S4", "S5"]


def test_stage_rules_are_loaded_from_existing_compact_schema():
    items = [
        {
            "record_id": f"rec{rank}",
            "fields": {
                "阶段": [{"type": "text", "text": f"S{rank}"}],
                "名称": [{"type": "text", "text": f"阶段{rank}"}],
                "优先级": rank,
                "达成条件": [{"type": "text", "text": "测试条件"}],
            },
        }
        for rank in range(6)
    ]

    def handler(request: httpx.Request):
        if request.url.path.endswith("tenant_access_token/internal"):
            return response(request, {"code": 0, "tenant_access_token": "token", "expire": 7200})
        return response(request, {"code": 0, "data": {"items": items, "has_more": False}})

    rules = make_client(handler, stage_rules_table_id="tbl_rules").load_stage_rules()
    assert rules[0].fallback is True
    assert rules[1].requirements[0].any_of == ["customer_needs", "core_scenarios"]
    assert rules[5].requirements[0].any_of == ["contract_or_order"]


def test_api_error_does_not_include_credentials():
    def handler(request: httpx.Request):
        if request.url.path.endswith("tenant_access_token/internal"):
            return response(request, {"code": 0, "tenant_access_token": "token", "expire": 7200})
        return response(request, {"code": 1254003, "msg": "WrongRequestBody"})

    with pytest.raises(FeishuAPIError) as exc_info:
        make_client(handler).list_fields()
    message = str(exc_info.value)
    assert "secret_test" not in message
    assert "token" not in message


def test_unauthorized_response_refreshes_token_once_even_without_network_retries():
    token_calls = 0
    field_calls = 0

    def handler(request: httpx.Request):
        nonlocal token_calls, field_calls
        if request.url.path.endswith("tenant_access_token/internal"):
            token_calls += 1
            return response(
                request,
                {"code": 0, "tenant_access_token": f"token-{token_calls}", "expire": 7200},
            )
        field_calls += 1
        if field_calls == 1:
            return response(request, {"code": 99991663, "msg": "token expired"}, status=401)
        assert request.headers["Authorization"] == "Bearer token-2"
        return response(request, {"code": 0, "data": {"items": [], "has_more": False}})

    assert make_client(handler, max_retries=0).list_fields() == []
    assert token_calls == 2
    assert field_calls == 2
