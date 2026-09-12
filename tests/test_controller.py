from agent.nodes.controller import controller
from agent.schemas import (
    AgentDecision,
    Evidence,
    EvidenceBackedFact,
    OpportunityAnalysis,
    OpportunityFacts,
    StageAssessment,
)


def _analysis(facts: OpportunityFacts, *, stage: str = "S0", issues=None) -> OpportunityAnalysis:
    return OpportunityAnalysis(
        request_id="req-1",
        original_note="一段足够长的销售拜访原始记录内容。",
        facts=facts,
        stage=stage,
        stage_name="初步接触" if stage == "S0" else "需求明确",
        stage_evidence=[],
        risks=[],
        next_actions=[],
        unconfirmed_information=facts.unconfirmed_labels(),
        confidence=0.6,
        quality_issues=issues or [],
    )


def _confirmed_list(value: str) -> EvidenceBackedFact[list[str]]:
    return EvidenceBackedFact[list[str]](
        status="confirmed",
        value=[value],
        evidence=[Evidence(quote=value)],
    )


def test_controller_asks_for_missing_need_or_scene():
    facts = OpportunityFacts()
    result = controller({"facts": facts.model_dump(), "analysis": _analysis(facts).model_dump()})
    assert result["agent_control"]["decision"] == AgentDecision.ASK_CLARIFICATION.value
    assert len(result["agent_control"]["questions"]) == 1
    assert result["agent_control"]["questions"][0]["required"] is True


def test_controller_is_ready_but_limits_recommended_questions():
    facts = OpportunityFacts(customer_needs=_confirmed_list("统一管理销售线索"))
    result = controller(
        {"facts": facts.model_dump(), "analysis": _analysis(facts, stage="S1").model_dump()}
    )
    control = result["agent_control"]
    assert control["decision"] == AgentDecision.READY_TO_SAVE.value
    assert control["requires_user_confirmation"] is True
    assert len(control["recommended_questions"]) <= 2


def test_controller_never_resolves_contradiction_itself():
    facts = OpportunityFacts(
        customer_needs=_confirmed_list("统一管理销售线索"),
        budget=EvidenceBackedFact[str](
            status="contradictory",
            evidence=[Evidence(quote="预算二十万"), Evidence(quote="预算尚未批准")],
            conflicting_values=["预算二十万", "预算尚未批准"],
        ),
    )
    result = controller(
        {"facts": facts.model_dump(), "analysis": _analysis(facts, stage="S1").model_dump()}
    )
    assert result["agent_control"]["decision"] == AgentDecision.CONFIRM_CONTRADICTION.value
    assert result["agent_control"]["requires_user_confirmation"] is False


def test_quality_issue_routes_to_manual_review():
    facts = OpportunityFacts(customer_needs=_confirmed_list("统一管理销售线索"))
    result = controller(
        {
            "facts": facts.model_dump(),
            "analysis": _analysis(facts, stage="S1", issues=["证据无法定位"]).model_dump(),
        }
    )
    assert result["agent_control"]["decision"] == AgentDecision.MANUAL_REVIEW.value


def test_stage_regression_routes_to_manual_review():
    facts = OpportunityFacts(customer_needs=_confirmed_list("统一管理销售线索"))
    analysis = _analysis(facts, stage="S1")
    analysis.stage_assessment = StageAssessment(
        current_stage="S1",
        current_stage_name="需求初探",
        matched_stages=["S1"],
        next_stage="S2",
        next_stage_name="方案验证",
        next_stage_reason="缺少方案验证承诺",
        next_stage_gaps=["方案验证承诺"],
        regressed_from="S3",
        regression_reason="重新评估结果由 S3 降至 S1。",
    )
    result = controller({"facts": facts.model_dump(), "analysis": analysis.model_dump()})
    assert result["agent_control"]["decision"] == AgentDecision.MANUAL_REVIEW.value
    assert "S3 降至 S1" in result["agent_control"]["reason"]
