from __future__ import annotations

from copy import deepcopy
from typing import Mapping

from agent.graph import build_graph
from agent.schemas import (
    AgentControl,
    AgentDecision,
    ClarificationQuestion,
    ClarificationTurn,
    OpportunityAnalysis,
)
from agent.state import AgentState
from agent.tools import get_customer_history, save_opportunity


class ConversationError(ValueError):
    pass


class OpportunityAgentService:
    """Application service that keeps multi-turn state and guards side effects."""

    def __init__(self) -> None:
        self.graph = build_graph()

    def start(self, note: str) -> AgentState:
        return self.graph.invoke(
            {
                "base_note": note,
                "raw_note": note,
                "clarifications": [],
                "tool_trace": [],
            }
        )

    @staticmethod
    def _available_questions(state: AgentState) -> dict[str, ClarificationQuestion]:
        control = AgentControl.model_validate(state.get("agent_control"))
        questions = control.questions + control.recommended_questions
        return {question.id: question for question in questions}

    def continue_with(self, state: AgentState, answers: Mapping[str, str]) -> AgentState:
        if state.get("errors"):
            raise ConversationError("当前分析包含输入错误，请重新开始。")

        available = self._available_questions(state)
        clean_answers = {
            question_id: answer.strip()
            for question_id, answer in answers.items()
            if question_id in available and answer.strip()
        }
        if not clean_answers:
            raise ConversationError("请至少回答一个当前 Agent 提出的问题。")

        turns = list(state.get("clarifications", []))
        for question_id, answer in clean_answers.items():
            question = available[question_id]
            turns.append(
                ClarificationTurn(
                    question_id=question_id,
                    question=question.question,
                    answer=answer,
                ).model_dump(mode="json")
            )

        base_note = state.get("base_note") or OpportunityAnalysis.model_validate(
            state["analysis"]
        ).original_note
        supplement = "\n\n".join(
            f"问题：{turn['question']}\n用户回答：{turn['answer']}" for turn in turns
        )
        merged_note = f"原始拜访记录：\n{base_note}\n\n用户补充信息：\n{supplement}"
        return self.graph.invoke(
            {
                "request_id": state.get("request_id"),
                "base_note": base_note,
                "raw_note": merged_note,
                "clarifications": turns,
                "previous_stage": state.get("stage"),
                "tool_trace": list(state.get("tool_trace", [])) + ["conversation.merge"],
            }
        )

    def confirm_save(self, state: AgentState) -> AgentState:
        control = AgentControl.model_validate(state.get("agent_control"))
        if control.decision != AgentDecision.READY_TO_SAVE or not control.requires_user_confirmation:
            raise ConversationError("当前结果尚未达到可保存状态。")

        result = deepcopy(state)
        analysis = OpportunityAnalysis.model_validate(result["analysis"])
        record_id = save_opportunity(analysis)
        analysis.feishu_record_id = record_id
        analysis.agent_control = AgentControl(
            decision=AgentDecision.SAVED,
            reason="用户已确认，商机已保存到飞书多维表格。",
        )
        result.update(
            {
                "analysis": analysis.model_dump(mode="json"),
                "agent_control": analysis.agent_control.model_dump(mode="json"),
                "feishu_record_id": record_id,
                "tool_trace": list(result.get("tool_trace", [])) + ["save_opportunity"],
            }
        )
        return result

    @staticmethod
    def customer_history(state: AgentState) -> list[dict]:
        analysis = OpportunityAnalysis.model_validate(state["analysis"])
        customer = analysis.facts.customer_name
        if not customer.is_confirmed or not customer.value:
            raise ConversationError("当前记录没有已确认的客户名称，无法查询历史商机。")
        return get_customer_history(str(customer.value))

    @staticmethod
    def end(state: AgentState) -> AgentState:
        result = deepcopy(state)
        analysis = OpportunityAnalysis.model_validate(result["analysis"])
        analysis.agent_control = AgentControl(
            decision=AgentDecision.ENDED,
            reason="用户结束了本次商机分析。",
        )
        result["analysis"] = analysis.model_dump(mode="json")
        result["agent_control"] = analysis.agent_control.model_dump(mode="json")
        return result
