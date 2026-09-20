from types import SimpleNamespace

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from agents.appointment_agent import AppointmentAgent
from agents.appointment.appointment_database import AppointmentDatabase
from agents.appointment.technician_finder import TechnicianFinder
from agents.consultant_agent import ConsultantAgent
from agents.task_classification_agent import TaskClassificationAgent


def _unexpected(*args, **kwargs):
    raise AssertionError("default dependency factory must not be called")


def test_appointment_agent_accepts_fixed_model_and_isolated_database(monkeypatch):
    monkeypatch.setattr("agents.appointment_agent.create_chat_model", _unexpected)
    monkeypatch.setattr("agents.appointment_agent.AppointmentDatabase", _unexpected)
    model = FakeListChatModel(responses=["{}"])
    database = object()
    technician_finder = object()

    agent = AppointmentAgent(
        session_id="eval-appointment",
        decision_gateway=SimpleNamespace(mode="disabled"),
        llm=model,
        appointment_database=database,
        technician_finder=technician_finder,
    )

    assert agent.llm is model
    assert agent.appointment_database is database
    assert agent.technician_finder is technician_finder
    assert agent.appointment_processor.appointment_database is database


def test_appointment_helpers_accept_isolated_services(monkeypatch):
    monkeypatch.setattr("services.appointment_service.AppointmentService", _unexpected)
    appointment_service = SimpleNamespace(
        get_all_technicians=lambda: [],
    )
    behavior_service = object()

    finder = TechnicianFinder(appointment_service=appointment_service)
    database = AppointmentDatabase(
        appointment_service=appointment_service,
        user_behavior_service=behavior_service,
    )

    assert finder.appointment_service is appointment_service
    assert finder.find_technician_with_thought(
        {
            "start_time": "2026-09-21 10:00",
            "duration": "60分钟",
            "preference": "无",
            "gender": "未知",
            "technician_name": "未知",
        }
    ) is None
    assert database.appointment_service is appointment_service
    assert database.user_behavior_service is behavior_service


def test_consultant_agent_accepts_fixed_model_and_knowledge_dependencies(
    monkeypatch,
):
    monkeypatch.setattr("agents.consultant_agent.create_chat_model", _unexpected)
    monkeypatch.setattr(
        "agents.consultant_agent.HermesRagClient.from_env",
        _unexpected,
    )
    model = FakeListChatModel(responses=["YES"])
    knowledge_client = object()
    knowledge_retriever = object()

    agent = ConsultantAgent(
        session_id="eval-knowledge",
        llm=model,
        knowledge_client=knowledge_client,
        knowledge_retriever=knowledge_retriever,
    )

    assert agent.llm is model
    assert agent.knowledge_retriever is knowledge_retriever
    assert agent.consultation_processor.knowledge_client is knowledge_client


def test_task_router_accepts_fixed_model(monkeypatch):
    monkeypatch.setattr(
        "agents.task_classification_agent.create_chat_model",
        _unexpected,
    )
    model = FakeListChatModel(responses=["knowledge"])

    agent = TaskClassificationAgent(None, None, llm=model)

    assert agent.llm is model
    assert agent.task_classifier.llm is model
