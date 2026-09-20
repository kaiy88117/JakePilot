from dotenv import load_dotenv
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from langchain_core.chat_history import InMemoryChatMessageHistory
from config.model_provider import create_chat_model
from appointment_decision.client import LocalDecisionClient
from appointment_decision.gateway import AppointmentDecisionGateway
from appointment_training.evaluator import AppointmentModelReport
from .appointment import (
    InputParser, 
    TechnicianFinder, 
    AppointmentProcessor, 
    MessageBuilder, 
    AppointmentDatabase
)

load_dotenv()


class AppointmentAgent:
    """
    预约机器人主控制器
    
    职责：
    1. 初始化各个组件
    2. 管理会话状态
    3. 协调整个预约流程
    """
    
    def __init__(
        self,
        session_id=None,
        unrelated_callback=None,
        decision_gateway=None,
        llm=None,
        appointment_database=None,
    ):
        # 基础设置
        self.session_id = session_id or str(uuid.uuid4())
        self.unrelated_callback = unrelated_callback
        self.state = None
        if decision_gateway is None:
            (
                self.decision_gateway,
                self.decision_mode_reason,
            ) = self._decision_gateway_from_env()
        else:
            (
                self.decision_gateway,
                self.decision_mode_reason,
            ) = self._guard_injected_gateway(decision_gateway)
        
        # 初始化LLM
        self.llm = llm if llm is not None else self._initialize_llm()
        
        # 初始化组件
        self.input_parser = InputParser(self.llm)
        self.technician_finder = TechnicianFinder()
        self.message_builder = MessageBuilder()
        self.appointment_database = (
            appointment_database
            if appointment_database is not None
            else AppointmentDatabase()
        )
        self.appointment_processor = AppointmentProcessor(
            self.input_parser, 
            self.technician_finder,
            self.message_builder, 
            self.appointment_database,
            self.llm
        )
        
        # 会话管理
        self.chats_by_session_id = {}
        self.chat_history = self._get_chat_history(self.session_id)
        
        # 预约状态
        self.reset()

    def _initialize_llm(self):
        """初始化通用聊天模型"""
        return create_chat_model(temperature=0)

    def _get_chat_history(self, session_id: str) -> InMemoryChatMessageHistory:
        """获取或创建会话历史记录"""
        chat_history = self.chats_by_session_id.get(session_id)
        if chat_history is None:
            chat_history = InMemoryChatMessageHistory()
            self.chats_by_session_id[session_id] = chat_history
        return chat_history

    @staticmethod
    def _guard_injected_gateway(decision_gateway):
        if decision_gateway.mode == "local_first":
            return (
                AppointmentDecisionGateway("shadow", decision_gateway.client),
                "integration_runtime_not_ready",
            )
        return decision_gateway, None

    @staticmethod
    def _resolve_decision_mode(
        requested_mode: str,
        evidence_dir: Path,
    ) -> tuple[str, str | None]:
        normalized = requested_mode.strip().lower()
        if normalized not in {"disabled", "shadow", "local_first"}:
            return "disabled", "invalid_mode"
        if normalized != "local_first":
            return normalized, None

        reports = sorted(
            Path(evidence_dir).glob("appointment_model_*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not reports:
            return "shadow", "formal_evidence_missing"
        try:
            report = AppointmentModelReport.model_validate_json(
                reports[0].read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return "shadow", "formal_evidence_missing"
        if not (
            report.formal_benchmark
            and report.promotion_eligible
            and report.sample_count >= 150
        ):
            return "shadow", "formal_evidence_missing"
        # The current legacy processor invokes the strong parser before this
        # seam and does not yet execute the new action contract. Component
        # evidence alone therefore cannot authorize a true local-first path.
        return "shadow", "integration_runtime_not_ready"

    @classmethod
    def _decision_gateway_from_env(cls):
        requested = os.getenv("APPOINTMENT_DECISION_MODE", "disabled")
        evidence_dir = Path(
            os.getenv(
                "APPOINTMENT_MODEL_EVIDENCE_DIR",
                "artifacts/appointment-model/reports",
            )
        )
        mode, reason = cls._resolve_decision_mode(requested, evidence_dir)
        return AppointmentDecisionGateway(mode, LocalDecisionClient.from_env()), reason

    def _evaluate_decision_model(
        self,
        user_input: str,
        strong_data: dict,
        *,
        recent_history: tuple[str, ...],
        current_time: datetime | None = None,
    ) -> tuple[dict, str]:
        request = InputParser.build_decision_request(
            user_input,
            recent_history=recent_history,
            appointment_history=self.appointment_history,
            current_time=current_time,
        )
        strong_decision = InputParser.legacy_data_to_decision(
            strong_data,
            self.appointment_history,
        )
        outcome = self.decision_gateway.decide(
            request,
            lambda _request: strong_decision,
        )
        selected_data = strong_data
        if (
            self.decision_gateway.mode == "local_first"
            and outcome.source == "local_model"
        ):
            selected_data = InputParser.decision_to_legacy_data(
                outcome.decision,
                strong_data,
            )

        trace_data = outcome.trace.model_dump(mode="json")
        if self.decision_mode_reason and not trace_data["fallback_reason"]:
            trace_data["fallback_reason"] = self.decision_mode_reason
        event = {
            "type": "decision_model_trace",
            "data": trace_data,
        }
        return selected_data, "[EVENT]" + json.dumps(
            event,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    
    def reset(self):
        """重置预约历史和状态"""
        self.appointment_history = {
            "gender": None,
            "start_time": None,
            "duration": None,
            "project": None,
            "preference": None,
            "technician": None,
            "technician_name": None
        }
        self.finished = False
        self.chat_history.clear()

    def set_shared_state(self, shared_state):
        """设置共享状态"""
        self.state = shared_state

    async def run_stream(self, user_input=None):
        """
        流式处理用户预约请求的主函数
        
        这是整个预约流程的入口点，协调各个组件完成预约
        """
        if user_input is None:
            user_input = input("用户：")
        
        recent_history = tuple(
            str(message.content)
            for message in self.chat_history.messages
            if message.type == "human"
        )

        # 1. 解析用户输入（内部 JSON，不向用户流式输出，避免英文字段名暴露在聊天界面）
        ai_content = ""
        for token in self.input_parser.parse_stream(user_input, self.chat_history):
            ai_content += token

        try:
            # 2. 解析AI返回的数据
            data = self.input_parser.parse_data(ai_content)
            data, decision_event = self._evaluate_decision_model(
                user_input,
                data,
                recent_history=recent_history,
            )
            yield decision_event
            self.finished = self.appointment_processor.update_history_from_data(self.appointment_history, data)
            
            # 3. 处理与预约无关的请求
            # 如果正在等待用户确认推荐技师，不要转交给归类机器人
            if data.get("unrelated", False) and not self.appointment_history.get('awaiting_confirmation'):
                # 注意：这里不清空预约历史，保留用户已输入的信息
                # 只设置状态为CLASSIFY，让系统转交给其他机器人处理
                if self.state:
                    from config.constants import StateEnum
                    self.state.value = StateEnum.CLASSIFY
                
                async for token in self.appointment_processor.handle_unrelated_request(
                    user_input, self.unrelated_callback, self.state
                ):
                    yield token
                return
            
            # 4. 处理预约完成的情况
            if self.finished:
                recommendation_pending = False
                async for token in self.appointment_processor.handle_complete_appointment(
                    self.appointment_history, self.session_id
                ):
                    # 检查是否有推荐等待确认
                    if token == "[SIGNAL]recommendation_pending":
                        recommendation_pending = True
                        # 将 finished 设为 False，让预约流程继续
                        self.finished = False
                        continue
                    yield token
                
                # 只有在真正完成预约时才重置状态
                if not recommendation_pending and not self.appointment_history.get('awaiting_confirmation'):
                    self._reset_state_after_appointment()
                return
            
            # 5. 处理信息不完整的情况
            async for token in self.appointment_processor.handle_incomplete_info(data, self.appointment_history):
                yield token
                
        except Exception as e:
            yield self.message_builder.create_parse_error_message()

    def _reset_state_after_appointment(self):
        """预约完成后重置状态"""
        self.reset()
        if self.state:
            from config.constants import StateEnum
            self.state.value = StateEnum.CLASSIFY
