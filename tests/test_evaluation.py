from evals.runner import EvalResult, build_report, evaluate_case, load_cases


def result(**overrides) -> EvalResult:
    values = {
        "case_id": "EVAL-UNIT-001",
        "category": "单元测试",
        "input_text": "客户首次拜访，仅交换资料。",
        "expected_stage": "S0",
        "actual_stage": "S0",
        "schema_pass": True,
        "stage_pass": True,
        "evidence_pass": True,
        "hallucination_pass": True,
        "fact_pass": True,
        "unconfirmed_pass": True,
        "decision_pass": True,
        "input_guard_pass": True,
        "passed": True,
        "latency_ms": 100,
        "model_version": "heuristic-v1",
        "stage_rule_version": "v1.0",
        "risk_rule_version": "v1.0",
        "failures": [],
    }
    values.update(overrides)
    return EvalResult(**values)


def test_dataset_covers_required_stage_and_robustness_categories():
    cases = load_cases()
    assert len(cases) >= 15
    assert {case.expected_stage for case in cases if case.expected_stage} == {
        "S0",
        "S1",
        "S2",
        "S3",
        "S4",
        "S5",
    }
    categories = {case.category for case in cases}
    for required in {
        "信息缺失",
        "信息矛盾",
        "模糊表达",
        "否定表达",
        "恶意提示词",
        "超长输入",
        "完整字段抽取",
        "中英混合",
        "口语噪声",
        "第三方归属",
    }:
        assert required in categories

    fact_fields = {
        "customer_name",
        "customer_needs",
        "core_scenarios",
        "budget",
        "decision_makers",
        "influencers",
        "timeline",
        "validation_commitments",
        "commercial_discussions",
        "decision_progress",
        "contract_or_order",
    }
    for case in cases:
        asserted = (
            set(case.required_confirmed)
            | set(case.required_unconfirmed_fields)
            | set(case.required_contradictory)
            | set(case.value_contains)
            | set(case.forbidden_confirmed)
        )
        assert asserted <= fact_fields


def test_report_computes_quality_targets():
    report = build_report([result(), result(case_id="EVAL-UNIT-002")], mode="mock", batch_id="batch")
    assert report.pass_rate == 1
    assert report.stage_accuracy == 1
    assert report.hallucination_rate == 0
    assert all(report.targets_met.values())


def test_report_excludes_input_rejections_from_model_quality_metrics():
    rejected = result(
        case_id="EVAL-UNIT-INPUT",
        expected_stage="输入拒绝",
        actual_stage="输入拒绝",
        schema_pass=True,
        latency_ms=1,
    )
    generated = result(latency_ms=1000)
    report = build_report([rejected, generated], mode="mock", batch_id="batch")
    assert report.output_count == 1
    assert report.schema_pass_rate == 1
    assert report.stage_accuracy == 1
    assert report.average_latency_ms == 1000


def test_report_separates_infrastructure_failures_from_model_quality_metrics():
    failed = result(
        case_id="EVAL-UNIT-INFRA",
        actual_stage="执行失败",
        schema_pass=False,
        stage_pass=False,
        evidence_pass=False,
        hallucination_pass=False,
        fact_pass=False,
        unconfirmed_pass=False,
        decision_pass=False,
        input_guard_pass=False,
        passed=False,
        latency_ms=30_000,
        failures=["RateLimitError: 429"],
    )
    report = build_report([result(latency_ms=1000), failed], mode="live", batch_id="batch")

    assert report.output_count == 1
    assert report.execution_failure_count == 1
    assert report.schema_pass_rate == 1
    assert report.stage_accuracy == 1
    assert report.average_latency_ms == 1000
    assert not report.targets_met["execution_failures_zero"]


def test_mock_fallback_resists_prompt_injection(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "true")
    monkeypatch.setenv("FEISHU_SYNC_ENABLED", "false")
    case = next(item for item in load_cases() if item.id == "EVAL-INJECTION-002")
    evaluated = evaluate_case(case)
    assert evaluated.passed
    assert evaluated.actual_stage == "S0"
    assert evaluated.hallucination_pass
