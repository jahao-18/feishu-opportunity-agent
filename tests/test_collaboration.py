from agent.collaboration import CollaborationRoute, plan_collaboration
from agent.schemas import (
    AgentControl,
    AgentDecision,
    NextAction,
    OpportunityAnalysis,
    OpportunityFacts,
    RiskItem,
)


def make_analysis(**overrides) -> OpportunityAnalysis:
    values = {
        "request_id": "route-001",
        "original_note": "用于协同路由单元测试的客户拜访记录。",
        "facts": OpportunityFacts(),
        "stage": "S0",
        "stage_name": "线索",
        "stage_evidence": [],
        "risks": [],
        "next_actions": [],
        "unconfirmed_information": [],
        "confidence": 0.8,
    }
    values.update(overrides)
    return OpportunityAnalysis(**values)


def routes(analysis: OpportunityAnalysis) -> set[CollaborationRoute]:
    return {item.route for item in plan_collaboration(analysis)}


def test_high_stage_matches_key_opportunity_route():
    assert routes(make_analysis(stage="S3", stage_name="商务评估")) == {
        CollaborationRoute.KEY_OPPORTUNITY
    }


def test_all_matching_routes_execute_in_parallel():
    analysis = make_analysis(
        stage="S4",
        stage_name="决策审批",
        risks=[RiskItem(type="预算", level="高", description="预算未确认", evidence="字段状态：未确认")],
        next_actions=[NextAction(priority="P0", action="确认预算", due_time="2个工作日内", reason="推进商务评估")],
        unconfirmed_information=["预算金额或预算范围"],
        quality_issues=["证据不足"],
    )
    assert routes(analysis) == set(CollaborationRoute)


def test_controller_can_force_manual_review_without_quality_issue():
    analysis = make_analysis(
        agent_control=AgentControl(
            decision=AgentDecision.MANUAL_REVIEW,
            reason="历史阶段发生回退。",
        )
    )
    assert routes(analysis) == {CollaborationRoute.MANUAL_REVIEW}


def test_no_route_for_clean_early_stage_record():
    assert plan_collaboration(make_analysis(stage="S2", stage_name="方案验证")) == []
