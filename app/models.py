from datetime import datetime

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    razorpay_event_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    event_type: Mapped[str] = mapped_column(String)
    source: Mapped[str] = mapped_column(String)
    razorpay_entity_id: Mapped[str] = mapped_column(String)
    customer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    amount: Mapped[int | None] = mapped_column(nullable=True)
    currency: Mapped[str] = mapped_column(String, default="INR")
    raw_payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String, default="open")
    root_cause_category: Mapped[str | None] = mapped_column(String, nullable=True)
    detected_at: Mapped[datetime]
    created_at: Mapped[datetime]

    decisions: Mapped[list["Decision"]] = relationship(back_populates="event")
    outcomes: Mapped[list["Outcome"]] = relationship(back_populates="event")


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"))
    attempt_number: Mapped[int]
    intervention_type: Mapped[str] = mapped_column(String)
    reasoning: Mapped[str]
    guardrail_verdict: Mapped[str] = mapped_column(String)
    guardrail_reasoning: Mapped[str]
    bounds_snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime]

    event: Mapped["Event"] = relationship(back_populates="decisions")
    actions: Mapped[list["Action"]] = relationship(back_populates="decision")


class Action(Base):
    __tablename__ = "actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    decision_id: Mapped[int] = mapped_column(ForeignKey("decisions.id"))
    razorpay_artifact_id: Mapped[str | None] = mapped_column(String, nullable=True)
    action_type: Mapped[str] = mapped_column(String)
    before_state: Mapped[dict] = mapped_column(JSON)
    after_state: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String)
    executed_at: Mapped[datetime]

    decision: Mapped["Decision"] = relationship(back_populates="actions")


class Outcome(Base):
    __tablename__ = "outcomes"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"))
    decision_id: Mapped[int | None] = mapped_column(ForeignKey("decisions.id"), nullable=True)
    outcome: Mapped[str] = mapped_column(String)
    amount_recovered: Mapped[int | None] = mapped_column(nullable=True)
    attributed: Mapped[bool] = mapped_column(default=True)
    resolved_at: Mapped[datetime]

    event: Mapped["Event"] = relationship(back_populates="outcomes")


class LearnerArm(Base):
    __tablename__ = "learner_arms"
    __table_args__ = (
        UniqueConstraint("failure_reason", "intervention_type", "amount_bracket", name="uq_learner_arm"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    failure_reason: Mapped[str] = mapped_column(String)
    intervention_type: Mapped[str] = mapped_column(String)
    amount_bracket: Mapped[str] = mapped_column(String)
    successes: Mapped[int] = mapped_column(default=0)
    failures: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime]


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"), nullable=True)
    actor: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)
    detail: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime]
