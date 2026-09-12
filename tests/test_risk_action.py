from agent.nodes.risk_action import analyze_risks_and_actions, load_risk_action_rules
from agent.schemas import Evidence, EvidenceBackedFact, FactStatus, OpportunityFacts


def confirmed_list(*values: str) -> EvidenceBackedFact[list[str]]:
    return EvidenceBackedFact[list[str]](
        status=FactStatus.CONFIRMED,
        value=list(values),
        evidence=[Evidence(quote=value) for value in values],
    )


def contradictory_text(first: str, second: str) -> EvidenceBackedFact[str]:
    return EvidenceBackedFact[str](
        status=FactStatus.CONTRADICTORY,
        conflicting_values=[first, second],
        evidence=[Evidence(quote=first), Evidence(quote=second)],
    )


def run(facts: OpportunityFacts, stage: str = "S1") -> dict:
    return analyze_risks_and_actions(
        {
            "facts": facts.model_dump(mode="json"),
            "stage": stage,
            "stage_evidence": [],
        }
    )


def test_rule_file_defines_actions_for_every_stage():
    config = load_risk_action_rules()
    assert config["version"] == "v1.0"
    assert set(config["stage_actions"]) == {"S0", "S1", "S2", "S3", "S4", "S5"}


def test_every_risk_has_evidence_and_every_action_is_executable():
    result = run(OpportunityFacts(), "S0")
    assert result["risks"]
    assert all(risk["evidence"].strip() for risk in result["risks"])
    assert all(action["owner"].strip() for action in result["next_actions"])
    assert all(action["due_time"] != "待确认" for action in result["next_actions"])
    assert all("继续沟通" not in action["action"] for action in result["next_actions"])


def test_engine_covers_all_required_risk_categories():
    s1 = run(
        OpportunityFacts(customer_needs=confirmed_list("希望提升业务处理效率")),
        "S1",
    )
    conflict = run(
        OpportunityFacts(
            customer_needs=confirmed_list("希望统一销售线索管理"),
            budget=contradictory_text("预算二十万元", "预算尚未批准"),
            timeline=contradictory_text("计划下月上线", "上线时间不确定"),
        ),
        "S3",
    )
    risk_types = {item["type"] for item in s1["risks"] + conflict["risks"]}
    assert {
        "预算未确认",
        "决策链不清晰",
        "时间计划缺失",
        "需求价值不足",
        "客户态度模糊",
        "商务条件冲突",
        "前后信息矛盾",
    } <= risk_types


def test_high_risks_are_sorted_before_medium_and_low():
    result = run(
        OpportunityFacts(
            customer_needs=confirmed_list("希望统一销售线索管理"),
            budget=contradictory_text("预算二十万元", "预算尚未批准"),
        ),
        "S3",
    )
    order = {"高": 0, "中": 1, "低": 2}
    levels = [order[item["level"]] for item in result["risks"]]
    assert levels == sorted(levels)


def test_stage_action_changes_with_current_stage():
    facts = OpportunityFacts(contract_or_order=confirmed_list("合同已签"))
    result = run(facts, "S5")
    assert any("项目交接" in action["action"] for action in result["next_actions"])
