from __future__ import annotations

import json
import hashlib
from pathlib import Path

from agent.schemas import Evidence, OpportunityFacts, StageAssessment, StageCode, StageRule
from agent.state import AgentState


RULE_PATH = Path(__file__).resolve().parents[2] / "rules" / "stage_rules.json"

SIGNAL_LABELS = {
    "customer_needs": "明确的客户业务问题",
    "core_scenarios": "明确的使用场景",
    "validation_commitments": "客户明确同意演示、试用、技术交流或方案评估",
    "commercial_discussions": "已确认的预算、报价、采购流程或合同条款讨论",
    "decision_progress": "已进入内部立项、审批或供应商决策",
    "contract_or_order": "已签合同或已确认正式订单",
}


def load_stage_rules(path: Path = RULE_PATH) -> list[StageRule]:
    rules = [StageRule.model_validate(row) for row in json.loads(path.read_text(encoding="utf-8"))]
    return validate_stage_rules(rules)


def validate_stage_rules(rules: list[StageRule]) -> list[StageRule]:
    stages = [rule.stage for rule in rules]
    ranks = [rule.rank for rule in rules]
    if set(stages) != {"S0", "S1", "S2", "S3", "S4", "S5"}:
        raise ValueError("stage rules must define every stage from S0 to S5 exactly once")
    if len(set(stages)) != len(stages):
        raise ValueError("stage rules contain duplicate stage codes")
    if len(set(ranks)) != len(ranks):
        raise ValueError("stage rules contain duplicate ranks")
    if sum(rule.fallback for rule in rules) != 1:
        raise ValueError("stage rules must contain exactly one fallback rule")
    return sorted(rules, key=lambda rule: rule.rank)


def load_effective_stage_rules() -> tuple[list[StageRule], str, str | None]:
    local_rules = load_stage_rules()
    from integrations.feishu_bitable import FeishuBitableClient

    client = FeishuBitableClient.from_env()
    if not client.enabled or not client.stage_rules_table_id:
        return local_rules, "local_file", None
    try:
        return validate_stage_rules(client.load_stage_rules()), "feishu_bitable", None
    except Exception as exc:
        return local_rules, "local_fallback", f"飞书阶段规则读取失败，已使用本地规则：{exc}"


def _signal_is_confirmed(facts: OpportunityFacts, signal: str) -> bool:
    fact = getattr(facts, signal)
    return fact.is_confirmed


def _rule_matches(facts: OpportunityFacts, rule: StageRule) -> bool:
    if rule.fallback:
        return False
    return all(
        any(_signal_is_confirmed(facts, signal) for signal in requirement.any_of)
        for requirement in rule.requirements
    )


def _collect_rule_evidence(facts: OpportunityFacts, rule: StageRule) -> list[Evidence]:
    evidence: list[Evidence] = []
    seen: set[tuple[str, str]] = set()
    for requirement in rule.requirements:
        for signal in requirement.any_of:
            fact = getattr(facts, signal)
            if not fact.is_confirmed:
                continue
            for item in fact.evidence:
                key = (item.source, item.quote)
                if key not in seen:
                    evidence.append(item)
                    seen.add(key)
    return evidence


def determine_stage(
    facts: OpportunityFacts,
    rules: list[StageRule] | None = None,
) -> tuple[str, list[Evidence]]:
    rules = rules or load_stage_rules()
    fallback = next(rule for rule in rules if rule.fallback)
    matched = [rule for rule in rules if _rule_matches(facts, rule)]
    selected = max(matched, key=lambda rule: rule.rank, default=fallback)
    if selected.fallback:
        return selected.stage, []
    return selected.stage, _collect_rule_evidence(facts, selected)


def _requirement_gap(requirement) -> str:
    return " 或 ".join(SIGNAL_LABELS[signal] for signal in requirement.any_of)


def assess_stage(
    facts: OpportunityFacts,
    rules: list[StageRule] | None = None,
    previous_stage: str | None = None,
    rule_source: str = "local_file",
    rule_warning: str | None = None,
) -> tuple[StageAssessment, list[Evidence]]:
    rules = rules or load_stage_rules()
    stage, evidence = determine_stage(facts, rules)
    selected = next(rule for rule in rules if rule.stage == stage)
    matched = [rule.stage for rule in rules if _rule_matches(facts, rule)]
    if not matched:
        matched = ["S0"]

    next_rule = next((rule for rule in rules if rule.rank == selected.rank + 1), None)
    gaps: list[str] = []
    if next_rule:
        gaps = [
            _requirement_gap(requirement)
            for requirement in next_rule.requirements
            if not any(_signal_is_confirmed(facts, signal) for signal in requirement.any_of)
        ]
        next_reason = (
            f"尚未进入 {next_rule.stage}（{next_rule.name}），缺少：{'；'.join(gaps)}。"
            if gaps
            else f"已具备 {next_rule.stage} 的规则信号，但当前存在更高或并行规则结果，请复核。"
        )
    else:
        next_reason = "已达到最高阶段 S5（赢单/签约），没有后续阶段。"

    ranks = {rule.stage: rule.rank for rule in rules}
    regressed_from: StageCode | None = None
    regression_reason: str | None = None
    if previous_stage in ranks and ranks[previous_stage] > selected.rank:
        regressed_from = previous_stage  # type: ignore[assignment]
        regression_reason = (
            f"重新评估结果由 {previous_stage} 降至 {stage}；原高阶段信号已缺失、未确认或发生矛盾，"
            "系统未沿用旧阶段，需人工复核。"
        )

    assessment = StageAssessment(
        current_stage=stage,
        current_stage_name=selected.name,
        matched_stages=matched,
        next_stage=next_rule.stage if next_rule else None,
        next_stage_name=next_rule.name if next_rule else None,
        next_stage_reason=next_reason,
        next_stage_gaps=gaps,
        regressed_from=regressed_from,
        regression_reason=regression_reason,
        rule_source=rule_source,
        rule_warning=rule_warning,
    )
    return assessment, evidence


def stage_rule_engine(state: AgentState) -> AgentState:
    facts = OpportunityFacts.model_validate(state["facts"])
    rules, source, warning = load_effective_stage_rules()
    assessment, evidence = assess_stage(
        facts,
        rules,
        state.get("previous_stage"),
        rule_source=source,
        rule_warning=warning,
    )
    rule_version = "v1.0"
    if source == "feishu_bitable":
        canonical = json.dumps(
            [rule.model_dump(mode="json") for rule in rules],
            ensure_ascii=False,
            sort_keys=True,
        )
        rule_version = "feishu-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:10]
    return {
        "stage": assessment.current_stage,
        "stage_name": assessment.current_stage_name,
        "stage_evidence": [item.model_dump() for item in evidence],
        "stage_assessment": assessment.model_dump(mode="json"),
        "rule_version": rule_version,
    }
