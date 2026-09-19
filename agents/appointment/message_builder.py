"""
消息构建器

负责构建各种响应消息
"""

from typing import Dict, Any, List


class MessageBuilder:
    """消息构建器"""
    
    def __init__(self):
        self.missing_info_prompts = {
            "start_time": "请问您希望工程师什么时间上门？",
            "duration": "请问需要预留多长时间的服务窗口？",
            "project": "请补充服务类型和商品，例如空调安装或洗衣机维修。",
            "preference": "您对工程师技能或服务方式有特殊要求吗？"
        }
    
    def create_appointment_success_message(self, tech: Dict[str, Any]) -> str:
        """创建预约成功消息"""
        # 检查是否是推荐技师
        if tech.get('is_recommendation'):
            original_tech = tech.get('original_technician', {})
            return (f"\n机器人：已为您预约工程师：{tech['name']}。预约成功！"
                    f"（原指定的{original_tech.get('name', '')}工程师时间冲突，{tech['name']}具备相应服务能力）\n")
        else:
            return f"\n机器人：已为您预约工程师：{tech['name']}。预约成功！\n"

    def create_technician_recommendation_message(self, original_tech: Dict[str, Any], 
                                               recommended_tech: Dict[str, Any], 
                                               appointment_history: Dict[str, Any],
                                               llm=None) -> str:
        """创建技师推荐消息，使用LLM生成个性化措辞"""
        project = appointment_history.get('project', '上门服务')
        start_time = appointment_history.get('start_time', '')
        
        if llm:
            try:
                # 构建LLM提示
                prompt = f"""
作为电商售后预约助手，用户想预约{original_tech['name']}工程师处理{project}，但该工程师在{start_time}不空闲。

我找到了一位技能相近的工程师：
- 姓名：{recommended_tech['name']}
- 技能：{recommended_tech.get('strength', '')}

原工程师技能：{original_tech.get('strength', '')}

请生成一段专业的推荐话术，说明原工程师没空，推荐工程师具备相应技能且该时段可用，并询问用户是否接受调整。

要求：
1. 语气温和、专业
2. 突出推荐工程师的专业性
3. 明确询问用户意愿
4. 字数控制在80字以内
"""
                
                response = llm.invoke(prompt)
                if hasattr(response, 'content'):
                    generated_msg = response.content.strip()
                    if generated_msg:
                        return f"\n机器人：{generated_msg}\n"
                
            except Exception as e:
                print(f"LLM生成推荐消息失败: {e}")
        
        # 如果LLM失败，使用默认消息
        return (f"\n机器人：抱歉，{original_tech['name']}工程师在{start_time}不空闲。"
                f"{recommended_tech['name']}工程师具备{project}相关技能且该时段可用，"
                f"请问是否调整为{recommended_tech['name']}工程师？\n")

    def create_recommendation_declined_message(self, llm=None) -> str:
        """创建用户拒绝推荐时的消息"""
        if llm:
            try:
                prompt = """
用户拒绝了推荐工程师，请生成一段专业回复，并提供更换时间或工程师的选择。

要求：
1. 表达理解用户的选择
2. 提供其他解决方案（如换时间、重新选择等）
3. 保持专业和友好的语气
4. 字数控制在60字以内
"""
                response = llm.invoke(prompt)
                if hasattr(response, 'content'):
                    generated_msg = response.content.strip()
                    if generated_msg:
                        return f"\n机器人：{generated_msg}\n"
            except Exception as e:
                print(f"LLM生成拒绝消息失败: {e}")
        
        # 默认消息
        return "\n机器人：好的，您可以选择其他时间段，或者由我重新匹配工程师。\n"
    
    def create_appointment_failure_message(self, technician_name: str) -> str:
        """创建预约失败消息"""
        if technician_name and technician_name != "未知":
            # 通过Services层访问数据库
            from services.appointment_service import AppointmentService
            appointment_service = AppointmentService()
            specific_tech = appointment_service.get_technician_by_name(technician_name)
            if specific_tech:
                return f"\n机器人：抱歉，{technician_name}工程师在该时段不空闲。请选择其他时间，或者由我重新匹配工程师。\n"
            else:
                return f"\n机器人：抱歉，没有找到名为'{technician_name}'的工程师。请确认姓名，或者由我自动匹配。\n"
        else:
            return "\n机器人：抱歉，该时间段没有合适的工程师空闲，请选择其他时间。\n"
    
    def create_missing_info_questions(self, missing_info: List[str]) -> str:
        """根据缺失信息创建询问"""
        questions = [self.missing_info_prompts.get(field, f"请补充{field}信息") for field in missing_info]
        return "\n" + " ".join(questions) + "\n"
    
    def create_unrelated_message(self) -> str:
        """创建无关请求的消息"""
        return "[REPLY][预约机器人]我当前负责上门安装、检测和维修预约；其他售后问题会交回任务路由 Agent。\n"
    
    def create_parse_error_message(self) -> str:
        """创建解析错误消息"""
        return "[REPLY][预约机器人]\n机器人：解析失败，请重试。\n"
    
    def create_save_failure_message(self) -> str:
        """创建保存失败消息"""
        return "\n机器人：抱歉，预约保存失败，请重试。\n"
