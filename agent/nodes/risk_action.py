from __future__ import annotations

import json
from pathlib import Path

from agent.schemas import FactStatus, NextAction, OpportunityFacts, RiskItem
from agent.state import AgentState


RULE_PATH = Path(__file__).resolve().parents[2] / "rules" / "risk_action_rules.json"
LEVEL_ORDER = {"高": 0, "中": 1, "低": 2}


def load_risk_action_rules(path: Path = RULE_PATH) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    if not config.get("version"):
        raise ValueError("risk/action rules must contain a version")
    if set(config.get("stage_actions", {})) != {"S0", "S1", "S2", "S3", "S4", "S5"}:
        raise ValueError("risk/action rules must define one action for every S0-S5 stage")
    return config


def _fact_basis(label: str, fact) -> str:
    quotes = [item.quote for item in fact.evidence]
    if quotes:
        return f"{label}字段原文：{'；'.join(quotes)}"
    return f"{label}字段状态：{fact.status.value}，没有可确认的原文证据"


def _action(payload: dict) -> NextAction:
    return NextAction.model_validate(payload)


def analyze_risks_and_actions(state: AgentState) -> AgentState:
    facts = OpportunityFacts.model_validate(state["facts"])
    config = load_risk_action_rules()
    stage = state["stage"]
    risks: list[RiskItem] = []
    actions: list[NextAction] = []

    def add_risk(
        risk_type: str,
        level: str,
        description: str,
        evidence: str,
        action: dict,
    ) -> None:
        risks.append(
            RiskItem(type=risk_type, level=level, description=description, evidence=evidence)
        )
        actions.append(_action(action))

    budget_level = "高" if stage in {"S3", "S4"} else "中"
    if not facts.budget.is_confirmed:
        add_risk(
            "预算未确认",
            budget_level,
            "尚无可用于判断采购可行性的明确预算范围或资金来源。",
            _fact_basis("预算", facts.budget),
            {
                "priority": "P0" if budget_level == "高" else "P1",
                "action": "向客户确认预算范围、资金来源及预算审批状态，并记录客户原话",
                "owner": "销售负责人",
                "due_time": "下次客户沟通前",
                "reason": "判断商务可行性并避免将未确认预算当作 S3 证据",
            },
        )

    decision_level = "高" if stage in {"S2", "S3", "S4"} else "中"
    if not facts.decision_makers.is_confirmed:
        add_risk(
            "决策链不清晰",
            decision_level,
            "尚未确认最终采购决策人、关键影响人或审批路径。",
            _fact_basis("决策人", facts.decision_makers),
            {
                "priority": "P0" if decision_level == "高" else "P1",
                "action": "绘制客户决策链，确认最终决策人、影响人和每一层审批节点",
                "owner": "销售负责人",
                "due_time": "下一次方案沟通前",
                "reason": "避免方案长期停留在无决策权的联系人处",
            },
        )

    timeline_level = "中" if stage in {"S1", "S2", "S3", "S4"} else "低"
    if not facts.timeline.is_confirmed:
        add_risk(
            "时间计划缺失",
            timeline_level,
            "缺少下一次沟通、采购决策或上线时间，推进节奏不可验证。",
            _fact_basis("时间计划", facts.timeline),
            {
                "priority": "P1" if timeline_level == "中" else "P2",
                "action": "与客户约定下一次沟通日期，并确认采购决策和计划上线时间",
                "owner": "销售负责人",
                "due_time": "本次沟通结束后 2 个工作日内",
                "reason": "形成有明确时间点的推进计划",
            },
        )

    has_need = facts.customer_needs.is_confirmed
    has_scene = facts.core_scenarios.is_confirmed
    if not has_need and not has_scene:
        add_risk(
            "需求价值不足",
            "高",
            "当前没有明确业务问题或使用场景，无法证明商机价值。",
            f"客户需求：{facts.customer_needs.status.value}；核心场景：{facts.core_scenarios.status.value}",
            {
                "priority": "P0",
                "action": "访谈客户当前流程、低效环节和影响范围，记录至少一个具体业务场景",
                "owner": "销售负责人",
                "due_time": "首次需求访谈内",
                "reason": "获得进入 S1 所必需的需求或场景证据",
            },
        )
    elif not has_scene:
        add_risk(
            "需求价值不足",
            "中",
            "已记录客户需求，但缺少具体业务场景和影响范围。",
            _fact_basis("客户需求", facts.customer_needs),
            {
                "priority": "P1",
                "action": "让客户描述需求发生的具体流程、涉及角色、频率和当前损失",
                "owner": "售前顾问",
                "due_time": "方案设计前",
                "reason": "把抽象需求转化为可演示、可验证的业务场景",
            },
        )

    if stage in {"S0", "S1"} and not facts.validation_commitments.is_confirmed:
        add_risk(
            "客户态度模糊",
            "中",
            "客户尚未明确承诺演示、试用、技术交流或方案评估。",
            _fact_basis("方案验证承诺", facts.validation_commitments),
            {
                "priority": "P1",
                "action": "向客户提出一个带日期和参与人的演示或试用邀请，并记录明确答复",
                "owner": "销售负责人",
                "due_time": "3 个工作日内",
                "reason": "用可观察的客户承诺验证真实推进意愿",
            },
        )

    commercial_conflicts = [
        (label, fact)
        for label, fact in [
            ("预算", facts.budget),
            ("商务讨论", facts.commercial_discussions),
            ("时间计划", facts.timeline),
            ("合同或订单", facts.contract_or_order),
        ]
        if fact.status == FactStatus.CONTRADICTORY
    ]
    if commercial_conflicts:
        conflict_evidence = "；".join(
            f"{label}：{' / '.join(fact.conflicting_values)}"
            for label, fact in commercial_conflicts
        )
        add_risk(
            "商务条件冲突",
            "高",
            "预算、时间或合同相关表述存在冲突，不能据此推进商务阶段。",
            conflict_evidence,
            {
                "priority": "P0",
                "action": "与客户逐项确认预算、时间和合同条件，以书面结论更新商机记录",
                "owner": "销售负责人",
                "due_time": "继续报价或提交审批前",
                "reason": "避免使用相互冲突的商务条件作出阶段判断",
            },
        )

    contradictory = facts.contradictory_fields()
    if contradictory:
        contradiction_text = "；".join(
            f"{label}：{' / '.join(fact.conflicting_values)}" for label, fact in contradictory
        )
        add_risk(
            "前后信息矛盾",
            "高",
            "客户记录中存在相互冲突的信息，Agent 未自行选择其中一种。",
            contradiction_text,
            {
                "priority": "P0",
                "action": "逐项向客户复核冲突信息，并将确认人、确认时间和最终原话写入记录",
                "owner": "销售负责人",
                "due_time": "执行任何阶段推进动作前",
                "reason": "防止错误事实进入 CRM 或触发错误工作流",
            },
        )

    actions.append(_action(config["stage_actions"][stage]))

    risk_order = {name: index for index, name in enumerate(config["risk_priority"])}
    risks.sort(key=lambda item: (LEVEL_ORDER[item.level], risk_order.get(item.type, 999)))
    unique_actions: list[NextAction] = []
    seen_actions: set[str] = set()
    for item in sorted(actions, key=lambda action: int(action.priority[1])):
        if item.action not in seen_actions:
            unique_actions.append(item)
            seen_actions.add(item.action)

    confirmed_count = sum(
        fact.status == FactStatus.CONFIRMED
        for fact in [
            facts.customer_needs,
            facts.core_scenarios,
            facts.budget,
            facts.decision_makers,
            facts.timeline,
        ]
    )
    confidence = min(
        0.95,
        0.45 + confirmed_count * 0.08 + min(len(state["stage_evidence"]), 3) * 0.04,
    )
    confidence = max(0.3, confidence - sum(risk.level == "高" for risk in risks) * 0.05)

    return {
        "risks": [item.model_dump(mode="json") for item in risks],
        "next_actions": [item.model_dump(mode="json") for item in unique_actions[:6]],
        "unconfirmed_information": facts.unconfirmed_labels(),
        "confidence": confidence,
        "risk_rule_version": config["version"],
    }
