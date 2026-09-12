import pytest
from pydantic import ValidationError

from agent.schemas import Evidence, EvidenceBackedFact, FactStatus, OpportunityFacts


def test_confirmed_fact_requires_value():
    with pytest.raises(ValidationError):
        EvidenceBackedFact[str](
            status=FactStatus.CONFIRMED,
            evidence=[Evidence(quote="客户确认预算")],
        )


def test_confirmed_fact_requires_evidence():
    with pytest.raises(ValidationError):
        EvidenceBackedFact[str](status=FactStatus.CONFIRMED, value="20万元")


def test_unconfirmed_fact_cannot_contain_fabricated_value():
    with pytest.raises(ValidationError):
        EvidenceBackedFact[str](status=FactStatus.UNCONFIRMED, value="推测为20万元")


def test_contradiction_preserves_both_values_and_evidence():
    fact = EvidenceBackedFact[str](
        status=FactStatus.CONTRADICTORY,
        conflicting_values=["20万元", "10万元"],
        evidence=[Evidence(quote="预算是20万元"), Evidence(quote="后来表示预算只有10万元")],
    )
    assert fact.value is None
    assert len(fact.conflicting_values) == 2


def test_contradiction_cannot_choose_a_final_value():
    with pytest.raises(ValidationError):
        EvidenceBackedFact[str](
            status=FactStatus.CONTRADICTORY,
            value="20万元",
            conflicting_values=["20万元", "10万元"],
            evidence=[Evidence(quote="预算是20万元"), Evidence(quote="后来表示预算只有10万元")],
        )


def test_missing_crm_fields_default_to_unconfirmed_without_values():
    facts = OpportunityFacts()
    assert facts.budget.status == FactStatus.UNCONFIRMED
    assert facts.budget.value is None
    assert "预算金额或预算范围" in facts.unconfirmed_labels()
