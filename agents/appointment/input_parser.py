"""
用户输入解析器

负责解析用户输入并提取预约相关信息
"""

import json
import re
from datetime import datetime, timezone
from typing import Dict, Any, Generator
from langchain.prompts import PromptTemplate
from langchain_core.chat_history import InMemoryChatMessageHistory
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, AIMessage

from appointment_decision import (
    AppointmentDecision,
    AppointmentSlots,
    DecisionRequest,
)


class InputParser:
    """用户输入解析器"""
    
    def __init__(self, llm: BaseChatModel):
        self.llm = llm
        self.prompt = self._create_prompt_template()
        self.chain = self.prompt | self.llm
    
    def _create_prompt_template(self) -> PromptTemplate:
        """创建预约信息提取的Prompt模板"""
        from config.time_config import time_config
        current_date = time_config.current_date_str()
        current_datetime = time_config.current_datetime_str()
        
        return PromptTemplate(
            input_variables=["history", "user_input"],
            template=(
                "你是电商售后的上门服务预约 Agent，负责预约商品安装、检测或维修。\n"
                f"当前日期是{current_date}，当前北京时间是{current_datetime}。\n"
                "当前已知信息：{history}\n"
                "用户输入：{user_input}\n"
                "特别注意：如果用户输入是对推荐工程师确认问题的回应（如\"是\"、\"好\"、\"可以\"、\"不\"、\"不要\"等简短回复），请优先识别为confirmation。\n"
                "重要：请你只输出纯JSON格式，不要添加任何markdown标记如```json或```，不要添加任何其他文字说明，直接输出JSON：\n"
                "{{\n"
                '  "gender": "兼容字段，固定输出未知",\n'
                '  "start_time": "预约起始时间，必须转换为标准格式YYYY-MM-DD HH:MM。如果用户说今天下午3点，转换为当前日期 15:00；如果说明天上午10点，转换为明天日期 10:00。如果只说时间没说日期，默认为今天。如果完全没有时间信息则为未知",\n'
                '  "duration": "服务时长，统一转换为分钟数格式，如180分钟、60分钟。如果没有明确时长则为未知",\n'
                '  "project": "上门服务类型与商品，如空调安装、洗衣机维修；没有则为未知",\n'
                '  "preference": "工程师技能或服务偏好，没有则为无",\n'
                '  "technician_name": "指定工程师姓名，没有则为未知",\n'
                '  "confirmation": "如果用户在回应工程师推荐，提取回复内容，否则为未知",\n'
                '  "info_complete": "start_time、project、duration均不为未知时为true",\n'
                '  "unrelated": "如果用户的问题和预约无关（如问天气、聊天等），则为true，否则为false。对推荐工程师的确认回复不应标记为unrelated",\n'
                '  "missing_info": "如果info_complete为false，请列出缺少的关键信息，如[start_time, project]等"\n'
                "}}\n"
                "判断逻辑：\n"
                "1. 如果用户明确指定工程师姓名，请提取technician_name。\n"
                "2. 如果用户回应工程师推荐，请提取confirmation且不要标记为unrelated。\n"
                "3. 必需信息只有start_time、project、duration，不要求性别。\n"
                "4. 只有所有必需信息都不是'未知'时，info_complete才为true。\n"
                "5. 如果用户的问题和预约无关，请将unrelated设为true。\n"
                "再次强调：只输出纯JSON，不要有任何代码块标记或其他文字。"
            )
        )
    
    def parse_stream(self, user_input: str, chat_history: InMemoryChatMessageHistory) -> Generator[str, None, str]:
        """流式解析用户输入"""
        # 添加用户消息到历史
        chat_history.add_message(HumanMessage(content=user_input))
        
        # 构建历史字符串
        history_str = "\n".join(
            [f"用户：{m.content}" if m.type == "human" else f"机器人：{m.content}" 
             for m in chat_history.messages]
        )
        
        # 流式调用LLM
        response_stream = self.chain.stream({"history": history_str, "user_input": user_input})
        ai_content = ""
        
        for chunk in response_stream:
            token = chunk.content if hasattr(chunk, "content") else str(chunk)
            ai_content += token
            yield token
        
        # 添加AI回复到历史
        chat_history.add_message(AIMessage(content=ai_content))
        return ai_content
    
    def parse_data(self, ai_content: str) -> Dict[str, Any]:
        """解析AI返回的JSON数据"""
        try:
            return json.loads(ai_content)
        except json.JSONDecodeError:
            return {
                "gender": "未知",
                "start_time": "未知", 
                "duration": "未知",
                "project": "未知",
                "preference": "未知",
                "technician_name": "未知",
                "confirmation": "未知",
                "info_complete": False,
                "unrelated": False,
                "missing_info": ["所有信息"]
            }

    @staticmethod
    def build_decision_request(
        user_input: str,
        *,
        recent_history: tuple[str, ...],
        appointment_history: Dict[str, Any],
        current_time: datetime | None = None,
    ) -> DecisionRequest:
        """Project legacy state onto the narrow local-model contract."""
        confirmed_values: dict[str, Any] = {}
        for name in (
            "order_id",
            "product_ref",
            "service_type",
            "issue_type",
            "region",
            "date_range",
            "slot_id",
            "confirmation",
        ):
            value = appointment_history.get(name)
            if value not in (None, "", "未知"):
                confirmed_values[name] = value
        try:
            confirmed_slots = AppointmentSlots(**confirmed_values)
        except (TypeError, ValueError):
            confirmed_slots = AppointmentSlots()
        return DecisionRequest(
            message=user_input,
            recent_appointment_history=recent_history[-6:],
            confirmed_slots=confirmed_slots,
            current_time=current_time or datetime.now(timezone.utc),
            allowed_actions=(
                "ask_user",
                "query_slots",
                "request_confirmation",
                "finish",
            ),
        )

    @staticmethod
    def legacy_data_to_decision(
        data: Dict[str, Any],
        appointment_history: Dict[str, Any],
    ) -> AppointmentDecision:
        """Normalize the authoritative strong-model parse for comparison."""
        project = _known(data.get("project")) or _known(
            appointment_history.get("project")
        )
        service_type = _service_type(project)
        product_ref = _product_ref(project, service_type)
        date_range = _known(data.get("date_range")) or _known(
            data.get("start_time")
        )
        slot_id = _known(data.get("slot_id")) or _known(
            appointment_history.get("slot_id")
        )
        region = _known(data.get("region")) or _known(
            appointment_history.get("region")
        )
        confirmation = _is_positive_confirmation(data.get("confirmation"))
        slots = AppointmentSlots(
            order_id=_known(data.get("order_id")),
            product_ref=product_ref,
            service_type=service_type,
            issue_type=_known(data.get("issue_type")),
            region=region,
            date_range=date_range,
            slot_id=slot_id,
            confirmation=confirmation,
        )

        if slot_id and confirmation:
            action = "finish"
            missing_slots: tuple[str, ...] = ()
        elif slot_id:
            action = "request_confirmation"
            missing_slots = ()
        elif all((product_ref, service_type, region, date_range)):
            action = "query_slots"
            missing_slots = ()
        else:
            action = "ask_user"
            missing_slots = tuple(
                name
                for name in (
                    "product_ref",
                    "service_type",
                    "region",
                    "date_range",
                )
                if getattr(slots, name) is None
            )
        return AppointmentDecision(
            action=action,
            slots=slots,
            missing_slots=missing_slots,
        )

    @staticmethod
    def decision_to_legacy_data(
        decision: AppointmentDecision,
        baseline: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Adapt a validated local decision without bypassing legacy guards."""
        data = dict(baseline)
        slots = decision.slots
        if slots.product_ref:
            service_label = {
                "install": "安装",
                "inspect": "检测",
                "repair": "维修",
            }.get(slots.service_type, "")
            data["project"] = f"{slots.product_ref}{service_label}"
        if slots.date_range:
            data["start_time"] = slots.date_range
        if slots.confirmation:
            data["confirmation"] = "是"
        data["unrelated"] = False
        data["missing_info"] = list(decision.missing_slots)
        data["info_complete"] = (
            decision.action != "ask_user"
            and all(
                data.get(name) not in (None, "", "未知")
                for name in ("start_time", "project", "duration")
            )
        )
        return data


def _known(value: Any) -> str | None:
    if value in (None, "", "未知", "无"):
        return None
    return str(value).strip() or None


def _service_type(project: str | None) -> str | None:
    if not project:
        return None
    if "安装" in project:
        return "install"
    if any(word in project for word in ("检测", "检查", "检修")):
        return "inspect"
    if any(word in project for word in ("维修", "修理", "故障")):
        return "repair"
    return None


def _product_ref(project: str | None, service_type: str | None) -> str | None:
    if not project:
        return None
    if not service_type:
        return project
    cleaned = re.sub(r"(安装|检测|检查|检修|维修|修理|故障)", "", project)
    return cleaned.strip() or project


def _is_positive_confirmation(value: Any) -> bool:
    normalized = str(value or "").strip().lower()
    return normalized in {"是", "好", "可以", "同意", "确定", "yes", "ok", "行"}
