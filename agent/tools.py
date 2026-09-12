from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from agent.nodes.stage_engine import load_effective_stage_rules
from agent.schemas import OpportunityAnalysis, OpportunityFacts
from integrations.ark_client import ArkExtractor
from integrations.feishu_bitable import FeishuBitableClient


class ToolUnavailableError(RuntimeError):
    """Raised when a tool belongs to a later integration stage or is disabled."""


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    side_effect: bool
    handler: Callable[..., Any]


def extract_opportunity_facts(note: str) -> OpportunityFacts:
    return OpportunityFacts.model_validate(ArkExtractor.from_env().extract(note).facts)


def get_stage_rules() -> list[dict[str, Any]]:
    rules, _, _ = load_effective_stage_rules()
    return [rule.model_dump(mode="json") for rule in rules]


def validate_evidence(facts: OpportunityFacts, source_text: str) -> list[str]:
    issues: list[str] = []
    for field_name in type(facts).model_fields:
        fact = getattr(facts, field_name)
        for evidence in fact.evidence:
            if evidence.source in {"visit_note", "user_clarification"} and evidence.quote not in source_text:
                issues.append(f"{field_name} 的证据无法在输入内容中定位。")
    return issues


def get_customer_history(customer_name: str) -> list[dict[str, Any]]:
    client = FeishuBitableClient.from_env()
    if not client.enabled:
        raise ToolUnavailableError("飞书同步未启用，无法查询客户历史。")
    if not client.configured:
        raise ToolUnavailableError("飞书连接参数尚未配置完整，无法查询客户历史。")
    return client.get_customer_history(customer_name)


def save_opportunity(analysis: OpportunityAnalysis) -> str:
    client = FeishuBitableClient.from_env()
    if not client.enabled:
        raise ToolUnavailableError("飞书同步未启用，请先完成飞书应用和多维表格配置。")
    report = client.validate_schema()
    if not report.ok:
        missing = "、".join(report.missing_fields)
        raise ToolUnavailableError(report.message + (f" 缺少：{missing}" if missing else ""))
    return client.upsert_opportunity(analysis)


def create_followup_task(*, title: str, owner: str, due_time: str) -> str:
    raise ToolUnavailableError(
        "跟进任务由飞书多维表格工作流在新增记录后按 P0 条件创建，"
        f"Agent 不直接调用任务 API（{title} / {owner} / {due_time}）。"
    )


TOOLS: dict[str, AgentTool] = {
    "extract_opportunity_facts": AgentTool(
        name="extract_opportunity_facts",
        description="从销售记录中提取带证据的结构化商机事实。",
        side_effect=False,
        handler=extract_opportunity_facts,
    ),
    "get_stage_rules": AgentTool(
        name="get_stage_rules",
        description="读取版本化的 S0-S5 商机阶段规则。",
        side_effect=False,
        handler=get_stage_rules,
    ),
    "validate_evidence": AgentTool(
        name="validate_evidence",
        description="校验证据原文能否在用户输入中定位。",
        side_effect=False,
        handler=validate_evidence,
    ),
    "get_customer_history": AgentTool(
        name="get_customer_history",
        description="从飞书多维表格读取客户历史记录。",
        side_effect=False,
        handler=get_customer_history,
    ),
    "save_opportunity": AgentTool(
        name="save_opportunity",
        description="经用户确认后将商机保存到飞书多维表格。",
        side_effect=True,
        handler=save_opportunity,
    ),
    "create_followup_task": AgentTool(
        name="create_followup_task",
        description="说明跟进任务的创建边界；实际创建由飞书工作流负责。",
        side_effect=True,
        handler=create_followup_task,
    ),
}
