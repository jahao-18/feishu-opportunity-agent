from agent.nodes.stage_engine import (
    assess_stage,
    determine_stage,
    load_effective_stage_rules,
    load_stage_rules,
)
from agent.schemas import Evidence, EvidenceBackedFact, FactStatus, OpportunityFacts


def confirmed_list(*values: str) -> EvidenceBackedFact[list[str]]:
    return EvidenceBackedFact[list[str]](
        status=FactStatus.CONFIRMED,
        value=list(values),
        evidence=[Evidence(quote=value) for value in values],
    )


def test_rule_file_contains_valid_s0_to_s5():
    rules = load_stage_rules()
    assert [rule.stage for rule in rules] == ["S0", "S1", "S2", "S3", "S4", "S5"]
    assert [rule.rank for rule in rules] == list(range(6))


def test_s0_when_no_need_or_scene():
    stage, _ = determine_stage(OpportunityFacts())
    assert stage == "S0"


def test_s1_when_need_is_explicit():
    facts = OpportunityFacts(customer_needs=confirmed_list("希望统一管理销售线索"))
    stage, evidence = determine_stage(facts)
    assert stage == "S1"
    assert evidence[0].quote == "希望统一管理销售线索"


def test_s2_requires_explicit_validation_agreement():
    facts = OpportunityFacts(
        customer_needs=confirmed_list("巡检整改困难"),
        validation_commitments=confirmed_list("客户同意下周安排产品演示"),
    )
    stage, evidence = determine_stage(facts)
    assert stage == "S2"
    assert evidence[0].quote == "客户同意下周安排产品演示"


def test_s3_requires_need_and_commercial_topic():
    facts = OpportunityFacts(
        customer_needs=confirmed_list("希望统一客户管理"),
        commercial_discussions=confirmed_list("会上讨论了预算"),
    )
    stage, evidence = determine_stage(facts)
    assert stage == "S3"
    assert {item.quote for item in evidence} == {"希望统一客户管理", "会上讨论了预算"}


def test_commercial_topic_without_active_need_does_not_reach_s3():
    facts = OpportunityFacts(commercial_discussions=confirmed_list("销售介绍了报价"))
    stage, _ = determine_stage(facts)
    assert stage == "S0"


def test_unconfirmed_budget_does_not_trigger_s3():
    facts = OpportunityFacts(
        customer_needs=confirmed_list("需要统一客户管理"),
        budget=EvidenceBackedFact[str](
            status=FactStatus.UNCONFIRMED,
            evidence=[Evidence(quote="预算尚未确认")],
        ),
    )
    stage, _ = determine_stage(facts)
    assert stage == "S1"


def test_s4_when_decision_process_is_explicit():
    facts = OpportunityFacts(decision_progress=confirmed_list("项目已经提交内部审批"))
    stage, _ = determine_stage(facts)
    assert stage == "S4"


def test_highest_stage_wins():
    facts = OpportunityFacts(
        customer_needs=confirmed_list("需要 CRM"),
        validation_commitments=confirmed_list("已同意试用"),
        commercial_discussions=confirmed_list("讨论了报价"),
        decision_progress=confirmed_list("已经提交内部审批"),
        contract_or_order=confirmed_list("合同已签"),
    )
    stage, _ = determine_stage(facts)
    assert stage == "S5"


def test_assessment_explains_next_stage_gap():
    facts = OpportunityFacts(customer_needs=confirmed_list("希望统一管理销售线索"))
    assessment, evidence = assess_stage(facts)
    assert assessment.current_stage == "S1"
    assert assessment.next_stage == "S2"
    assert assessment.next_stage_gaps == ["客户明确同意演示、试用、技术交流或方案评估"]
    assert "缺少" in assessment.next_stage_reason
    assert [item.quote for item in evidence] == ["希望统一管理销售线索"]


def test_assessment_reports_stage_regression_instead_of_silently_preserving_it():
    facts = OpportunityFacts(customer_needs=confirmed_list("希望统一管理销售线索"))
    assessment, _ = assess_stage(facts, previous_stage="S3")
    assert assessment.current_stage == "S1"
    assert assessment.regressed_from == "S3"
    assert "降至 S1" in assessment.regression_reason


def test_s5_has_no_next_stage():
    facts = OpportunityFacts(contract_or_order=confirmed_list("合同已签"))
    assessment, evidence = assess_stage(facts)
    assert assessment.current_stage == "S5"
    assert assessment.next_stage is None
    assert assessment.next_stage_gaps == []
    assert evidence[0].quote == "合同已签"


def test_rule_engine_is_deterministic_for_same_facts():
    facts = OpportunityFacts(
        customer_needs=confirmed_list("需要统一客户管理"),
        commercial_discussions=confirmed_list("讨论了报价"),
    )
    first = assess_stage(facts)
    second = assess_stage(facts)
    assert first == second


def test_feishu_rule_failure_falls_back_to_local_rules(monkeypatch):
    class FailingClient:
        enabled = True
        stage_rules_table_id = "tbl_rules"

        def load_stage_rules(self):
            raise RuntimeError("temporary Feishu failure")

    monkeypatch.setattr(
        "integrations.feishu_bitable.FeishuBitableClient.from_env",
        classmethod(lambda cls: FailingClient()),
    )
    rules, source, warning = load_effective_stage_rules()
    assert len(rules) == 6
    assert source == "local_fallback"
    assert "已使用本地规则" in warning
