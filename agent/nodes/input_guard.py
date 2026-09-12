from __future__ import annotations

from uuid import uuid4

from agent.state import AgentState


def input_guard(state: AgentState) -> AgentState:
    note = (state.get("raw_note") or "").strip()
    errors: list[str] = []
    if not note:
        errors.append("请输入销售拜访记录。")
    elif len(note) < 15:
        errors.append("记录过短，请至少提供一个客户问题、场景或下一步信息。")
    elif len(note) > 12_000:
        errors.append("记录超过 12000 字，请先精简后再分析。")
    return {
        "request_id": state.get("request_id") or str(uuid4()),
        "base_note": state.get("base_note") or note,
        "raw_note": note,
        "clarifications": state.get("clarifications", []),
        "errors": errors,
    }
