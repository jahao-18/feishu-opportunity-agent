from __future__ import annotations

import json
from dataclasses import dataclass

from agent.schemas import AgentDecision, OpportunityAnalysis


@dataclass(frozen=True)
class TraceItem:
    label: str
    detail: str
    status: str = "complete"


def build_agent_trace(result: OpportunityAnalysis) -> list[TraceItem]:
    """Build an explainable UI trace from validated Agent output."""

    confirmed_count = sum(
        1
        for field_name in type(result.facts).model_fields
        if getattr(result.facts, field_name).is_confirmed
    )
    trace = [
        TraceItem("读取拜访记录", f"请求编号：{result.request_id}"),
        TraceItem("提取并校验事实", f"提取 {confirmed_count} 个已确认字段，证据与原文绑定"),
        TraceItem("加载阶段规则", f"规则版本：{result.rule_version}"),
        TraceItem("执行确定性判级", f"当前阶段：{result.stage} {result.stage_name}"),
        TraceItem(
            "生成风险与行动",
            f"识别 {len(result.risks)} 项风险，生成 {len(result.next_actions)} 项行动",
        ),
    ]

    if result.quality_issues:
        trace.append(
            TraceItem(
                "质量门禁",
                f"发现 {len(result.quality_issues)} 个问题，已转人工复核",
                "warning",
            )
        )
    else:
        trace.append(TraceItem("质量门禁", "Schema、证据和事实边界校验通过"))

    if result.agent_control:
        decision_labels = {
            AgentDecision.ASK_CLARIFICATION: "请求补充信息",
            AgentDecision.CONFIRM_CONTRADICTION: "请求确认矛盾",
            AgentDecision.READY_TO_SAVE: "等待用户确认保存",
            AgentDecision.MANUAL_REVIEW: "进入人工复核",
            AgentDecision.SAVED: "保存完成",
            AgentDecision.ENDED: "会话结束",
        }
        trace.append(
            TraceItem(
                "Controller 决策",
                decision_labels[result.agent_control.decision],
                "warning"
                if result.agent_control.decision == AgentDecision.MANUAL_REVIEW
                else "complete",
            )
        )

    trace.append(
        TraceItem(
            "同步飞书",
            f"记录 ID：{result.feishu_record_id}"
            if result.feishu_record_id
            else "尚未执行，需用户确认保存",
            "complete" if result.feishu_record_id else "pending",
        )
    )
    return trace


def analysis_as_json(result: OpportunityAnalysis) -> str:
    return json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2)
