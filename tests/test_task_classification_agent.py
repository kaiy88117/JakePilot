"""Offline routing tests for the current e-commerce task graph."""

import asyncio

from agents.task_classification.agent_router import AgentRouter
from agents.task_classification.classification_processor import (
    ClassificationProcessor,
)
from agents.task_classification.state_manager import StateManager
from agents.task_classification.unrelated_handler import UnrelatedHandler
from config.constants import SharedState, StateEnum


class FakeClassifier:
    def __init__(self, category):
        self.category = category

    async def classify_task(self, task):
        return self.category


class FakeAppointmentAgent:
    def __init__(self):
        self.appointment_history = {}
        self.unrelated_callback = None

    async def run_stream(self, user_input=None):
        yield "[REPLY][预约机器人]请补充上门时间。"

    def reset(self):
        self.appointment_history = {}


class FakeConsultantAgent:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def consult_stream(self, task):
        yield "[REPLY][咨询机器人]耳机整机保修一年。"


def make_processor(category):
    state = StateManager(SharedState())
    router = AgentRouter(
        FakeAppointmentAgent(),
        FakeConsultantAgent(),
        state,
    )
    return ClassificationProcessor(
        FakeClassifier(category),
        state,
        router,
        UnrelatedHandler(state),
    )


def run_stream(processor, message):
    async def collect():
        return "".join(
            [
                token
                async for token in processor.process_task_stream(message)
            ]
        )

    return asyncio.run(collect())


def test_routes_knowledge_consultation_to_consultant_agent():
    response = run_stream(make_processor("query"), "耳机保修多久？")

    assert "电商售后咨询" in response
    assert "保修一年" in response


def test_routes_service_request_to_appointment_agent():
    response = run_stream(
        make_processor("appointment"), "预约空调安装"
    )

    assert "上门服务预约" in response
    assert "请补充上门时间" in response


def test_unsupported_request_returns_current_capability_boundary():
    response = run_stream(make_processor("other"), "讲个笑话")

    assert "暂不支持该类型任务" in response
    assert "商品与售后政策咨询" in response
    assert "按摩" not in response


def test_processor_state_can_be_reset():
    processor = make_processor("appointment")
    run_stream(processor, "预约空调安装")

    processor.reset_conversation()

    assert (
        processor.get_current_state_info()["current_state"]
        == StateEnum.CLASSIFY
    )
