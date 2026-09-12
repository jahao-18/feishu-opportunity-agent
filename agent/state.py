from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    request_id: str
    base_note: str
    raw_note: str
    clarifications: list[dict[str, Any]]
    facts: dict[str, Any]
    previous_stage: str
    stage: str
    stage_name: str
    stage_evidence: list[dict[str, Any]]
    stage_assessment: dict[str, Any]
    risks: list[dict[str, Any]]
    next_actions: list[dict[str, Any]]
    unconfirmed_information: list[str]
    confidence: float
    analysis: dict[str, Any]
    feishu_record_id: str | None
    errors: list[str]
    quality_issues: list[str]
    rule_version: str
    risk_rule_version: str
    model_metadata: dict[str, Any]
    agent_control: dict[str, Any]
    tool_trace: list[str]
