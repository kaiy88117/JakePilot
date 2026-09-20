from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, JSON, UniqueConstraint, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

Base = declarative_base()


def utc_now_naive() -> datetime:
    """Return UTC without tzinfo for the existing SQLite DateTime schema."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

class Technician(Base):
    __tablename__ = 'technicians'
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)
    gender = Column(String, nullable=True)      # 新增性别字段
    strength = Column(String, nullable=True)    # 新增力气/倾向性字段
    schedules = relationship("TechnicianSchedule", back_populates="technician", cascade="all, delete-orphan")

class TechnicianSchedule(Base):
    __tablename__ = 'technician_schedules'
    id = Column(Integer, primary_key=True)
    technician_id = Column(Integer, ForeignKey('technicians.id'))
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    status = Column(String, nullable=False)  # 'busy' or 'free'
    appointment_id = Column(Integer, nullable=True)
    technician = relationship("Technician", back_populates="schedules")

class KnowledgeDocument(Base):
    __tablename__ = 'knowledge_documents'
    id = Column(Integer, primary_key=True)
    content = Column(Text, nullable=False)
    category = Column(String, nullable=False)
    keywords = Column(JSON, nullable=True)  # 存储关键词列表
    embedding = Column(JSON, nullable=True)  # 存储嵌入向量
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Integer, default=1)  # 软删除标记

class UserBehavior(Base):
    __tablename__ = 'user_behaviors'
    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, default='default_user')  # 单用户场景使用默认用户ID
    action_type = Column(String, nullable=False)  # 'appointment', 'consultation', 'inquiry'
    action_data = Column(JSON, nullable=True)  # 存储行为相关的详细数据
    technician_id = Column(Integer, ForeignKey('technicians.id'), nullable=True)
    session_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    technician = relationship("Technician")

class UserPreference(Base):
    __tablename__ = 'user_preferences'
    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, default='default_user')
    preference_type = Column(String, nullable=False)  # 'technician', 'time', 'service', 'duration'
    preference_value = Column(String, nullable=False)
    confidence_score = Column(Integer, default=1)  # 偏好的置信度（出现次数）
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class UserRecommendation(Base):
    __tablename__ = 'user_recommendations'
    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, default='default_user')
    recommendation_type = Column(String, nullable=False)  # 'technician_available', 'return_reminder', 'service_suggestion'
    content = Column(Text, nullable=False)
    technician_id = Column(Integer, ForeignKey('technicians.id'), nullable=True)
    is_sent = Column(Integer, default=0)  # 是否已发送
    created_at = Column(DateTime, default=datetime.utcnow)
    sent_at = Column(DateTime, nullable=True)
    technician = relationship("Technician")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("tenant_id", "order_no", name="uq_order_tenant_no"),
    )

    id = Column(Integer, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
    order_no = Column(String, nullable=False, index=True)
    item_name = Column(String, nullable=False)
    status = Column(String, nullable=False)
    delivered_at = Column(DateTime, nullable=True)
    return_policy = Column(String, nullable=False, default="seven_day")


class LogisticsEvent(Base):
    __tablename__ = "logistics_events"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False, index=True)
    status = Column(String, nullable=False)
    description = Column(String, nullable=False)
    occurred_at = Column(DateTime, nullable=False)


class AfterSalesRequest(Base):
    __tablename__ = "after_sales_requests"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_after_sales_tenant_idem"
        ),
    )

    id = Column(Integer, primary_key=True)
    request_no = Column(String, nullable=False, unique=True, index=True)
    tenant_id = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
    order_no = Column(String, nullable=False, index=True)
    reason = Column(String, nullable=False)
    status = Column(String, nullable=False)
    idempotency_key = Column(String, nullable=False)
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)


class ActionExecution(Base):
    __tablename__ = "action_executions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_action_tenant_idem"
        ),
    )

    id = Column(Integer, primary_key=True)
    action_id = Column(String, nullable=False, unique=True)
    tenant_id = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
    tool_name = Column(String, nullable=False)
    payload_hash = Column(String, nullable=False)
    idempotency_key = Column(String, nullable=False)
    status = Column(String, nullable=False)
    external_ref = Column(String, nullable=True)
    confirmed_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)


class WorkingState(Base):
    __tablename__ = "working_states"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "user_id", "session_id", name="uq_working_scope"
        ),
    )

    id = Column(Integer, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
    session_id = Column(String, nullable=False, index=True)
    active_intent = Column(String, nullable=False)
    plan_state = Column(String, nullable=False)
    slots_json = Column(JSON, nullable=False, default=dict)
    pending_action_json = Column(JSON, nullable=True)
    version = Column(Integer, nullable=False, default=1)
    expires_at = Column(DateTime, nullable=False, index=True)
    updated_at = Column(DateTime, default=utc_now_naive, nullable=False)


class MemoryEvent(Base):
    __tablename__ = "memory_events"

    id = Column(Integer, primary_key=True)
    event_id = Column(String, nullable=False, unique=True, index=True)
    tenant_id = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
    event_type = Column(String, nullable=False, index=True)
    entity_refs_json = Column(JSON, nullable=False, default=list)
    summary = Column(Text, nullable=False)
    outcome = Column(String, nullable=False)
    source_trace_id = Column(String, nullable=False, index=True)
    occurred_at = Column(DateTime, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=True, index=True)


class UserProfileMemory(Base):
    __tablename__ = "user_profile_memories"

    id = Column(Integer, primary_key=True)
    memory_id = Column(String, nullable=False, unique=True, index=True)
    tenant_id = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
    memory_key = Column(String, nullable=False, index=True)
    memory_value = Column(JSON, nullable=False)
    source_type = Column(String, nullable=False)
    confidence = Column(Float, nullable=False)
    source_trace_id = Column(String, nullable=False, index=True)
    valid_from = Column(DateTime, nullable=False)
    valid_until = Column(DateTime, nullable=True)
    superseded_by = Column(String, nullable=True)


class TurnCheckpoint(Base):
    __tablename__ = "turn_checkpoints"

    id = Column(Integer, primary_key=True)
    turn_id = Column(String, nullable=False, unique=True, index=True)
    tenant_id = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
    session_id = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="started")
    last_event_type = Column(String, nullable=False, default="turn_started")
    delivery_status = Column(String, nullable=False, default="pending")
    business_write_succeeded = Column(Integer, nullable=False, default=0)
    event_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)


class HumanHandoffTicket(Base):
    __tablename__ = "human_handoff_tickets"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "turn_id", name="uq_handoff_tenant_turn"
        ),
    )

    id = Column(Integer, primary_key=True)
    ticket_no = Column(String, nullable=False, unique=True, index=True)
    tenant_id = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
    session_id = Column(String, nullable=False, index=True)
    turn_id = Column(String, nullable=False, index=True)
    reason_code = Column(String, nullable=False)
    summary = Column(String(240), nullable=False)
    verified_facts_json = Column(JSON, nullable=False, default=list)
    evidence_refs_json = Column(JSON, nullable=False, default=list)
    failed_steps_json = Column(JSON, nullable=False, default=list)
    status = Column(String, nullable=False, default="open")
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)
