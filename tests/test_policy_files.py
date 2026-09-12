import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_analysis_policy_covers_all_assignment_categories():
    policy = json.loads((ROOT / "rules" / "analysis_rules.json").read_text(encoding="utf-8"))
    categories = {rule["category"] for rule in policy["rules"]}
    assert categories == {"事实边界", "推断处理", "缺失信息", "证据要求", "矛盾处理", "下一步行动"}
    assert policy["version"] == "v1.0"


def test_analysis_policy_lists_all_required_crm_fields():
    policy = json.loads((ROOT / "rules" / "analysis_rules.json").read_text(encoding="utf-8"))
    assert set(policy["crm_fields"]) == {
        "客户需求",
        "核心场景",
        "预算",
        "决策人",
        "影响人",
        "时间计划",
        "商机阶段",
        "风险",
        "下一步行动",
        "未确认信息",
    }


def test_risk_policy_contains_required_categories_and_stage_actions():
    policy = json.loads((ROOT / "rules" / "risk_action_rules.json").read_text(encoding="utf-8"))
    assert policy["version"] == "v1.0"
    assert set(policy["stage_actions"]) == {"S0", "S1", "S2", "S3", "S4", "S5"}
    assert set(policy["risk_priority"]) == {
        "预算未确认",
        "决策链不清晰",
        "时间计划缺失",
        "需求价值不足",
        "客户态度模糊",
        "商务条件冲突",
        "前后信息矛盾",
    }
