from __future__ import annotations

from agent.schemas import FactStatus, OpportunityFacts
from agent.state import AgentState


def validate_evidence(state: AgentState) -> AgentState:
    facts = OpportunityFacts.model_validate(state["facts"])
    issues: list[str] = list(state.get("quality_issues", []))
    for field_name in type(facts).model_fields:
        fact = getattr(facts, field_name)
        if fact.status == FactStatus.CONFIRMED:
            missing_quotes = [
                item.quote
                for item in fact.evidence
                if item.source in {"visit_note", "user_clarification"}
                and item.quote not in state["raw_note"]
            ]
            if missing_quotes:
                issues.append(f"{field_name} 的证据无法在原始记录中定位，建议人工复核。")

    return {"facts": facts.model_dump(mode="json"), "quality_issues": issues}
