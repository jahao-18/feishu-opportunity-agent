from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agent.nodes.controller import controller
from agent.nodes.extract import extract_facts
from agent.nodes.input_guard import input_guard
from agent.nodes.quality_gate import quality_gate
from agent.nodes.risk_action import analyze_risks_and_actions
from agent.nodes.stage_engine import stage_rule_engine
from agent.nodes.validate import validate_evidence
from agent.state import AgentState


def route_after_guard(state: AgentState) -> str:
    return "stop" if state.get("errors") else "continue"


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("input_guard", input_guard)
    graph.add_node("extract_facts", extract_facts)
    graph.add_node("validate_evidence", validate_evidence)
    graph.add_node("stage_rule_engine", stage_rule_engine)
    graph.add_node("risk_and_action", analyze_risks_and_actions)
    graph.add_node("quality_gate", quality_gate)
    graph.add_node("controller", controller)

    graph.add_edge(START, "input_guard")
    graph.add_conditional_edges(
        "input_guard",
        route_after_guard,
        {"stop": END, "continue": "extract_facts"},
    )
    graph.add_edge("extract_facts", "validate_evidence")
    graph.add_edge("validate_evidence", "stage_rule_engine")
    graph.add_edge("stage_rule_engine", "risk_and_action")
    graph.add_edge("risk_and_action", "quality_gate")
    graph.add_edge("quality_gate", "controller")
    graph.add_edge("controller", END)
    return graph.compile()
