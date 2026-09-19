import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from agents.appointment.appointment_processor import AppointmentProcessor
from agents.appointment.input_parser import InputParser
from agents.appointment.message_builder import MessageBuilder
from agents.consultant.prompt_builder import PromptBuilder
from agents.task_classification.agent_router import AgentRouter
from agents.task_classification.state_manager import StateManager
from agents.task_classification.task_classifier import TaskClassifier
from agents.task_classification.unrelated_handler import UnrelatedHandler
from services import knowledge_service as knowledge_module
from services import technician_service as technician_module


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
