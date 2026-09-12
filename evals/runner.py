from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from agent.prompts import PROMPT_VERSION
from agent.schemas import AgentDecision, OpportunityAnalysis, StageCode
from agent.service import OpportunityAgentService
from integrations.feishu_bitable import FeishuBitableClient, FeishuAPIError


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES_PATH = ROOT / "evals" / "cases.json"
DEFAULT_OUTPUT_DIR = ROOT / "evals" / "results"

EVAL_EXTRA_FIELDS = {
    "类别": 1,
    "Schema通过": 7,
    "未确认正确": 7,
    "模型版本": 1,
    "Prompt版本": 1,
    "阶段规则版本": 1,
    "风险规则版本": 1,
    "响应时间ms": 2,
    "失败原因": 1,
    "评测批次": 1,
}


class EvalCase(BaseModel):
    id: str
    category: str
    note: str
    repeat: int = Field(default=1, ge=1)
    expected_stage: StageCode | None = None
    expect_error: bool = False
    required_unconfirmed: list[str] = Field(default_factory=list)
    forbidden_confirmed: list[str] = Field(default_factory=list)
    expected_decision: AgentDecision | None = None

    @property
    def input_text(self) -> str:
        return self.note * self.repeat


class EvalResult(BaseModel):
    case_id: str
    category: str
    input_text: str
    expected_stage: str
    actual_stage: str
    schema_pass: bool
    stage_pass: bool
    evidence_pass: bool
    hallucination_pass: bool
    unconfirmed_pass: bool
    decision_pass: bool
    input_guard_pass: bool
    passed: bool
    latency_ms: int
    model_version: str
    prompt_version: str = PROMPT_VERSION
    stage_rule_version: str
    risk_rule_version: str
    failures: list[str] = Field(default_factory=list)


class EvalReport(BaseModel):
    batch_id: str
    mode: str
    case_count: int
    output_count: int
    passed_count: int
    pass_rate: float
    schema_pass_rate: float
    stage_accuracy: float
    evidence_pass_rate: float
    hallucination_rate: float
    average_latency_ms: int
    p95_latency_ms: int
    targets_met: dict[str, bool]
    results: list[EvalResult]


def load_cases(path: Path = DEFAULT_CASES_PATH) -> list[EvalCase]:
    return [EvalCase.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]


def _evidence_is_grounded(analysis: OpportunityAnalysis, source: str) -> bool:
    for field_name in type(analysis.facts).model_fields:
        fact = getattr(analysis.facts, field_name)
        for evidence in fact.evidence:
            if evidence.source == "visit_note" and evidence.quote not in source:
                return False
    return all(item.quote in source for item in analysis.stage_evidence)


def evaluate_case(case: EvalCase) -> EvalResult:
    started = time.perf_counter()
    failures: list[str] = []
    state: dict[str, Any] = {}
    analysis: OpportunityAnalysis | None = None
    schema_pass = False
    try:
        state = OpportunityAgentService().start(case.input_text)
        if not state.get("errors") and state.get("analysis"):
            analysis = OpportunityAnalysis.model_validate(state["analysis"])
            schema_pass = True
    except Exception as exc:
        failures.append(f"执行异常：{type(exc).__name__}: {str(exc)[:300]}")

    has_error = bool(state.get("errors")) or analysis is None
    input_guard_pass = has_error == case.expect_error
    if not input_guard_pass:
        failures.append("输入校验结果与预期不一致")

    if case.expect_error:
        stage_pass = evidence_pass = hallucination_pass = unconfirmed_pass = decision_pass = True
        actual_stage = "输入拒绝" if has_error else (analysis.stage if analysis else "执行失败")
        model_version = "input_guard"
        stage_rule_version = risk_rule_version = "未执行"
    elif analysis is None:
        stage_pass = evidence_pass = hallucination_pass = unconfirmed_pass = decision_pass = False
        actual_stage = "执行失败"
        model_version = stage_rule_version = risk_rule_version = "未知"
        failures.append("未生成可校验的 OpportunityAnalysis")
    else:
        actual_stage = analysis.stage
        stage_pass = case.expected_stage is None or analysis.stage == case.expected_stage
        if not stage_pass:
            failures.append(f"阶段期望 {case.expected_stage}，实际 {analysis.stage}")
        evidence_pass = _evidence_is_grounded(analysis, case.input_text)
        if not evidence_pass:
            failures.append("存在无法在原文定位的证据")
        hallucination_pass = evidence_pass and all(
            not getattr(analysis.facts, field_name).is_confirmed
            for field_name in case.forbidden_confirmed
        )
        if not hallucination_pass:
            failures.append("禁止确认的字段被确认为事实或证据越界")
        actual_unconfirmed = set(analysis.unconfirmed_information)
        unconfirmed_pass = set(case.required_unconfirmed).issubset(actual_unconfirmed)
        if not unconfirmed_pass:
            missing = sorted(set(case.required_unconfirmed) - actual_unconfirmed)
            failures.append("未正确标记未确认信息：" + "、".join(missing))
        actual_decision = analysis.agent_control.decision if analysis.agent_control else None
        decision_pass = case.expected_decision is None or actual_decision == case.expected_decision
        if not decision_pass:
            failures.append(f"Controller 期望 {case.expected_decision}，实际 {actual_decision}")
        metadata = analysis.model_metadata
        model_version = metadata.model if metadata else "未知"
        stage_rule_version = analysis.rule_version
        risk_rule_version = analysis.risk_rule_version

    latency_ms = max(0, int((time.perf_counter() - started) * 1000))
    passed = all(
        [
            input_guard_pass,
            schema_pass if not case.expect_error else True,
            stage_pass,
            evidence_pass,
            hallucination_pass,
            unconfirmed_pass,
            decision_pass,
        ]
    )
    return EvalResult(
        case_id=case.id,
        category=case.category,
        input_text=case.input_text,
        expected_stage=case.expected_stage or ("输入拒绝" if case.expect_error else "不限定"),
        actual_stage=actual_stage,
        schema_pass=schema_pass if not case.expect_error else input_guard_pass,
        stage_pass=stage_pass,
        evidence_pass=evidence_pass,
        hallucination_pass=hallucination_pass,
        unconfirmed_pass=unconfirmed_pass,
        decision_pass=decision_pass,
        input_guard_pass=input_guard_pass,
        passed=passed,
        latency_ms=latency_ms,
        model_version=model_version,
        stage_rule_version=stage_rule_version,
        risk_rule_version=risk_rule_version,
        failures=failures,
    )


def _rate(values: list[bool]) -> float:
    return sum(values) / len(values) if values else 1.0


def build_report(results: list[EvalResult], *, mode: str, batch_id: str) -> EvalReport:
    output_results = [item for item in results if item.actual_stage != "输入拒绝"]
    stage_results = [item for item in results if item.expected_stage.startswith("S")]
    latencies = sorted(item.latency_ms for item in output_results)
    p95_index = max(0, math.ceil(len(latencies) * 0.95) - 1)
    schema_rate = _rate([item.schema_pass for item in output_results])
    stage_accuracy = _rate([item.stage_pass for item in stage_results])
    evidence_rate = _rate([item.evidence_pass for item in output_results])
    hallucination_rate = 1 - _rate([item.hallucination_pass for item in output_results])
    return EvalReport(
        batch_id=batch_id,
        mode=mode,
        case_count=len(results),
        output_count=len(output_results),
        passed_count=sum(item.passed for item in results),
        pass_rate=_rate([item.passed for item in results]),
        schema_pass_rate=schema_rate,
        stage_accuracy=stage_accuracy,
        evidence_pass_rate=evidence_rate,
        hallucination_rate=hallucination_rate,
        average_latency_ms=int(statistics.mean(latencies)) if latencies else 0,
        p95_latency_ms=latencies[p95_index] if latencies else 0,
        targets_met={
            "schema_100_percent": schema_rate == 1.0,
            "stage_accuracy_at_least_90_percent": stage_accuracy >= 0.9,
            "hallucination_rate_zero": hallucination_rate == 0,
            "average_latency_below_15_seconds": bool(latencies) and statistics.mean(latencies) < 15_000,
        },
        results=results,
    )


def run_evaluation(
    *,
    mode: str,
    workers: int = 1,
    case_ids: set[str] | None = None,
) -> EvalReport:
    os.environ["MOCK_MODE"] = "true" if mode == "mock" else "false"
    os.environ["FEISHU_SYNC_ENABLED"] = "false"
    cases = load_cases()
    if case_ids:
        cases = [case for case in cases if case.id in case_ids]
        missing = sorted(case_ids - {case.id for case in cases})
        if missing:
            raise ValueError("未找到评测用例：" + "、".join(missing))
    batch_id = datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{mode}"
    if workers <= 1:
        results = [evaluate_case(case) for case in cases]
    else:
        by_id: dict[str, EvalResult] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(evaluate_case, case): case.id for case in cases}
            for future in as_completed(futures):
                by_id[futures[future]] = future.result()
        results = [by_id[case.id] for case in cases]
    return build_report(results, mode=mode, batch_id=batch_id)


def save_report(report: EvalReport, output_dir: Path = DEFAULT_OUTPUT_DIR) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{report.batch_id}.json"
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return path


def sync_report_to_feishu(report: EvalReport) -> list[str]:
    os.environ["FEISHU_SYNC_ENABLED"] = "true"
    client = FeishuBitableClient.from_env()
    if not client.eval_table_id:
        raise FeishuAPIError("同步评测结果", "缺少 FEISHU_EVAL_TABLE_ID")
    client.ensure_fields(table_id=client.eval_table_id, fields=EVAL_EXTRA_FIELDS)
    record_ids: list[str] = []
    for item in report.results:
        display_input = item.input_text
        if len(display_input) > 3000:
            display_input = display_input[:3000] + f"\n……（原始长度 {len(item.input_text)} 字）"
        fields = {
            "用例编号": item.case_id,
            "类别": item.category,
            "测试输入": display_input,
            "预期阶段": item.expected_stage,
            "实际阶段": item.actual_stage,
            "是否编造": not item.hallucination_pass,
            "是否保留证据": item.evidence_pass,
            "Schema通过": item.schema_pass,
            "未确认正确": item.unconfirmed_pass,
            "是否通过": item.passed,
            "模型版本": item.model_version,
            "Prompt版本": item.prompt_version,
            "阶段规则版本": item.stage_rule_version,
            "风险规则版本": item.risk_rule_version,
            "响应时间ms": item.latency_ms,
            "失败原因": "；".join(item.failures) if item.failures else "无",
            "评测批次": report.batch_id,
        }
        existing = client.search_records(
            table_id=client.eval_table_id,
            field_name="用例编号",
            value=item.case_id,
            page_size=20,
        )
        if existing and existing[0].get("record_id"):
            record_id = client.update_record(
                table_id=client.eval_table_id,
                record_id=str(existing[0]["record_id"]),
                fields=fields,
            )
        else:
            record_id = client.create_record(table_id=client.eval_table_id, fields=fields)
        record_ids.append(record_id)
    return record_ids


def main() -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="运行销售商机 Agent 自动评测")
    parser.add_argument("--mode", choices=["mock", "live"], default="mock")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--sync-feishu", action="store_true")
    args = parser.parse_args()

    report = run_evaluation(
        mode=args.mode,
        workers=max(1, args.workers),
        case_ids=set(args.case_ids) if args.case_ids else None,
    )
    path = save_report(report)
    print(f"batch_id={report.batch_id}")
    print(f"cases={report.case_count}")
    print(f"passed={report.passed_count}")
    print(f"pass_rate={report.pass_rate:.1%}")
    print(f"schema_pass_rate={report.schema_pass_rate:.1%}")
    print(f"stage_accuracy={report.stage_accuracy:.1%}")
    print(f"evidence_pass_rate={report.evidence_pass_rate:.1%}")
    print(f"hallucination_rate={report.hallucination_rate:.1%}")
    print(f"average_latency_ms={report.average_latency_ms}")
    print(f"p95_latency_ms={report.p95_latency_ms}")
    print(f"report={path}")
    failed = [item.case_id for item in report.results if not item.passed]
    print("failed=" + (",".join(failed) if failed else "none"))
    if args.sync_feishu:
        print(f"feishu_records={len(sync_report_to_feishu(report))}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
