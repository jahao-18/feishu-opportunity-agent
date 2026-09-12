from __future__ import annotations

from enum import Enum
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field, model_validator


class FactStatus(str, Enum):
    """Truth status of a CRM field under the assignment's fact-boundary rules."""

    CONFIRMED = "confirmed"
    UNCONFIRMED = "unconfirmed"
    CONTRADICTORY = "contradictory"
    CANNOT_DETERMINE = "cannot_determine"


class Evidence(BaseModel):
    """A verbatim excerpt that supports a fact or an uncertainty."""

    quote: str = Field(min_length=1, max_length=500)
    source: Literal["visit_note", "user_clarification", "customer_history"] = "visit_note"


T = TypeVar("T")


class EvidenceBackedFact(BaseModel, Generic[T]):
    """A value that cannot be separated from its truth status and evidence."""

    status: FactStatus = FactStatus.UNCONFIRMED
    value: T | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    conflicting_values: list[str] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def enforce_fact_boundary(self) -> "EvidenceBackedFact[T]":
        if self.status == FactStatus.CONFIRMED:
            if self.value is None or self.value == "" or self.value == []:
                raise ValueError("confirmed fact must contain a non-empty value")
            if not self.evidence:
                raise ValueError("confirmed fact must contain source evidence")
            if self.conflicting_values:
                raise ValueError("confirmed fact cannot contain conflicting values")
        elif self.status in {FactStatus.UNCONFIRMED, FactStatus.CANNOT_DETERMINE}:
            if self.value is not None:
                raise ValueError("unconfirmed or indeterminable fact cannot contain a value")
            if self.conflicting_values:
                raise ValueError("unconfirmed or indeterminable fact cannot contain conflicting values")
        elif self.status == FactStatus.CONTRADICTORY:
            if self.value is not None:
                raise ValueError("contradictory fact cannot select one value as final")
            if len(self.conflicting_values) < 2:
                raise ValueError("contradictory fact must preserve at least two conflicting values")
            if len(self.evidence) < 2:
                raise ValueError("contradictory fact must preserve evidence for both sides")
        return self

    @property
    def is_confirmed(self) -> bool:
        return self.status == FactStatus.CONFIRMED


def _unknown_text() -> EvidenceBackedFact[str]:
    return EvidenceBackedFact[str]()


def _unknown_list() -> EvidenceBackedFact[list[str]]:
    return EvidenceBackedFact[list[str]]()


class OpportunityFacts(BaseModel):
    """Evidence-backed facts extracted from a sales visit note."""

    customer_name: EvidenceBackedFact[str] = Field(default_factory=_unknown_text)
    customer_needs: EvidenceBackedFact[list[str]] = Field(default_factory=_unknown_list)
    core_scenarios: EvidenceBackedFact[list[str]] = Field(default_factory=_unknown_list)
    budget: EvidenceBackedFact[str] = Field(default_factory=_unknown_text)
    decision_makers: EvidenceBackedFact[list[str]] = Field(default_factory=_unknown_list)
    influencers: EvidenceBackedFact[list[str]] = Field(default_factory=_unknown_list)
    timeline: EvidenceBackedFact[str] = Field(default_factory=_unknown_text)

    # Evidence signals consumed by the deterministic stage engine.
    validation_commitments: EvidenceBackedFact[list[str]] = Field(
        default_factory=_unknown_list,
        description="仅限客户肯定同意演示、试用、技术交流或方案评估；拒绝或尚未安排不算。",
    )
    commercial_discussions: EvidenceBackedFact[list[str]] = Field(
        default_factory=_unknown_list,
        description="仅限双方确实讨论预算、报价、采购流程或合同条款；仅出现名词不算。",
    )
    decision_progress: EvidenceBackedFact[list[str]] = Field(
        default_factory=_unknown_list,
        description="仅限项目已进入内部立项、审批或供应商决策；决策人未知、预算待批或否定表述不算。",
    )
    contract_or_order: EvidenceBackedFact[list[str]] = Field(
        default_factory=_unknown_list,
        description="仅限合同已经签署或正式订单已经确认；拟签、未签、取消或作废不算。",
    )

    def unconfirmed_labels(self) -> list[str]:
        labels = {
            "customer_needs": "客户需求",
            "core_scenarios": "核心场景",
            "budget": "预算金额或预算范围",
            "decision_makers": "最终决策人及决策链",
            "influencers": "关键影响人",
            "timeline": "采购或上线时间计划",
        }
        return [
            label
            for field_name, label in labels.items()
            if getattr(self, field_name).status != FactStatus.CONFIRMED
        ]

    def contradictory_fields(self) -> list[tuple[str, EvidenceBackedFact]]:
        labels = {
            "customer_name": "客户名称",
            "customer_needs": "客户需求",
            "core_scenarios": "核心场景",
            "budget": "预算",
            "decision_makers": "决策人",
            "influencers": "影响人",
            "timeline": "时间计划",
            "validation_commitments": "方案验证承诺",
            "commercial_discussions": "商务讨论",
            "decision_progress": "决策审批进展",
            "contract_or_order": "合同或订单",
        }
        return [
            (label, getattr(self, field_name))
            for field_name, label in labels.items()
            if getattr(self, field_name).status == FactStatus.CONTRADICTORY
        ]


StageCode = Literal["S0", "S1", "S2", "S3", "S4", "S5"]
OpportunitySignal = Literal[
    "customer_needs",
    "core_scenarios",
    "validation_commitments",
    "commercial_discussions",
    "decision_progress",
    "contract_or_order",
]


class RuleRequirement(BaseModel):
    """At least one of the listed evidence signals must be confirmed."""

    any_of: list[OpportunitySignal] = Field(min_length=1)


class StageRule(BaseModel):
    stage: StageCode
    name: str
    rank: int = Field(ge=0, le=5)
    condition: str
    requirements: list[RuleRequirement] = Field(default_factory=list)
    fallback: bool = False

    @model_validator(mode="after")
    def validate_fallback(self) -> "StageRule":
        if self.rank != int(self.stage[1]):
            raise ValueError("stage rank must match the numeric part of stage code")
        if self.fallback and self.requirements:
            raise ValueError("fallback rule cannot contain requirements")
        if not self.fallback and not self.requirements:
            raise ValueError("non-fallback rule must contain requirements")
        return self


class StageAssessment(BaseModel):
    """Deterministic and explainable output of the S0-S5 rule engine."""

    current_stage: StageCode
    current_stage_name: str
    matched_stages: list[StageCode] = Field(default_factory=list)
    next_stage: StageCode | None = None
    next_stage_name: str | None = None
    next_stage_reason: str
    next_stage_gaps: list[str] = Field(default_factory=list)
    regressed_from: StageCode | None = None
    regression_reason: str | None = None
    rule_source: Literal["local_file", "feishu_bitable", "local_fallback"] = "local_file"
    rule_warning: str | None = None


class RiskItem(BaseModel):
    type: str
    level: Literal["高", "中", "低"]
    description: str
    evidence: str = Field(min_length=1)


class NextAction(BaseModel):
    priority: Literal["P0", "P1", "P2"]
    action: str = Field(min_length=1)
    owner: str = Field(default="销售负责人", min_length=1)
    due_time: str = Field(default="待确认", min_length=1)
    reason: str = Field(min_length=1)


class ModelMetadata(BaseModel):
    provider: Literal["volcengine_ark", "mock"]
    model: str
    structured_mode: Literal["tool_call", "json_object", "mock"]
    attempts: int = Field(ge=1)
    latency_ms: int = Field(ge=0)
    fallback_used: bool = False


class AgentDecision(str, Enum):
    ASK_CLARIFICATION = "ask_clarification"
    CONFIRM_CONTRADICTION = "confirm_contradiction"
    READY_TO_SAVE = "ready_to_save"
    MANUAL_REVIEW = "manual_review"
    SAVED = "saved"
    ENDED = "ended"


class ClarificationQuestion(BaseModel):
    id: str = Field(min_length=1)
    field: str = Field(min_length=1)
    question: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    required: bool = False


class ClarificationTurn(BaseModel):
    question_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)


class AgentControl(BaseModel):
    decision: AgentDecision
    reason: str = Field(min_length=1)
    questions: list[ClarificationQuestion] = Field(default_factory=list, max_length=2)
    recommended_questions: list[ClarificationQuestion] = Field(default_factory=list, max_length=2)
    requires_user_confirmation: bool = False


class OpportunityAnalysis(BaseModel):
    request_id: str
    original_note: str
    facts: OpportunityFacts
    stage: StageCode
    stage_name: str
    stage_evidence: list[Evidence]
    stage_assessment: StageAssessment | None = None
    risks: list[RiskItem]
    next_actions: list[NextAction]
    unconfirmed_information: list[str]
    confidence: float = Field(ge=0, le=1)
    quality_issues: list[str] = Field(default_factory=list)
    rule_version: str = "v1.0"
    risk_rule_version: str = "v1.0"
    model_metadata: ModelMetadata | None = None
    agent_control: AgentControl | None = None
    feishu_record_id: str | None = None
