import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from agents.appointment.appointment_processor import AppointmentProcessor
from agents.appointment.appointment_database import AppointmentDatabase
from agents.appointment.input_parser import InputParser
from agents.appointment.message_builder import MessageBuilder
from agents.consultant.prompt_builder import PromptBuilder
from agents.user_behavior.pattern_analyzer import PatternAnalyzer
from agents.user_behavior_agent import UserBehaviorAgent
from agents.task_classification.agent_router import AgentRouter
from agents.task_classification.state_manager import StateManager
from agents.task_classification.task_classifier import TaskClassifier
from agents.task_classification.unrelated_handler import UnrelatedHandler
from services import knowledge_service as knowledge_module
from services import technician_service as technician_module


ROOT = Path(__file__).resolve().parents[1]


def test_router_prompt_describes_ecommerce_after_sales_without_legacy_domain():
    classifier = TaskClassifier(FakeListChatModel(responses=["query"]))
    prompt = classifier.prompt.template

    assert "电商售后" in prompt
    assert "订单物流" in prompt
    assert "上门安装" in prompt
    assert "按摩" not in prompt
    assert "推拿" not in prompt


def test_consultation_prompt_is_grounded_and_honest_about_missing_evidence():
    builder = PromptBuilder()
    prompt = builder.build_consultation_prompt("耳机保修多久？", [])

    assert "电商售后" in prompt
    assert "知识库没有足够依据" in prompt
    assert "不得编造" in prompt
    assert "一般了解" not in prompt
    assert "推拿" not in prompt


def test_unsupported_route_returns_ecommerce_capability_boundary():
    router = AgentRouter(None, None, StateManager())

    async def collect_reply():
        return "".join(
            [token async for token in router.handle_unsupported_task("other")]
        )

    reply = asyncio.run(collect_reply())

    assert "商品与售后政策咨询" in reply
    assert "上门安装或维修预约" in reply
    assert "按摩" not in reply


def test_service_appointment_no_longer_requires_engineer_gender():
    processor = AppointmentProcessor.__new__(AppointmentProcessor)
    history = {}

    complete = processor.update_history_from_data(
        history,
        {
            "start_time": "2026-09-20 10:00",
            "project": "空调安装",
            "duration": "120分钟",
            "gender": "未知",
        },
    )

    assert complete is True


def test_service_appointment_copy_uses_engineer_language():
    builder = MessageBuilder()

    assert "服务类型" in builder.missing_info_prompts["project"]
    assert "工程师" in builder.create_appointment_success_message(
        {"name": "张伟", "gender": "男"}
    )
    assert "按摩" not in builder.create_unrelated_message()


def test_successful_appointment_emits_private_verified_memory_fact():
    class Finder:
        def find_technician_with_thought(self, history, yield_func):
            return {"id": 7, "name": "张伟", "gender": "男"}

        def parse_time_and_duration(self, start_time, duration):
            start = datetime(2026, 9, 20, 10, 0)
            return start, start + timedelta(hours=1), 60

    class Database:
        def save_appointment(self, *args, **kwargs):
            return "APT-001"

        def update_memory_schedule(self, *args, **kwargs):
            return None

    processor = AppointmentProcessor(
        input_parser=None,
        technician_finder=Finder(),
        message_builder=MessageBuilder(),
        appointment_database=Database(),
        llm=None,
    )

    async def collect():
        return [
            token
            async for token in processor.handle_complete_appointment(
                {
                    "start_time": "2026-09-20 10:00",
                    "duration": "60分钟",
                    "project": "空调安装",
                },
                "session-a",
            )
        ]

    tokens = asyncio.run(collect())
    event = next(
        json.loads(token.removeprefix("[EVENT]"))
        for token in tokens
        if token.startswith("[EVENT]")
    )

    assert event["type"] == "memory_fact"
    assert event["data"]["event_type"] == "service_booked"
    assert event["data"]["entity_refs"] == ["APT-001"]


def test_appointment_parser_and_unrelated_replies_have_no_legacy_domain():
    parser = InputParser(FakeListChatModel(responses=["{}"])).prompt.template
    replies = UnrelatedHandler(StateManager()).get_available_replies()

    public_copy = parser + "".join(replies)
    assert "工程师" in public_copy
    assert "电商售后" in public_copy
    assert "按摩" not in public_copy
    assert "推拿" not in public_copy


class FakeTechnicianRepository:
    def __init__(self):
        self.rows = [
            {
                "id": 1,
                "name": "张伟",
                "gender": "男",
                "strength": "擅长深层组织按摩，力气大，善于缓解肩颈腰背酸痛，注重肌肉深层放松",
            }
        ]

    def get_all_technicians(self):
        return self.rows

    def update_technician(self, technician_id, **updates):
        row = next(item for item in self.rows if item["id"] == technician_id)
        row.update(updates)
        return True


def test_builtin_staff_profiles_migrate_to_service_engineers(monkeypatch):
    repository = FakeTechnicianRepository()
    monkeypatch.setattr(
        technician_module,
        "DatabaseRouter",
        lambda: SimpleNamespace(technicians=repository),
    )

    service = technician_module.TechnicianService()
    assert service.initialize_default_technicians() is True

    assert "空调安装" in repository.rows[0]["strength"]
    assert "按摩" not in repository.rows[0]["strength"]


class FakeKnowledgeRepository:
    def __init__(self):
        self.documents = [
            {
                "id": 1,
                "content": "我们推拿房的营业时间是每天上午9点到晚上10点，全年无休。",
                "category": "营业时间",
                "keywords": ["营业时间"],
                "embedding": [1.0, 0.0],
            },
            {
                "id": 2,
                "content": "用户自己上传的商品说明。",
                "category": "用户资料",
                "keywords": ["商品"],
                "embedding": [0.0, 1.0],
            },
        ]
        self.deleted_ids = []

    def get_all_documents(self):
        return [doc for doc in self.documents if doc["id"] not in self.deleted_ids]

    def delete_document(self, doc_id, soft_delete=True):
        self.deleted_ids.append(doc_id)
        return True

    def add_document(self, content, category, keywords, embedding):
        doc_id = len(self.documents) + 1
        self.documents.append(
            {
                "id": doc_id,
                "content": content,
                "category": category,
                "keywords": keywords,
                "embedding": embedding,
            }
        )
        return doc_id


def test_knowledge_initialization_replaces_only_builtin_legacy_seed(monkeypatch):
    repository = FakeKnowledgeRepository()
    monkeypatch.setattr(
        knowledge_module,
        "DatabaseRouter",
        lambda _db_path: SimpleNamespace(knowledge=repository),
    )
    monkeypatch.setattr(knowledge_module, "embed_input", lambda _text: [0.5, 0.5])

    service = knowledge_module.KnowledgeService()
    service._build_vector_index = AsyncMock()
    asyncio.run(service.initialize())

    active_contents = {doc["content"] for doc in repository.get_all_documents()}
    assert 1 in repository.deleted_ids
    assert "用户自己上传的商品说明。" in active_contents
    assert any("七日无理由" in content for content in active_contents)
    assert all("按摩" not in content and "推拿" not in content for content in active_contents)


def test_public_knowledge_page_uses_ecommerce_copy_only():
    source = (ROOT / "web" / "templates" / "knowledge_management.html").read_text(
        encoding="utf-8"
    )

    assert "电商售后知识库" in source
    assert "订单物流" in source
    assert "推拿" not in source
    assert "按摩" not in source
    assert "技师" not in source


def test_user_return_visit_fallbacks_stay_in_ecommerce_domain():
    analyzer = PatternAnalyzer.__new__(PatternAnalyzer)
    analyzer.logger = logging.getLogger(__name__)
    analyzer.analyze_user_preferences = lambda _user_id: None

    agent = UserBehaviorAgent.__new__(UserBehaviorAgent)
    agent.logger = logging.getLogger(__name__)
    agent.get_user_analysis = lambda _user_id: None

    replies = [
        analyzer.generate_return_message("u-1"),
        asyncio.run(agent.generate_personalized_reminder("u-1")),
    ]

    assert all("售后" in reply for reply in replies)
    assert all("按摩" not in reply and "推拿" not in reply for reply in replies)


def test_appointment_behavior_record_uses_ecommerce_default_service():
    recorded = {}

    class Recorder:
        def record_behavior(self, **kwargs):
            recorded.update(kwargs)

    database = AppointmentDatabase()
    database._user_behavior_service = Recorder()
    start = datetime(2026, 9, 20, 10, 0)

    database._record_user_behavior(
        start,
        start + timedelta(minutes=60),
        "engineer-1",
        {},
        "session-1",
    )

    assert recorded["action_data"]["project"] == "上门售后服务"
