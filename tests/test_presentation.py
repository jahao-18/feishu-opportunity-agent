import json

from agent.presentation import analysis_as_json, build_agent_trace
from agent.schemas import OpportunityAnalysis, OpportunityFacts


def analysis(**overrides) -> OpportunityAnalysis:
    values = {
        "request_id": "ui-001",
        "original_note": "用于页面展示测试的客户拜访记录。",
        "facts": OpportunityFacts(),
        "stage": "S0",
        "stage_name": "线索",
        "stage_evidence": [],
        "risks": [],
        "next_actions": [],
        "unconfirmed_information": [],
        "confidence": 0.45,
    }
    values.update(overrides)
    return OpportunityAnalysis(**values)


def test_trace_contains_core_agent_steps_and_pending_sync():
    trace = build_agent_trace(analysis())
    labels = [item.label for item in trace]
    assert labels[:5] == [
        "读取拜访记录",
        "提取并校验事实",
        "加载阶段规则",
        "执行确定性判级",
        "生成风险与行动",
    ]
    assert trace[-1].label == "同步飞书"
    assert trace[-1].status == "pending"


def test_trace_reports_saved_record():
    trace = build_agent_trace(analysis(feishu_record_id="rec-ui"))
    assert trace[-1].status == "complete"
    assert "rec-ui" in trace[-1].detail


def test_analysis_export_is_valid_utf8_json():
    payload = json.loads(analysis_as_json(analysis(stage_name="线索")))
    assert payload["request_id"] == "ui-001"
    assert payload["stage_name"] == "线索"
