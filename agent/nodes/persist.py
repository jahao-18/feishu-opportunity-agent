from __future__ import annotations

from agent.schemas import OpportunityAnalysis
from agent.state import AgentState
from integrations.feishu_bitable import FeishuBitableClient


def persist_to_feishu(state: AgentState) -> AgentState:
    analysis = OpportunityAnalysis.model_validate(state["analysis"])
    client = FeishuBitableClient.from_env()
    if not client.enabled:
        return {"feishu_record_id": None}
    record_id = client.create_opportunity(analysis)
    analysis.feishu_record_id = record_id
    return {"feishu_record_id": record_id, "analysis": analysis.model_dump(mode="json")}
