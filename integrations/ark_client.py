from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from openai import OpenAI
from pydantic import ValidationError

from agent.prompts import EXTRACTION_SYSTEM_PROMPT
from agent.schemas import (
    Evidence,
    EvidenceBackedFact,
    FactStatus,
    ModelMetadata,
    OpportunityFacts,
)


StructuredMode = Literal["tool_call", "json_object"]


class ArkExtractionError(RuntimeError):
    """Raised after all configured Ark extraction attempts fail."""


@dataclass
class ExtractionResult:
    facts: OpportunityFacts
    metadata: ModelMetadata


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class ArkExtractor:
    mock_mode: bool
    api_key: str | None
    base_url: str
    model: str | None
    timeout_seconds: float = 30.0
    max_retries: int = 2
    structured_mode: StructuredMode = "tool_call"
    fallback_to_mock: bool = False
    retry_delay_seconds: float = 0.5
    client_factory: Callable[[], Any] | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls) -> "ArkExtractor":
        structured_mode = os.getenv("ARK_STRUCTURED_MODE", "tool_call").strip().lower()
        if structured_mode not in {"tool_call", "json_object"}:
            raise ValueError("ARK_STRUCTURED_MODE 只能是 tool_call 或 json_object。")
        return cls(
            mock_mode=_truthy(os.getenv("MOCK_MODE"), default=True),
            api_key=os.getenv("ARK_API_KEY"),
            base_url=os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
            model=os.getenv("ARK_MODEL"),
            timeout_seconds=float(os.getenv("ARK_TIMEOUT_SECONDS", "30")),
            max_retries=max(0, int(os.getenv("ARK_MAX_RETRIES", "2"))),
            structured_mode=structured_mode,
            fallback_to_mock=_truthy(os.getenv("ARK_FALLBACK_TO_MOCK"), default=False),
        )

    def extract(self, note: str) -> ExtractionResult:
        started = time.perf_counter()
        if self.mock_mode:
            return ExtractionResult(
                facts=self._mock_extract(note),
                metadata=ModelMetadata(
                    provider="mock",
                    model="heuristic-v1",
                    structured_mode="mock",
                    attempts=1,
                    latency_ms=self._elapsed_ms(started),
                ),
            )

        self._validate_configuration()
        client = self._create_client()
        previous_output: str | None = None
        previous_error: str | None = None
        attempts = self.max_retries + 1

        for attempt in range(1, attempts + 1):
            response = None
            try:
                model_note = self._sanitize_untrusted_note(note)
                messages = self._build_messages(model_note, previous_output, previous_error)
                response = self._request(client, messages)
                raw_payload = self._extract_payload(response)
                facts = self._enforce_fact_polarity(
                    OpportunityFacts.model_validate(raw_payload),
                    source_note=model_note,
                )
                return ExtractionResult(
                    facts=facts,
                    metadata=ModelMetadata(
                        provider="volcengine_ark",
                        model=self.model or "unknown",
                        structured_mode=self.structured_mode,
                        attempts=attempt,
                        latency_ms=self._elapsed_ms(started),
                    ),
                )
            except Exception as exc:
                previous_output = self._safe_response_text(response)
                previous_error = self._format_error(exc)
                if attempt < attempts and self.retry_delay_seconds > 0:
                    time.sleep(self.retry_delay_seconds * attempt)

        if self.fallback_to_mock:
            return ExtractionResult(
                facts=self._mock_extract(note),
                metadata=ModelMetadata(
                    provider="mock",
                    model="heuristic-v1",
                    structured_mode="mock",
                    attempts=attempts,
                    latency_ms=self._elapsed_ms(started),
                    fallback_used=True,
                ),
            )
        raise ArkExtractionError(
            f"豆包事实提取在 {attempts} 次尝试后仍失败：{previous_error or '未知错误'}"
        )

    def _validate_configuration(self) -> None:
        missing = []
        if not self.api_key:
            missing.append("ARK_API_KEY")
        if not self.model:
            missing.append("ARK_MODEL")
        if missing:
            raise ArkExtractionError(f"真实模型模式缺少配置：{', '.join(missing)}")
        if self.timeout_seconds <= 0:
            raise ArkExtractionError("ARK_TIMEOUT_SECONDS 必须大于 0。")

    def _create_client(self):
        if self.client_factory:
            return self.client_factory()
        return OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout_seconds,
            max_retries=0,
        )

    def _build_messages(
        self,
        note: str,
        previous_output: str | None,
        previous_error: str | None,
    ) -> list[dict[str, str]]:
        schema = json.dumps(OpportunityFacts.model_json_schema(), ensure_ascii=False)
        user_content = f"输出结构 JSON Schema：\n{schema}\n\n销售拜访记录：\n{note}"
        if previous_error:
            user_content += (
                "\n\n上一次输出未通过校验，请只修复结构和事实边界问题。"
                f"\n校验错误：{previous_error}"
            )
            if previous_output:
                user_content += f"\n上一次输出：{previous_output[:4000]}"
        return [
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

    def _request(self, client, messages: list[dict[str, str]]):
        common: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
        }
        if self.structured_mode == "tool_call":
            common.update(
                {
                    "tools": [
                        {
                            "type": "function",
                            "function": {
                                "name": "submit_opportunity_facts",
                                "description": "提交有原文证据的销售商机结构化事实",
                                "parameters": OpportunityFacts.model_json_schema(),
                            },
                        }
                    ],
                    "tool_choice": {
                        "type": "function",
                        "function": {"name": "submit_opportunity_facts"},
                    },
                }
            )
        else:
            common["response_format"] = {"type": "json_object"}
        return client.chat.completions.create(**common)

    def _extract_payload(self, response) -> dict[str, Any]:
        message = response.choices[0].message
        if self.structured_mode == "tool_call":
            tool_calls = message.tool_calls or []
            matching = [call for call in tool_calls if call.function.name == "submit_opportunity_facts"]
            if not matching:
                raise ValueError("模型未调用 submit_opportunity_facts 工具")
            return self._parse_json(matching[0].function.arguments)
        return self._parse_json(message.content or "")

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        candidate = content.strip()
        if candidate.startswith("```"):
            candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
            candidate = re.sub(r"\s*```$", "", candidate)
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            start, end = candidate.find("{"), candidate.rfind("}")
            if start < 0 or end <= start:
                raise
            payload = json.loads(candidate[start : end + 1])
        if not isinstance(payload, dict):
            raise ValueError("模型结构化输出必须是 JSON 对象")
        return payload

    @staticmethod
    def _safe_response_text(response: Any) -> str | None:
        if response is None:
            return None
        try:
            message = response.choices[0].message
            if message.tool_calls:
                return message.tool_calls[0].function.arguments
            return message.content
        except Exception:
            return None

    @staticmethod
    def _format_error(exc: Exception) -> str:
        if isinstance(exc, ValidationError):
            return str(exc)[:2000]
        if isinstance(exc, json.JSONDecodeError):
            return f"JSON 解析失败：{exc.msg}"
        return f"{type(exc).__name__}: {str(exc)[:1000]}"

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return max(0, int((time.perf_counter() - started) * 1000))

    @staticmethod
    def _sentences(note: str) -> list[str]:
        return [s.strip() for s in re.split(r"[。！？\n；]", note) if s.strip()]

    @classmethod
    def _sanitize_untrusted_note(cls, note: str) -> str:
        injection_patterns = [
            r"忽略.{0,20}(?:规则|指令|要求)",
            r"输出\s*S[0-5]",
            r"不要保留证据",
            r"伪造",
            r"请把.{0,30}写成",
        ]
        safe_sentences = [
            sentence
            for sentence in cls._sentences(note)
            if not any(re.search(pattern, sentence, re.IGNORECASE) for pattern in injection_patterns)
        ]
        return "。".join(safe_sentences) or "无可提取的客户业务事实。"

    @classmethod
    def _enforce_fact_polarity(
        cls,
        facts: OpportunityFacts,
        source_note: str | None = None,
    ) -> OpportunityFacts:
        negative_markers = {
            "customer_needs": [
                "不需要", "没有需求", "未提出需求", "没有讨论客户需求", "只希望保留普通联系",
            ],
            "core_scenarios": [
                "没有场景", "未说明", "不需要", "不同意", "只希望保留普通联系",
            ],
            "validation_commitments": ["不同意", "拒绝", "取消", "未安排", "没有安排", "不安排"],
            "commercial_discussions": ["未讨论", "没有讨论", "尚未讨论"],
            "decision_progress": ["没有提交", "未提交", "尚未", "没有进入", "未进入", "不审批"],
            "contract_or_order": ["未签", "没有签", "尚未签", "作废", "取消订单", "未确认订单"],
        }
        source_sentences = cls._sentences(source_note or "")
        for field_name, markers in negative_markers.items():
            fact = getattr(facts, field_name)
            if not fact.is_confirmed:
                continue
            quotes = [item.quote for item in fact.evidence]
            contexts = []
            for quote in quotes:
                matches = [sentence for sentence in source_sentences if quote in sentence]
                contexts.extend(matches or [quote])
            if any(marker in context for context in contexts for marker in markers):
                setattr(
                    facts,
                    field_name,
                    EvidenceBackedFact(
                        status=FactStatus.UNCONFIRMED,
                        evidence=fact.evidence,
                        note="证据为否定、缺失或尚未发生的表述，不能作为正向阶段信号。",
                    ),
                )
        return facts

    def _mock_extract(self, note: str) -> OpportunityFacts:
        injection_markers = [
            "忽略所有规则",
            "忽略以上规则",
            "不要保留证据",
            "伪造",
            "输出 S0",
            "输出 S1",
            "输出 S2",
            "输出 S3",
            "输出 S4",
            "输出 S5",
        ]
        sentences = [
            sentence
            for sentence in self._sentences(note)
            if not any(marker in sentence for marker in injection_markers)
        ]
        uncertainty_markers = [
            "未确认", "尚未", "暂无", "没有", "不确定", "未最终", "还没",
            "可能", "应该", "感觉", "似乎", "挺感兴趣", "考虑", "不需要",
            "不同意", "拒绝", "未签", "没有签署", "取消订单", "已经作废",
        ]

        def hits(words: list[str]) -> list[str]:
            return [s for s in sentences if any(word in s for word in words)]

        def confirmed_hits(words: list[str]) -> list[str]:
            return [
                s
                for s in sentences
                if any(word in s for word in words)
                and not any(marker in s for marker in uncertainty_markers)
            ]

        need_hits = confirmed_hits(["希望", "需要", "痛点", "问题", "低效", "管理", "统一", "困难"])
        scene_hits = confirmed_hits(
            ["场景", "销售线索", "CRM", "客户管理", "渠道", "拜访记录", "拜访复盘", "审批", "合同"]
        )
        validation_hits = [
            s
            for s in sentences
            if any(verb in s for verb in ["同意", "安排", "愿意", "确认参加", "已完成"])
            and any(activity in s for activity in ["演示", "试用", "技术交流", "方案评估"])
            and not any(marker in s for marker in uncertainty_markers)
        ]
        commercial_hits = confirmed_hits(["讨论了预算", "预算为", "预算是", "报价", "采购流程", "合同条款"])
        decision_hits = confirmed_hits(["内部立项", "进入审批", "提交审批", "供应商决策", "供应商选型"])
        contract_hits = confirmed_hits(["已签合同", "合同已签", "订单已确认", "正式订单"])
        budget_hits = confirmed_hits(["预算", "报价"])
        timeline_hits = confirmed_hits(["下周", "下个月", "本月", "季度", "上线", "采购时间", "计划"])
        dm_hits = confirmed_hits(["决策人", "采购负责人", "总经理", "老板", "CEO", "负责人审批"])
        influencer_hits = hits(["影响人", "市场负责人", "业务负责人", "IT负责人", "销售负责人"])
        customer_name = None
        match = re.search(r"(?:拜访|客户(?:是|为)?)[：: ]*([\u4e00-\u9fa5A-Za-z0-9·（）()_-]{2,24})", note)
        if match:
            customer_name = match.group(1).rstrip("，,。")

        def confirmed(value, quotes: list[str]):
            if not value:
                return EvidenceBackedFact(status=FactStatus.UNCONFIRMED)
            return EvidenceBackedFact(
                status=FactStatus.CONFIRMED,
                value=value,
                evidence=[Evidence(quote=quote) for quote in quotes],
            )

        def unconfirmed(quotes: list[str], note_text: str):
            return EvidenceBackedFact(
                status=FactStatus.UNCONFIRMED,
                evidence=[Evidence(quote=quote) for quote in quotes],
                note=note_text,
            )

        uncertain_budget = [s for s in sentences if "预算" in s and any(m in s for m in uncertainty_markers)]
        uncertain_timeline = [
            s for s in sentences
            if any(word in s for word in ["时间", "上线", "采购", "计划"])
            and any(m in s for m in uncertainty_markers)
        ]
        uncertain_decision = [
            s for s in sentences
            if any(word in s for word in ["决策人", "采购负责人", "审批人"])
            and any(m in s for m in uncertainty_markers)
        ]
        name_quotes = [s for s in sentences if customer_name and customer_name in s][:1]

        return OpportunityFacts(
            customer_name=confirmed(customer_name, name_quotes),
            customer_needs=confirmed(need_hits[:3], need_hits[:3]),
            core_scenarios=confirmed(scene_hits[:3], scene_hits[:3]),
            budget=(
                unconfirmed(uncertain_budget[:2], "预算被提及，但尚未确认")
                if uncertain_budget
                else confirmed("；".join(budget_hits[:2]) or None, budget_hits[:2])
            ),
            decision_makers=(
                unconfirmed(uncertain_decision[:2], "决策人被提及，但尚未确认")
                if uncertain_decision
                else confirmed(dm_hits[:2], dm_hits[:2])
            ),
            influencers=confirmed(influencer_hits[:2], influencer_hits[:2]),
            timeline=(
                unconfirmed(uncertain_timeline[:2], "时间计划被提及，但尚未确认")
                if uncertain_timeline
                else confirmed("；".join(timeline_hits[:2]) or None, timeline_hits[:2])
            ),
            validation_commitments=confirmed(validation_hits[:3], validation_hits[:3]),
            commercial_discussions=confirmed(commercial_hits[:3], commercial_hits[:3]),
            decision_progress=confirmed(decision_hits[:3], decision_hits[:3]),
            contract_or_order=confirmed(contract_hits[:3], contract_hits[:3]),
        )
