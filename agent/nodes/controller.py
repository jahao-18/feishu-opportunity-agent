from __future__ import annotations

from agent.schemas import (
    AgentControl,
    AgentDecision,
    ClarificationQuestion,
    FactStatus,
    OpportunityAnalysis,
    OpportunityFacts,
    RiskItem,
)
from agent.state import AgentState


def _question(
    question_id: str,
    field: str,
    text: str,
    reason: str,
    *,
    required: bool,
) -> ClarificationQuestion:
    return ClarificationQuestion(
        id=question_id,
        field=field,
        question=text,
        reason=reason,
        required=required,
    )


def _recommended_questions(
    facts: OpportunityFacts,
    stage: str,
    risks: list[RiskItem],
) -> list[ClarificationQuestion]:
    questions: list[ClarificationQuestion] = []
    risk_questions = {
        "决策链不清晰": _question(
            "decision_maker",
            "decision_makers",
            "最终决策人是谁，关键影响人有哪些，内部需要经过哪些审批节点？",
            "这是当前最高优先级风险之一；明确决策链可以避免商机停滞。",
            required=False,
        ),
        "预算未确认": _question(
            "budget",
            "budget",
            "客户是否确认预算范围、资金来源或预算审批状态？请尽量保留客户原话。",
            "这是当前主要商务风险；确认后才能判断采购可行性。",
            required=False,
        ),
        "时间计划缺失": _question(
            "timeline",
            "timeline",
            "客户是否明确下一次沟通、采购决策或上线时间？",
            "明确时间点才能形成可执行的推进节奏。",
            required=False,
        ),
        "需求价值不足": _question(
            "core_scenario",
            "core_scenarios",
            "这个需求发生在哪个具体流程，涉及哪些角色、发生频率和当前影响是什么？",
            "把抽象需求转化为可验证的业务场景。",
            required=False,
        ),
        "客户态度模糊": _question(
            "validation_commitment",
            "validation_commitments",
            "客户是否明确同意演示、试用、技术交流或方案评估？时间和参与人是谁？",
            "用明确承诺验证客户的实际推进意愿。",
            required=False,
        ),
    }
    for risk in risks:
        question = risk_questions.get(risk.type)
        if question and question.id not in {item.id for item in questions}:
            questions.append(question)

    if stage == "S1" and facts.validation_commitments.status != FactStatus.CONFIRMED:
        questions.append(
            _question(
                "validation_commitment",
                "validation_commitments",
                "客户是否明确同意演示、试用、技术交流或方案评估？",
                "用于判断是否具备进入 S2 方案验证阶段的证据。",
                required=False,
            )
        )
    if stage in {"S1", "S2"} and facts.budget.status != FactStatus.CONFIRMED:
        questions.append(
            _question(
                "budget",
                "budget",
                "客户是否讨论过预算范围、报价或采购流程？请尽量保留客户原话。",
                "用于评估商务可行性，并辅助判断是否进入 S3。",
                required=False,
            )
        )
    if stage in {"S2", "S3"} and facts.decision_makers.status != FactStatus.CONFIRMED:
        questions.append(
            _question(
                "decision_maker",
                "decision_makers",
                "最终决策人是谁，内部还需要经过哪些审批或供应商决策环节？",
                "用于明确决策链和 S4 的推进条件。",
                required=False,
            )
        )
    if stage == "S4" and facts.contract_or_order.status != FactStatus.CONFIRMED:
        questions.append(
            _question(
                "contract_or_order",
                "contract_or_order",
                "合同是否已经签署，或正式订单是否已经确认？",
                "用于判断是否达到 S5 赢单/签约。",
                required=False,
            )
        )
    if facts.timeline.status != FactStatus.CONFIRMED:
        questions.append(
            _question(
                "timeline",
                "timeline",
                "客户是否明确下一次沟通、采购或上线时间？",
                "用于形成可执行的推进计划。",
                required=False,
            )
        )
    unique = {question.id: question for question in questions}
    return list(unique.values())[:2]


def controller(state: AgentState) -> AgentState:
    facts = OpportunityFacts.model_validate(state["facts"])
    analysis = OpportunityAnalysis.model_validate(state["analysis"])
    answered_ids = {
        item.get("question_id")
        for item in state.get("clarifications", [])
        if item.get("answer")
    }

    contradictory = facts.contradictory_fields()
    if contradictory:
        questions = [
            _question(
                f"contradiction_{label}",
                label,
                f"关于“{label}”存在冲突：{' / '.join(fact.conflicting_values)}。请确认当前有效信息。",
                "题目要求矛盾信息不得由 Agent 自行选择。",
                required=True,
            )
            for label, fact in contradictory
        ][:2]
        unanswered = [question for question in questions if question.id not in answered_ids]
        if unanswered:
            control = AgentControl(
                decision=AgentDecision.CONFIRM_CONTRADICTION,
                reason="关键商机字段存在矛盾，需要用户确认后才能保存。",
                questions=unanswered,
            )
        else:
            control = AgentControl(
                decision=AgentDecision.MANUAL_REVIEW,
                reason="补充确认后仍存在矛盾，建议转人工复核。",
            )
    elif analysis.stage_assessment and analysis.stage_assessment.regressed_from:
        control = AgentControl(
            decision=AgentDecision.MANUAL_REVIEW,
            reason=analysis.stage_assessment.regression_reason
            or "商机阶段发生回退，需要人工复核。",
        )
    elif facts.customer_needs.status != FactStatus.CONFIRMED and facts.core_scenarios.status != FactStatus.CONFIRMED:
        question = _question(
            "customer_need",
            "customer_needs",
            "客户明确希望解决的业务问题或具体使用场景是什么？",
            "当前只有初步接触信息，至少需要一个明确需求或场景才能形成有效商机。",
            required=True,
        )
        if question.id in answered_ids:
            control = AgentControl(
                decision=AgentDecision.MANUAL_REVIEW,
                reason="用户已经补充过需求，但仍无法提取明确事实，建议人工复核。",
            )
        else:
            control = AgentControl(
                decision=AgentDecision.ASK_CLARIFICATION,
                reason="缺少形成有效商机所必需的需求或场景信息。",
                questions=[question],
            )
    elif analysis.quality_issues:
        control = AgentControl(
            decision=AgentDecision.MANUAL_REVIEW,
            reason="分析结果触发质量门禁，需要人工复核后再保存。",
        )
    else:
        recommended = [
            question
            for question in _recommended_questions(facts, analysis.stage, analysis.risks)
            if question.id not in answered_ids
        ][:2]
        control = AgentControl(
            decision=AgentDecision.READY_TO_SAVE,
            reason="已形成有证据支持的结构化商机，可以在用户确认后保存。",
            recommended_questions=recommended,
            requires_user_confirmation=True,
        )

    analysis.agent_control = control
    return {
        "agent_control": control.model_dump(mode="json"),
        "analysis": analysis.model_dump(mode="json"),
        "tool_trace": list(state.get("tool_trace", [])) + ["controller.decide"],
    }
