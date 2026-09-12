from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, ClassVar
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field

from agent.schemas import OpportunityAnalysis, StageRule


API_BASE = "https://open.feishu.cn/open-apis"
REQUIRED_OPPORTUNITY_FIELDS = {
    "请求编号",
    "原始拜访记录",
    "客户名称",
    "客户需求",
    "核心场景",
    "预算",
    "决策人",
    "影响人",
    "时间计划",
    "商机阶段",
    "阶段判断依据",
    "风险",
    "下一步行动",
    "未确认信息",
    "矛盾信息",
    "AI置信度",
    "处理状态",
    "规则版本",
}

OPPORTUNITY_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "原始拜访记录": ("原始拜访记录", "拜访原文"),
    "预算": ("预算", "预算信息"),
    "商机阶段": ("商机阶段", "AI 建议阶段"),
    "AI置信度": ("AI置信度", "AI 置信度"),
    "处理状态": ("处理状态", "人工确认状态"),
    "规则版本": ("规则版本", "分析版本"),
}

REMOTE_STAGE_REQUIREMENTS: dict[str, list[dict[str, list[str]]]] = {
    "S0": [],
    "S1": [{"any_of": ["customer_needs", "core_scenarios"]}],
    "S2": [{"any_of": ["validation_commitments"]}],
    "S3": [{"any_of": ["commercial_discussions"]}],
    "S4": [{"any_of": ["decision_progress"]}],
    "S5": [{"any_of": ["contract_or_order"]}],
}

FEISHU_STAGE_OPTION_NAMES = {
    "S0": "S0 线索",
    "S1": "S1 明确需求",
    "S2": "S2 方案验证",
    "S3": "S3 商务评估",
    "S4": "S4 决策审批",
    "S5": "S5 赢单签约",
}


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


class FeishuAPIError(RuntimeError):
    def __init__(self, operation: str, message: str, *, code: int | None = None):
        self.operation = operation
        self.code = code
        super().__init__(f"{operation}失败：{message}" + (f"（code={code}）" if code is not None else ""))


class FeishuSchemaReport(BaseModel):
    enabled: bool
    configured: bool
    ok: bool
    available_fields: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    message: str


@dataclass
class FeishuBitableClient:
    enabled: bool
    app_id: str | None
    app_secret: str | None
    app_token: str | None
    table_id: str | None
    stage_rules_table_id: str | None = None
    eval_table_id: str | None = None
    timeout_seconds: float = 20.0
    max_retries: int = 2
    http_client: httpx.Client = field(default_factory=httpx.Client, repr=False)
    _opportunity_field_map_cache: dict[str, str] | None = field(default=None, init=False, repr=False)

    _TOKEN_CACHE: ClassVar[dict[str, tuple[str, float]]] = {}

    @classmethod
    def from_env(cls) -> "FeishuBitableClient":
        return cls(
            enabled=_truthy(os.getenv("FEISHU_SYNC_ENABLED")),
            app_id=os.getenv("FEISHU_APP_ID"),
            app_secret=os.getenv("FEISHU_APP_SECRET"),
            app_token=os.getenv("FEISHU_BITABLE_APP_TOKEN"),
            table_id=os.getenv("FEISHU_BITABLE_TABLE_ID"),
            stage_rules_table_id=os.getenv("FEISHU_STAGE_RULES_TABLE_ID"),
            eval_table_id=os.getenv("FEISHU_EVAL_TABLE_ID"),
            timeout_seconds=float(os.getenv("FEISHU_TIMEOUT_SECONDS", "20")),
            max_retries=max(0, int(os.getenv("FEISHU_MAX_RETRIES", "2"))),
        )

    @property
    def configured(self) -> bool:
        return all([self.app_id, self.app_secret, self.app_token, self.table_id])

    def _validate_config(self, *, require_main_table: bool = True) -> None:
        values = {
            "FEISHU_APP_ID": self.app_id,
            "FEISHU_APP_SECRET": self.app_secret,
            "FEISHU_BITABLE_APP_TOKEN": self.app_token,
        }
        if require_main_table:
            values["FEISHU_BITABLE_TABLE_ID"] = self.table_id
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise FeishuAPIError("配置检查", f"缺少 {', '.join(missing)}")

    @staticmethod
    def _safe_error(payload: dict[str, Any]) -> tuple[str, int | None]:
        return str(payload.get("msg") or payload.get("message") or "飞书返回未知错误"), payload.get("code")

    def _get_token(self, *, force_refresh: bool = False) -> str:
        self._validate_config(require_main_table=False)
        cache_key = self.app_id or ""
        cached = self._TOKEN_CACHE.get(cache_key)
        if not force_refresh and cached and time.time() < cached[1]:
            return cached[0]

        try:
            response = self.http_client.post(
                f"{API_BASE}/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self.app_secret},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise FeishuAPIError("获取 tenant_access_token", str(exc)) from exc
        if payload.get("code") != 0:
            message, code = self._safe_error(payload)
            raise FeishuAPIError("获取 tenant_access_token", message, code=code)
        token = payload.get("tenant_access_token")
        if not token:
            raise FeishuAPIError("获取 tenant_access_token", "响应中缺少 token")
        expires_at = time.time() + max(60, int(payload.get("expire", 7200)) - 120)
        self._TOKEN_CACHE[cache_key] = (token, expires_at)
        return token

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._validate_config()
        last_error: Exception | None = None
        network_attempt = 0
        refreshed_once = False
        refresh_token_next = False
        while network_attempt <= self.max_retries:
            token = self._get_token(force_refresh=refresh_token_next)
            refresh_token_next = False
            try:
                response = self.http_client.request(
                    method,
                    f"{API_BASE}{path}",
                    headers={"Authorization": f"Bearer {token}"},
                    params=params,
                    json=body,
                    timeout=self.timeout_seconds,
                )
                if response.status_code == 401 and not refreshed_once:
                    self._TOKEN_CACHE.pop(self.app_id or "", None)
                    refreshed_once = True
                    refresh_token_next = True
                    continue
                try:
                    payload = response.json()
                except ValueError:
                    response.raise_for_status()
                    raise
                if payload.get("code") != 0:
                    message, code = self._safe_error(payload)
                    raise FeishuAPIError(operation, message, code=code)
                response.raise_for_status()
                return payload
            except FeishuAPIError:
                raise
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if network_attempt >= self.max_retries:
                    break
                network_attempt += 1
                time.sleep(0.2 * network_attempt)
        raise FeishuAPIError(operation, str(last_error or "未知网络错误"))

    def _table_path(self, suffix: str = "", *, table_id: str | None = None) -> str:
        selected_table = table_id or self.table_id
        return f"/bitable/v1/apps/{self.app_token}/tables/{selected_table}{suffix}"

    def list_fields(self, *, table_id: str | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token
            payload = self._request_json(
                "GET",
                self._table_path("/fields", table_id=table_id),
                operation="查询多维表格字段",
                params=params,
            )
            data = payload.get("data", {})
            items.extend(data.get("items", []))
            if not data.get("has_more"):
                return items
            page_token = data.get("page_token")
            if not page_token:
                raise FeishuAPIError("查询多维表格字段", "分页响应缺少 page_token")

    def create_field(self, *, table_id: str, field_name: str, field_type: int) -> str:
        payload = self._request_json(
            "POST",
            self._table_path("/fields", table_id=table_id),
            operation="新增多维表格字段",
            body={"field_name": field_name, "type": field_type},
        )
        field_id = payload.get("data", {}).get("field", {}).get("field_id")
        if not field_id:
            raise FeishuAPIError("新增多维表格字段", "响应中缺少 field_id")
        return str(field_id)

    def ensure_fields(self, *, table_id: str, fields: dict[str, int]) -> list[str]:
        existing = {
            str(item.get("field_name"))
            for item in self.list_fields(table_id=table_id)
            if item.get("field_name")
        }
        created: list[str] = []
        for field_name, field_type in fields.items():
            if field_name not in existing:
                self.create_field(
                    table_id=table_id,
                    field_name=field_name,
                    field_type=field_type,
                )
                created.append(field_name)
        return created

    def create_record(self, *, table_id: str, fields: dict[str, Any]) -> str:
        payload = self._request_json(
            "POST",
            self._table_path("/records", table_id=table_id),
            operation="新增多维表格记录",
            body={"fields": fields},
        )
        record_id = payload.get("data", {}).get("record", {}).get("record_id")
        if not record_id:
            raise FeishuAPIError("新增多维表格记录", "响应中缺少 record_id")
        return str(record_id)

    def update_record(
        self,
        *,
        table_id: str,
        record_id: str,
        fields: dict[str, Any],
    ) -> str:
        safe_record_id = quote(record_id, safe="")
        payload = self._request_json(
            "PUT",
            self._table_path(f"/records/{safe_record_id}", table_id=table_id),
            operation="更新多维表格记录",
            body={"fields": fields},
        )
        return str(
            payload.get("data", {}).get("record", {}).get("record_id") or record_id
        )

    def validate_schema(self) -> FeishuSchemaReport:
        if not self.enabled:
            return FeishuSchemaReport(
                enabled=False,
                configured=self.configured,
                ok=False,
                message="飞书同步当前未启用。",
            )
        if not self.configured:
            return FeishuSchemaReport(
                enabled=True,
                configured=False,
                ok=False,
                message="飞书同步已启用，但连接参数尚未配置完整。",
            )
        fields = self.list_fields()
        available = sorted(
            {str(item.get("field_name")) for item in fields if item.get("field_name")}
        )
        resolved = self.resolve_opportunity_field_names(available_fields=set(available))
        missing = sorted(REQUIRED_OPPORTUNITY_FIELDS - set(resolved))
        return FeishuSchemaReport(
            enabled=True,
            configured=True,
            ok=not missing,
            available_fields=available,
            missing_fields=missing,
            message="飞书连接和字段检查通过。" if not missing else "飞书表缺少必需字段。",
        )

    def resolve_opportunity_field_names(
        self,
        *,
        available_fields: set[str] | None = None,
    ) -> dict[str, str]:
        if available_fields is None and self._opportunity_field_map_cache is not None:
            return self._opportunity_field_map_cache
        if available_fields is None:
            available_fields = {
                str(item.get("field_name"))
                for item in self.list_fields()
                if item.get("field_name")
            }
        resolved: dict[str, str] = {}
        for canonical in REQUIRED_OPPORTUNITY_FIELDS:
            candidates = OPPORTUNITY_FIELD_ALIASES.get(canonical, (canonical,))
            match = next((candidate for candidate in candidates if candidate in available_fields), None)
            if match:
                resolved[canonical] = match
        if available_fields is not None:
            self._opportunity_field_map_cache = resolved
        return resolved

    def search_records(
        self,
        *,
        field_name: str | None = None,
        value: str | None = None,
        table_id: str | None = None,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": min(max(page_size, 1), 500)}
            if page_token:
                params["page_token"] = page_token
            body: dict[str, Any] = {}
            if field_name is not None and value is not None:
                body["filter"] = {
                    "conjunction": "and",
                    "conditions": [
                        {"field_name": field_name, "operator": "is", "value": [value]}
                    ],
                }
            payload = self._request_json(
                "POST",
                self._table_path("/records/search", table_id=table_id),
                operation="查询多维表格记录",
                params=params,
                body=body,
            )
            data = payload.get("data", {})
            items.extend(data.get("items", []))
            if not data.get("has_more"):
                return items
            page_token = data.get("page_token")
            if not page_token:
                raise FeishuAPIError("查询多维表格记录", "分页响应缺少 page_token")

    def get_customer_history(self, customer_name: str) -> list[dict[str, Any]]:
        if not customer_name.strip():
            return []
        return self.search_records(field_name="客户名称", value=customer_name.strip())

    @staticmethod
    def _cell_text(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts = []
            for item in value:
                if isinstance(item, dict):
                    parts.append(str(item.get("text") or item.get("name") or item.get("value") or ""))
                else:
                    parts.append(str(item))
            return "".join(parts)
        if isinstance(value, dict):
            return str(value.get("text") or value.get("name") or value.get("value") or "")
        return str(value)

    def load_stage_rules(self) -> list[StageRule]:
        if not self.stage_rules_table_id:
            raise FeishuAPIError("读取飞书阶段规则", "缺少 FEISHU_STAGE_RULES_TABLE_ID")
        records = self.search_records(table_id=self.stage_rules_table_id)
        rules: list[StageRule] = []
        for record in records:
            fields = record.get("fields", {})
            if fields.get("启用") is False:
                continue
            stage = self._cell_text(fields.get("阶段代码") or fields.get("阶段"))
            raw_requirements = self._cell_text(fields.get("规则JSON"))
            if raw_requirements:
                raw_requirements = json.loads(raw_requirements)
                if isinstance(raw_requirements, dict):
                    raw_requirements = raw_requirements.get("requirements", [])
            else:
                raw_requirements = REMOTE_STAGE_REQUIREMENTS.get(stage or "", [])
            rules.append(
                StageRule.model_validate(
                    {
                        "stage": stage,
                        "name": self._cell_text(fields.get("阶段名称") or fields.get("名称")),
                        "rank": fields.get("排序", fields.get("优先级")),
                        "condition": self._cell_text(fields.get("条件") or fields.get("达成条件")),
                        "requirements": raw_requirements,
                        "fallback": bool(fields.get("是否兜底", stage == "S0")),
                    }
                )
            )
        if len(rules) != 6:
            raise FeishuAPIError("读取飞书阶段规则", "启用的 S0-S5 规则必须恰好为 6 条")
        return sorted(rules, key=lambda rule: rule.rank)

    @staticmethod
    def _join(values: list[str]) -> str:
        return "\n".join(values) if values else "未确认"

    @staticmethod
    def _join_or_none(values: list[str]) -> str:
        """Serialize workflow list fields with an unambiguous empty sentinel."""
        return "\n".join(values) if values else "无"

    @staticmethod
    def _text_fact(fact) -> str:
        return str(fact.value) if fact.is_confirmed else "未确认"

    @staticmethod
    def _list_fact(fact) -> list[str]:
        return fact.value if fact.is_confirmed and fact.value else []

    def opportunity_fields(self, analysis: OpportunityAnalysis) -> dict[str, Any]:
        canonical = {
            "请求编号": analysis.request_id,
            "原始拜访记录": analysis.original_note,
            "客户名称": self._text_fact(analysis.facts.customer_name),
            "客户需求": self._join(self._list_fact(analysis.facts.customer_needs)),
            "核心场景": self._join(self._list_fact(analysis.facts.core_scenarios)),
            "预算": self._text_fact(analysis.facts.budget),
            "决策人": self._join(self._list_fact(analysis.facts.decision_makers)),
            "影响人": self._join(self._list_fact(analysis.facts.influencers)),
            "时间计划": self._text_fact(analysis.facts.timeline),
            "商机阶段": FEISHU_STAGE_OPTION_NAMES[analysis.stage],
            "阶段判断依据": self._join([item.quote for item in analysis.stage_evidence]),
            "风险": "\n".join(
                f"[{risk.level}] {risk.type}：{risk.description}｜依据：{risk.evidence}"
                for risk in analysis.risks
            ) or "暂无明确风险",
            "下一步行动": "\n".join(
                f"[{action.priority}] {action.action}｜负责人：{action.owner}｜时间：{action.due_time}"
                for action in analysis.next_actions
            ),
            "未确认信息": self._join_or_none(analysis.unconfirmed_information),
            "矛盾信息": self._join_or_none(
                [
                    f"{label}：{' / '.join(fact.conflicting_values)}"
                    for label, fact in analysis.facts.contradictory_fields()
                ]
            ),
            "AI置信度": analysis.confidence,
            "处理状态": "需修改" if analysis.quality_issues else "待确认",
            "规则版本": f"阶段规则={analysis.rule_version}; 风险规则={analysis.risk_rule_version}",
        }
        field_names = self.resolve_opportunity_field_names()
        return {
            field_names[name]: value
            for name, value in canonical.items()
            if name in field_names
        }

    def create_opportunity(self, analysis: OpportunityAnalysis) -> str:
        payload = self._request_json(
            "POST",
            self._table_path("/records"),
            operation="新增商机记录",
            body={"fields": self.opportunity_fields(analysis)},
        )
        try:
            return payload["data"]["record"]["record_id"]
        except (KeyError, TypeError) as exc:
            raise FeishuAPIError("新增商机记录", "响应中缺少 record_id") from exc

    def update_opportunity(self, record_id: str, analysis: OpportunityAnalysis) -> str:
        safe_record_id = quote(record_id, safe="")
        payload = self._request_json(
            "PUT",
            self._table_path(f"/records/{safe_record_id}"),
            operation="更新商机记录",
            body={"fields": self.opportunity_fields(analysis)},
        )
        return payload.get("data", {}).get("record", {}).get("record_id") or record_id

    def upsert_opportunity(self, analysis: OpportunityAnalysis) -> str:
        request_field = self.resolve_opportunity_field_names().get("请求编号")
        if not request_field:
            raise FeishuAPIError("保存商机记录", "多维表格缺少文本字段‘请求编号’，无法保证幂等写入")
        existing = self.search_records(field_name=request_field, value=analysis.request_id, page_size=20)
        if existing:
            record_id = existing[0].get("record_id")
            if not record_id:
                raise FeishuAPIError("更新商机记录", "查询结果缺少 record_id")
            return self.update_opportunity(record_id, analysis)
        return self.create_opportunity(analysis)
