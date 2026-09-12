import pytest

from agent.schemas import AgentDecision, OpportunityAnalysis
from agent.service import ConversationError, OpportunityAgentService
from agent.tools import ToolUnavailableError


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "true")
    monkeypatch.setenv("FEISHU_SYNC_ENABLED", "false")
    return OpportunityAgentService()


def test_multi_turn_clarification_preserves_request_and_reanalyzes(service):
    first = service.start("今天与客户进行了首次沟通，但对方暂时没有说明具体需求和应用场景。")
    first_analysis = OpportunityAnalysis.model_validate(first["analysis"])
    assert first_analysis.agent_control.decision == AgentDecision.ASK_CLARIFICATION

    second = service.continue_with(
        first,
        {"customer_need": "客户明确希望统一管理销售线索，避免销售重复录入。"},
    )
    second_analysis = OpportunityAnalysis.model_validate(second["analysis"])
    assert second["request_id"] == first["request_id"]
    assert second["base_note"] == first["base_note"]
    assert len(second["clarifications"]) == 1
    assert second_analysis.stage == "S1"
    assert second_analysis.agent_control.decision == AgentDecision.READY_TO_SAVE
    assert "conversation.merge" in second["tool_trace"]


def test_unknown_or_empty_answer_is_rejected(service):
    state = service.start("今天与客户进行了首次沟通，但对方暂时没有说明具体需求和应用场景。")
    with pytest.raises(ConversationError):
        service.continue_with(state, {"not_a_current_question": "任意回答"})


def test_save_requires_feishu_to_be_enabled(service):
    state = service.start("客户明确希望统一管理销售线索，避免销售人员重复录入客户资料。")
    analysis = OpportunityAnalysis.model_validate(state["analysis"])
    assert analysis.agent_control.decision == AgentDecision.READY_TO_SAVE
    with pytest.raises(ToolUnavailableError):
        service.confirm_save(state)
