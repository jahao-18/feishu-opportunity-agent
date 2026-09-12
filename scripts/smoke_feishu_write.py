from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.schemas import (  # noqa: E402
    Evidence,
    EvidenceBackedFact,
    OpportunityAnalysis,
    OpportunityFacts,
)
from integrations.feishu_bitable import FeishuBitableClient  # noqa: E402


REQUEST_ID = "FDE-STAGE6-SMOKE-001"


def build_analysis() -> OpportunityAnalysis:
    evidence = [Evidence(quote="客户希望统一销售拜访记录并自动生成跟进建议")]
    facts = OpportunityFacts(
        customer_name=EvidenceBackedFact[str](
            status="confirmed",
            value="阶段六联调样例客户",
            evidence=[Evidence(quote="阶段六联调样例客户")],
        ),
        customer_needs=EvidenceBackedFact[list[str]](
            status="confirmed",
            value=["统一销售拜访记录"],
            evidence=evidence,
        ),
        core_scenarios=EvidenceBackedFact[list[str]](
            status="confirmed",
            value=["自动生成跟进建议"],
            evidence=evidence,
        ),
    )
    return OpportunityAnalysis(
        request_id=REQUEST_ID,
        original_note="阶段六联调样例客户希望统一销售拜访记录并自动生成跟进建议。",
        facts=facts,
        stage="S1",
        stage_name="需求初探",
        stage_evidence=evidence,
        risks=[],
        next_actions=[],
        unconfirmed_information=["预算金额或预算范围", "最终决策人及决策链", "采购或上线时间计划"],
        confidence=0.88,
        rule_version="feishu-smoke-v1",
        risk_rule_version="risk-v1",
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(ROOT / ".env")
    client = FeishuBitableClient.from_env()
    rules = client.load_stage_rules()
    if [rule.stage for rule in rules] != ["S0", "S1", "S2", "S3", "S4", "S5"]:
        raise RuntimeError("飞书阶段规则不是完整的 S0-S5")

    analysis = build_analysis()
    first_record_id = client.upsert_opportunity(analysis)
    second_record_id = client.upsert_opportunity(analysis)
    matched = client.search_records(field_name="请求编号", value=REQUEST_ID)

    print("stage_rules_ok=True")
    print(f"record_id={first_record_id}")
    print(f"idempotent_update={first_record_id == second_record_id}")
    print(f"matched_records={len(matched)}")
    return 0 if first_record_id == second_record_id and len(matched) == 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
