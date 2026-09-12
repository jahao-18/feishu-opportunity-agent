import json

from agent.graph import build_graph
from agent.schemas import AgentDecision, OpportunityAnalysis


def test_mock_graph_returns_structured_analysis(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "true")
    monkeypatch.setenv("FEISHU_SYNC_ENABLED", "false")
    result = build_graph().invoke(
        {"raw_note": "客户希望统一管理销售线索，并且同意下周安排产品演示。预算和决策人尚未确认。"}
    )
    analysis = OpportunityAnalysis.model_validate(result["analysis"])
    assert analysis.stage == "S2"
    assert analysis.stage_evidence
    assert analysis.stage_assessment is not None
    assert analysis.stage_assessment.next_stage == "S3"
    assert analysis.stage_assessment.next_stage_gaps
    assert analysis.model_metadata is not None
    assert analysis.model_metadata.provider == "mock"
    assert analysis.agent_control is not None
    assert analysis.agent_control.decision == AgentDecision.READY_TO_SAVE
    assert analysis.risks[0].type == "决策链不清晰"
    assert analysis.risks[0].evidence
    assert analysis.agent_control.recommended_questions[0].id == "decision_maker"
    assert all(action.due_time != "待确认" for action in analysis.next_actions)
    assert result.get("feishu_record_id") is None
    json.dumps(result["analysis"], ensure_ascii=False)


def test_short_input_is_rejected(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "true")
    result = build_graph().invoke({"raw_note": "太短了"})
    assert result["errors"]
    assert "analysis" not in result


def test_analysis_graph_never_writes_feishu_without_confirmation(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "true")
    monkeypatch.setenv("FEISHU_SYNC_ENABLED", "true")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("analysis graph must not persist automatically")

    monkeypatch.setattr(
        "integrations.feishu_bitable.FeishuBitableClient.create_opportunity",
        fail_if_called,
    )
    result = build_graph().invoke(
        {"raw_note": "客户明确希望统一管理销售线索，避免销售人员重复录入客户资料。"}
    )
    analysis = OpportunityAnalysis.model_validate(result["analysis"])
    assert analysis.agent_control.decision == AgentDecision.READY_TO_SAVE
    assert result.get("feishu_record_id") is None
