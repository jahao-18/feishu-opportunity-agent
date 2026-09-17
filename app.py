from __future__ import annotations

import json
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from app_security import PublicDemoPolicy, consume_analysis_quota
from agent.presentation import analysis_as_json, build_agent_trace
from agent.schemas import AgentDecision, FactStatus, OpportunityAnalysis
from agent.service import ConversationError, OpportunityAgentService
from agent.tools import ToolUnavailableError
from integrations.feishu_bitable import FeishuBitableClient


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
PUBLIC_POLICY = PublicDemoPolicy.from_env()


@st.cache_resource
def get_agent():
    return OpportunityAgentService()


@st.cache_data(ttl=60)
def get_feishu_status() -> dict:
    try:
        return FeishuBitableClient.from_env().validate_schema().model_dump(mode="json")
    except Exception as exc:
        return {
            "enabled": True,
            "configured": True,
            "ok": False,
            "available_fields": [],
            "missing_fields": [],
            "message": f"飞书连接检查失败：{exc}",
        }


def load_examples() -> list[dict[str, str]]:
    return json.loads((ROOT / "assets" / "examples.json").read_text(encoding="utf-8"))


def render_trace(result: OpportunityAnalysis) -> None:
    st.subheader("Agent 运行轨迹")
    icons = {"complete": "✅", "warning": "⚠️", "pending": "⏳"}
    for item in build_agent_trace(result):
        st.markdown(f"{icons[item.status]} **{item.label}**")
        st.caption(item.detail)


def render_analysis(result: OpportunityAnalysis) -> None:
    def display(fact):
        if fact.status == FactStatus.CONFIRMED:
            if isinstance(fact.value, list):
                return "；".join(str(item) for item in fact.value)
            return fact.value
        status_labels = {
            FactStatus.UNCONFIRMED: "未确认",
            FactStatus.CONTRADICTORY: "信息矛盾，待确认",
            FactStatus.CANNOT_DETERMINE: "无法判断",
        }
        return status_labels[fact.status]

    customer_name = display(result.facts.customer_name)
    st.subheader("分析结果")
    col1, col2 = st.columns(2)
    col1.metric("客户", customer_name if isinstance(customer_name, str) else "已识别")
    col2.metric("商机阶段", f"{result.stage} · {result.stage_name}")
    col3, col4 = st.columns(2)
    col3.metric("置信度", f"{result.confidence:.0%}")
    col4.metric("待确认信息", len(result.unconfirmed_information))

    st.subheader("结构化商机信息")
    left, right = st.columns(2)
    with left:
        st.markdown("**客户需求**")
        st.write(display(result.facts.customer_needs))
        st.markdown("**核心场景**")
        st.write(display(result.facts.core_scenarios))
        st.markdown("**预算**")
        st.write(display(result.facts.budget))
    with right:
        st.markdown("**决策人**")
        st.write(display(result.facts.decision_makers))
        st.markdown("**影响人**")
        st.write(display(result.facts.influencers))
        st.markdown("**时间计划**")
        st.write(display(result.facts.timeline))

    st.subheader("阶段判断依据")
    if result.stage_evidence:
        for item in result.stage_evidence:
            st.success(item.quote)
    else:
        st.info("未发现可以支持阶段升级的直接证据。")

    assessment = result.stage_assessment
    if assessment:
        st.subheader("下一阶段差距")
        if assessment.next_stage:
            st.write(
                f"目标：{assessment.next_stage}（{assessment.next_stage_name}）"
            )
            st.caption(assessment.next_stage_reason)
            if assessment.next_stage_gaps:
                for gap in assessment.next_stage_gaps:
                    st.markdown(f"- {gap}")
            else:
                st.success("下一阶段所需规则信号已齐备。")
        else:
            st.success(assessment.next_stage_reason)
        if assessment.regressed_from:
            st.error(assessment.regression_reason)
        if assessment.rule_source == "feishu_bitable":
            st.caption("阶段规则来源：飞书多维表格")
        elif assessment.rule_warning:
            st.warning(assessment.rule_warning)

    st.subheader("风险与下一步")
    if result.risks:
        for risk in result.risks:
            st.warning(f"{risk.level}｜{risk.type}：{risk.description}")
            st.caption(f"依据：{risk.evidence}")
    else:
        st.info("暂未识别到明确风险")

    for action in result.next_actions:
        st.markdown(
            f"- **{action.priority}｜{action.action}** — 负责人：{action.owner}；"
            f"时间：{action.due_time}；原因：{action.reason}"
        )

    with st.expander("未确认信息、矛盾与证据"):
        st.markdown("**未确认信息**")
        st.write(result.unconfirmed_information or ["无"])
        st.markdown("**矛盾信息**")
        contradictions = {
            label: fact.conflicting_values
            for label, fact in result.facts.contradictory_fields()
        }
        st.write(contradictions or "无")
        st.markdown("**字段证据**")
        evidence = {
            field_name: [item.quote for item in getattr(result.facts, field_name).evidence]
            for field_name in type(result.facts).model_fields
            if getattr(result.facts, field_name).evidence
        }
        st.json(evidence, expanded=False)

    if result.feishu_record_id:
        st.caption(f"已同步飞书多维表格，record_id：{result.feishu_record_id}")
    else:
        st.caption("尚未保存到飞书；请在确认分析结果后点击下方保存按钮。")
    if result.model_metadata:
        metadata = result.model_metadata
        fallback = "；已降级 Mock" if metadata.fallback_used else ""
        st.caption(
            f"模型：{metadata.provider}/{metadata.model}；"
            f"结构化模式：{metadata.structured_mode}；"
            f"尝试次数：{metadata.attempts}；耗时：{metadata.latency_ms}ms{fallback}"
        )
    st.caption(f"阶段规则：{result.rule_version}；风险与行动规则：{result.risk_rule_version}")

    st.download_button(
        "下载结构化分析 JSON",
        data=analysis_as_json(result),
        file_name=f"opportunity-{result.request_id}.json",
        mime="application/json",
        use_container_width=True,
    )


def render_customer_history(state: dict) -> None:
    result = OpportunityAnalysis.model_validate(state["analysis"])
    if not result.facts.customer_name.is_confirmed:
        return
    with st.expander("飞书客户历史"):
        if st.button("查询该客户的历史商机", use_container_width=True):
            try:
                st.session_state["customer_history"] = get_agent().customer_history(state)
                st.session_state["customer_history_request_id"] = state["request_id"]
            except (ConversationError, ToolUnavailableError) as exc:
                st.warning(str(exc))
            except Exception as exc:
                st.error(f"查询失败：{exc}")
        history = (
            st.session_state.get("customer_history")
            if st.session_state.get("customer_history_request_id") == state["request_id"]
            else None
        )
        if history is not None:
            if history:
                st.caption(f"共找到 {len(history)} 条历史记录")
                for record in history:
                    st.json(
                        {
                            "record_id": record.get("record_id"),
                            "fields": record.get("fields", {}),
                        },
                        expanded=False,
                    )
            else:
                st.info("未找到该客户的历史商机。")


def render_agent_controls(state: dict) -> None:
    result = OpportunityAnalysis.model_validate(state["analysis"])
    control = result.agent_control
    if control is None:
        return

    labels = {
        AgentDecision.ASK_CLARIFICATION: "需要补充关键信息",
        AgentDecision.CONFIRM_CONTRADICTION: "需要确认矛盾信息",
        AgentDecision.READY_TO_SAVE: "等待确认保存",
        AgentDecision.MANUAL_REVIEW: "建议人工复核",
        AgentDecision.SAVED: "已保存",
        AgentDecision.ENDED: "已结束",
    }
    st.subheader("Agent 决策")
    st.info(f"{labels[control.decision]}：{control.reason}")

    questions = control.questions or control.recommended_questions
    if questions:
        if control.recommended_questions:
            st.caption("以下问题不是保存前置条件，但补充后可提高商机信息完整度。")
        answers: dict[str, str] = {}
        for question in questions:
            answers[question.id] = st.text_area(
                question.question,
                help=question.reason,
                key=f"answer_{state['request_id']}_{question.id}",
            )
        if st.button("提交补充信息并重新分析", use_container_width=True):
            try:
                with st.spinner("正在合并补充信息并重新判断……"):
                    st.session_state["agent_state"] = get_agent().continue_with(state, answers)
                st.rerun()
            except ConversationError as exc:
                st.warning(str(exc))
            except Exception as exc:
                st.error(f"重新分析失败：{exc}")

    if control.decision == AgentDecision.READY_TO_SAVE:
        write_authorized = bool(st.session_state.get("feishu_write_authorized"))
        can_save = PUBLIC_POLICY.can_attempt_feishu_save and (
            not PUBLIC_POLICY.requires_write_code or write_authorized
        )

        if not PUBLIC_POLICY.can_attempt_feishu_save:
            st.info("公开演示环境已关闭飞书写入；分析、追问和 JSON 下载仍可正常体验。")
        elif PUBLIC_POLICY.requires_write_code and not PUBLIC_POLICY.write_code_configured:
            st.error("飞书写入口令尚未配置，当前按安全策略禁止公网写入。")
        elif PUBLIC_POLICY.requires_write_code and not write_authorized:
            st.info("输入面试演示口令后，才能将确认结果写入飞书多维表格。")
            code_key = f"write_code_{state['request_id']}"
            candidate = st.text_input(
                "演示写入口令",
                type="password",
                key=code_key,
                help="口令仅用于本次浏览器会话的飞书写入授权。",
            )
            if st.button("验证写入口令", use_container_width=True):
                if PUBLIC_POLICY.authorize_feishu_save(candidate):
                    st.session_state["feishu_write_authorized"] = True
                    st.rerun()
                else:
                    st.error("写入口令错误，请检查后重试。")
        else:
            if PUBLIC_POLICY.requires_write_code:
                st.success("本次会话已获得飞书写入权限。")
            st.warning("保存是有副作用操作。系统不会自动执行，需由你明确确认。")

        if st.button(
            "确认保存到飞书多维表格",
            type="primary",
            use_container_width=True,
            disabled=not can_save,
        ):
            try:
                with st.spinner("正在写入飞书多维表格……"):
                    st.session_state["agent_state"] = get_agent().confirm_save(state)
                st.rerun()
            except (ConversationError, ToolUnavailableError) as exc:
                st.warning(f"{exc} 修正配置后可再次点击按钮重试，本次分析结果不会丢失。")
            except Exception as exc:
                st.error(f"保存失败：{exc}")

    if control.decision not in {AgentDecision.SAVED, AgentDecision.ENDED}:
        if st.button("结束本次分析", use_container_width=True):
            st.session_state["agent_state"] = get_agent().end(state)
            st.rerun()


def run() -> None:
    st.set_page_config(page_title="商机录入与分析助手", page_icon="📊", layout="wide")
    st.markdown(
        """
        <style>
        .block-container {max-width: 1180px; padding-top: 2rem; padding-bottom: 4rem;}
        [data-testid="stMetric"] {background: rgba(248,250,252,.8); border: 1px solid #e8edf3; padding: 14px; border-radius: 12px;}
        .demo-badge {display:inline-block; margin-right:.45rem; padding:.25rem .65rem; border-radius:999px; background:#eef4ff; color:#315efb; font-size:.82rem;}
        </style>
        """,
        unsafe_allow_html=True,
    )
    title_col, reset_col = st.columns([5, 1])
    with title_col:
        st.title("商机录入与分析 Agent")
        st.caption("把非结构化销售拜访记录转化为可跟进、可解释、可沉淀的商机信息")
        st.markdown(
            '<span class="demo-badge">无需飞书登录</span>'
            '<span class="demo-badge">证据可追溯</span>'
            '<span class="demo-badge">确认后才同步</span>',
            unsafe_allow_html=True,
        )
    with reset_col:
        if st.button("重置", help="清空本次结果并开始新的分析", use_container_width=True):
            for key in list(st.session_state):
                if key.startswith(("answer_", "write_code_")) or key in {
                    "agent_state",
                    "customer_history",
                    "customer_history_request_id",
                    "analysis_error",
                    "feishu_write_authorized",
                }:
                    st.session_state.pop(key, None)
            st.rerun()

    feishu_status = get_feishu_status()
    with st.sidebar:
        st.subheader("体验说明")
        st.markdown(
            "1. 选择样例或粘贴拜访记录\n"
            "2. 查看 Agent 判级、证据和行动\n"
            "3. 按需回答追问\n"
            "4. 确认后同步飞书"
        )
        st.caption("外部体验者无需加入飞书组织；飞书只作为内部 CRM 与协同后台。")
        if PUBLIC_POLICY.enabled:
            st.caption(
                f"公开演示限流：每个会话 {PUBLIC_POLICY.window_seconds // 60} 分钟内最多 "
                f"{PUBLIC_POLICY.analysis_limit} 次分析。"
            )
        st.divider()
        st.subheader("飞书连接")
        if feishu_status["ok"]:
            st.success(feishu_status["message"])
        elif feishu_status["enabled"]:
            st.warning(feishu_status["message"])
            if feishu_status.get("missing_fields"):
                st.caption("缺少字段：" + "、".join(feishu_status["missing_fields"]))
        else:
            st.info(feishu_status["message"])
        if st.button("重新检查飞书连接"):
            get_feishu_status.clear()
            st.rerun()

    examples = load_examples()
    names = ["自行输入"] + [item["name"] for item in examples]
    st.subheader("1. 输入拜访记录")
    selected = st.selectbox("测试样例", names, help="选择后会自动填入对应的演示记录")
    initial_text = ""
    if selected != "自行输入":
        initial_text = next(item["text"] for item in examples if item["name"] == selected)

    note = st.text_area(
        "销售拜访记录",
        value=initial_text,
        height=240,
        placeholder="粘贴销售拜访纪要、电话记录或销售笔记……",
        key=f"note_{selected}",
    )
    st.caption(f"当前输入 {len(note)} 字；支持 20～12,000 字。")

    if st.button("开始分析", type="primary", use_container_width=True):
        timestamps, retry_after = consume_analysis_quota(
            st.session_state.get("analysis_timestamps", []), PUBLIC_POLICY
        )
        st.session_state["analysis_timestamps"] = timestamps
        if retry_after:
            st.warning(f"本会话请求较频繁，请约 {retry_after} 秒后再试。")
            state = None
        else:
            try:
                st.session_state.pop("analysis_error", None)
                with st.status("Agent 正在运行", expanded=True) as status:
                    st.write("正在提取有原文依据的客户事实……")
                    st.write("正在加载 S0–S5 阶段规则并执行确定性判级……")
                    st.write("正在识别风险、生成下一步行动并执行质量门禁……")
                    state = get_agent().start(note)
                    status.update(label="Agent 分析完成", state="complete", expanded=False)
            except Exception as exc:
                st.session_state["analysis_error"] = str(exc)
                state = None
        if state is None and not retry_after:
            st.error(f"分析失败：{st.session_state['analysis_error']}")
            st.caption("可直接重试；上一次成功的分析结果不会丢失。请检查模型连接，或启用 Mock 演示兜底。")
        elif state is not None and state.get("errors"):
            for error in state["errors"]:
                st.error(error)
        elif state is not None:
            st.session_state["agent_state"] = state
            st.session_state.pop("customer_history", None)
            st.session_state.pop("customer_history_request_id", None)

    state = st.session_state.get("agent_state")
    if state and not state.get("errors"):
        analysis = OpportunityAnalysis.model_validate(state["analysis"])
        st.divider()
        result_tab, trace_tab, action_tab = st.tabs(["分析结果", "Agent 轨迹", "补充与保存"])
        with result_tab:
            render_analysis(analysis)
            render_customer_history(state)
        with trace_tab:
            render_trace(analysis)
        with action_tab:
            render_agent_controls(state)


if __name__ == "__main__":
    run()
