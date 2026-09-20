"""Offline contract tests for the current e-commerce appointment agent."""

import asyncio

from agents.appointment_agent import AppointmentAgent


def test_ecommerce_appointment_state_no_longer_requires_gender():
    agent = AppointmentAgent()

    complete = agent.appointment_processor.update_history_from_data(
        agent.appointment_history,
        {
            "project": "空调安装",
            "start_time": "2026-09-21 14:00",
            "duration": "60分钟",
        },
    )

    assert complete is True
    assert agent.appointment_history["project"] == "空调安装"
    assert agent.appointment_history["gender"] is None


def test_incomplete_appointment_asks_for_missing_time_offline():
    agent = AppointmentAgent()
    history = dict(agent.appointment_history)
    history["project"] = "洗衣机维修"

    async def collect():
        return "".join(
            [
                token
            async for token in agent.appointment_processor.handle_incomplete_info(
                {"project": "洗衣机维修"}, history
            )
            ]
        )

    response = asyncio.run(collect())

    assert "时间" in response or "什么时候" in response
    assert "按摩" not in response


def test_reset_clears_current_appointment_state():
    agent = AppointmentAgent()
    agent.appointment_history["project"] = "空调安装"
    agent.appointment_history["start_time"] = "2026-09-21 14:00"
    agent.finished = True

    agent.reset()

    assert agent.appointment_history["project"] is None
    assert agent.appointment_history["start_time"] is None
    assert agent.finished is False
