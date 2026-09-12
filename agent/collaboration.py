from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from agent.schemas import AgentDecision, OpportunityAnalysis


class CollaborationRoute(str, Enum):
    """Business routes mirrored by the Feishu multi-branch workflow."""

    KEY_OPPORTUNITY = "key_opportunity"
    HIGH_RISK = "high_risk"
    P0_FOLLOWUP = "p0_followup"
    INFORMATION_MISSING = "information_missing"
    MANUAL_REVIEW = "manual_review"


@dataclass(frozen=True)
class CollaborationAction:
    route: CollaborationRoute
    action: str
    reason: str


def plan_collaboration(analysis: OpportunityAnalysis) -> list[CollaborationAction]:
    """Return every matching route; routes are intentionally non-exclusive."""

    actions: list[CollaborationAction] = []
    if int(analysis.stage[1]) >= 3:
        actions.append(
            CollaborationAction(
                route=CollaborationRoute.KEY_OPPORTUNITY,
                action="notify_owner",
                reason=f"商机已进入 {analysis.stage}，需要销售负责人及时推进。",
            )
        )
    if any(risk.level == "高" for risk in analysis.risks):
        actions.append(
            CollaborationAction(
                route=CollaborationRoute.HIGH_RISK,
                action="notify_reviewer",
                reason="存在高风险项，需要人工复核。",
            )
        )
    if any(item.priority == "P0" for item in analysis.next_actions):
        actions.append(
            CollaborationAction(
                route=CollaborationRoute.P0_FOLLOWUP,
                action="create_task",
                reason="存在 P0 下一步行动，需要创建跟进任务。",
            )
        )
    if analysis.unconfirmed_information:
        actions.append(
            CollaborationAction(
                route=CollaborationRoute.INFORMATION_MISSING,
                action="mark_for_completion",
                reason="仍有关键字段未确认，需要销售补充信息。",
            )
        )
    manual_review = bool(analysis.quality_issues) or (
        analysis.agent_control is not None
        and analysis.agent_control.decision == AgentDecision.MANUAL_REVIEW
    )
    if manual_review:
        actions.append(
            CollaborationAction(
                route=CollaborationRoute.MANUAL_REVIEW,
                action="enqueue_manual_review",
                reason="质量门禁或 Controller 要求人工复核。",
            )
        )
    return actions
