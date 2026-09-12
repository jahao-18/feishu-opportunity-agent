from __future__ import annotations

from agent.schemas import OpportunityFacts
from agent.state import AgentState
from integrations.ark_client import ArkExtractor


def extract_facts(state: AgentState) -> AgentState:
    result = ArkExtractor.from_env().extract(state["raw_note"])
    issues = list(state.get("quality_issues", []))
    if result.metadata.fallback_used:
        issues.append("豆包调用失败，本次使用 Mock 提取器降级处理，必须人工复核。")
    return {
        "facts": OpportunityFacts.model_validate(result.facts).model_dump(mode="json"),
        "model_metadata": result.metadata.model_dump(mode="json"),
        "quality_issues": issues,
    }
