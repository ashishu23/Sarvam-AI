from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Language = Literal['en-IN', 'hi-IN']


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class CreateCase(StrictModel):
    title: str = Field(default='My case', min_length=1, max_length=120)
    domain: Literal['employment_payment', 'consumer_refund', 'tenancy', 'family', 'business', 'cybercrime', 'unsure'] = 'employment_payment'
    language: Language = 'en-IN'
    consent: bool


class Mutation(StrictModel):
    request_id: UUID
    expected_revision: int = Field(ge=1)


class MessageInput(Mutation):
    text: str = Field(min_length=1, max_length=6000)
    document_id: UUID | None = None
    transcript_id: UUID | None = None


class Fact(StrictModel):
    key: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=160)
    value: str = Field(max_length=1200)
    source_id: str | None = None
    quote: str = Field(default='', max_length=1500)
    status: Literal['proposed', 'user_confirmed', 'disputed', 'unknown'] = 'proposed'


class Citation(StrictModel):
    source_id: str
    quote: str = Field(min_length=1, max_length=1800)


class Finding(StrictModel):
    title: str = Field(max_length=200)
    explanation: str = Field(max_length=2400)
    attention: Literal['high', 'medium', 'low'] = 'medium'
    reason: str = Field(max_length=600)
    action: str = Field(max_length=800)
    citations: list[Citation] = Field(default_factory=list, max_length=6)


class Answer(StrictModel):
    answer: str = Field(max_length=7000)
    questions: list[str] = Field(default_factory=list, max_length=2)
    facts: list[Fact] = Field(default_factory=list, max_length=25)
    citations: list[Citation] = Field(default_factory=list, max_length=12)
    gaps: list[str] = Field(default_factory=list, max_length=15)


class Assessment(StrictModel):
    summary: str = Field(max_length=5000)
    favourable: list[Finding] = Field(default_factory=list, max_length=12)
    risks: list[Finding] = Field(default_factory=list, max_length=12)
    unclear: list[Finding] = Field(default_factory=list, max_length=12)
    next_steps: list[str] = Field(default_factory=list, max_length=10)
    gaps: list[str] = Field(default_factory=list, max_length=15)


class ConfirmInput(Mutation):
    facts: list[Fact] = Field(default_factory=list, max_length=40)
    proposal_id: UUID | None = None


class AnalysisInput(Mutation):
    document_id: UUID | None = None


class ReviewInput(Mutation):
    complete: bool = False
    pages: dict[str, str] = Field(default_factory=dict)


class Payment(StrictModel):
    id: UUID
    kind: Literal['payment', 'offer', 'deduction']
    amount_paise: int = Field(ge=0, le=100_000_000_000)
    reference: str = Field(min_length=1, max_length=100)
    date: str = Field(max_length=40)
    attributed: bool = False
    confirmed: bool = False


class LedgerInput(Mutation):
    monthly_pay_paise: int = Field(ge=0, le=100_000_000_000)
    periods: int = Field(ge=0, le=120)
    basis: Literal['gross', 'net', 'unknown']
    period_labels: str = Field(max_length=200)
    comparable_confirmed: bool
    entries: list[Payment] = Field(default_factory=list, max_length=100)


class DraftInput(Mutation):
    kind: Literal['clarification', 'payment_request', 'lawyer_questions'] = 'clarification'


class DraftOutput(StrictModel):
    text: str = Field(max_length=10000)
    placeholders: list[str] = Field(default_factory=list, max_length=20)


class DraftEdit(Mutation):
    text: str = Field(min_length=1, max_length=15000)


class OutputInput(StrictModel):
    output_id: UUID
    language: Language


class LanguageInput(StrictModel):
    language: Language


class CompareInput(Mutation):
    before_document_id: UUID
    after_document_id: UUID
