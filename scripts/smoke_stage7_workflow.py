from __future__ import annotations

from datetime import datetime
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.schemas import NextAction, OpportunityAnalysis, OpportunityFacts, RiskItem  # noqa: E402
from integrations.feishu_bitable import FeishuBitableClient  # noqa: E402


def main() -> None:
    load_dotenv()
    request_id = f"FDE-STAGE7-SMOKE-{datetime.now():%Y%m%d%H%M%S}"
    analysis = OpportunityAnalysis(
        request_id=request_id,
        original_note="阶段七工作流联调专用记录：客户进入决策审批，但预算和最终决策链仍待确认。",
        facts=OpportunityFacts(),
        stage="S4",
        stage_name="决策审批",
        stage_evidence=[],
        risks=[
            RiskItem(
                type="预算风险",
                level="高",
                description="预算仍未确认，可能影响审批。",
                evidence="字段状态：未确认",
            )
        ],
        next_actions=[
            NextAction(
                priority="P0",
                action="确认预算和最终决策链",
                owner="销售负责人",
                due_time="2个工作日内",
                reason="避免决策审批停滞。",
            )
        ],
        unconfirmed_information=["预算金额或预算范围", "最终决策人及决策链"],
        confidence=0.62,
        quality_issues=["联调记录：转人工复核以验证协同分支。"],
    )
    client = FeishuBitableClient.from_env()
    record_id = client.create_opportunity(analysis)
    print(f"request_id={request_id}")
    print(f"record_id={record_id}")


if __name__ == "__main__":
    main()
