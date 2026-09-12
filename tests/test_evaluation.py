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
    for required in {"信息缺失", "信息矛盾", "模糊表达", "否定表达", "恶意提示词", "超长输入"}:
        assert required in categories


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


def test_mock_fallback_resists_prompt_injection(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "true")
    monkeypatch.setenv("FEISHU_SYNC_ENABLED", "false")
    case = next(item for item in load_cases() if item.id == "EVAL-INJECTION-002")
    evaluated = evaluate_case(case)
    assert evaluated.passed
    assert evaluated.actual_stage == "S0"
    assert evaluated.hallucination_pass
