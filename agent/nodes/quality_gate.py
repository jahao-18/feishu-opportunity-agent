from __future__ import annotations

from agent.schemas import OpportunityAnalysis, OpportunityFacts
from agent.state import AgentState


def quality_gate(state: AgentState) -> AgentState:
    issues = list(state.get("quality_issues", []))
    facts = OpportunityFacts.model_validate(state["facts"])
    if state["stage"] in {"S3", "S4", "S5"} and not state["stage_evidence"]:
        issues.append("高阶段缺少阶段证据，建议人工复核。")
    if facts.contradictory_fields():
        issues.append("存在矛盾信息，需人工或客户确认。")

    analysis = OpportunityAnalysis(
        request_id=state["request_id"],
        original_note=state["raw_note"],
        facts=facts,
        stage=state["stage"],
        stage_name=state["stage_name"],
        stage_evidence=state["stage_evidence"],
        stage_assessment=state.get("stage_assessment"),
        risks=state["risks"],
        next_actions=state["next_actions"],
        unconfirmed_information=state["unconfirmed_information"],
        confidence=state["confidence"],
        quality_issues=issues,
        rule_version=state.get("rule_version", "v1.0"),
        risk_rule_version=state.get("risk_rule_version", "v1.0"),
        model_metadata=state.get("model_metadata"),
    )
    return {"analysis": analysis.model_dump(mode="json"), "quality_issues": issues}
