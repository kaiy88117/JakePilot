"""
提示词构建器

负责构建各种类型的提示词
"""

from typing import List, Dict, Any


class PromptBuilder:
    """提示词构建器"""
    
    def __init__(self):
        self.system_prompt = self._create_system_prompt()
        self.classification_prompt_template = self._create_classification_prompt_template()
    
    def _create_system_prompt(self) -> str:
        """创建系统提示词"""
        return (
            "你是演示商城的电商售后助手，负责回答商品使用、保修、退换货、物流和上门服务政策。"
            "回答必须以提供的知识库信息为依据，不得编造平台政策、订单状态、退款结果或承诺。"
            "涉及具体订单、物流和退款进度时，若没有业务工具返回的实时事实，应明确说明当前无法核验，"
            "并引导用户提供订单号或转人工处理。知识库没有足够依据时，应直接说明证据不足。"
            "请使用专业、礼貌、简洁的中文回答，并清楚区分已知政策与尚未核验的信息。"
        )
    
    def _create_classification_prompt_template(self) -> str:
        """创建分类提示词模板"""
        return (
            "你是一个分类器，判断用户输入是否属于电商商品或售后咨询。\n"
            "咨询类问题包括：商品使用、保修、退换货、订单、物流、退款和售后政策。\n"
            "非咨询类问题包括：明确预约上门安装或维修，以及完全无关的话题。\n"
            "如果是咨询类问题，回答'YES'。如果是预约类问题或完全无关问题，回答'NO'。\n"
            "只回答YES或NO。\n\n"
            "用户输入：{user_input}"
        )
    
    def build_consultation_prompt(self, user_input: str, knowledge_docs: List[Dict[str, Any]]) -> str:
        """构建咨询提示词"""
        context = self._build_knowledge_context(knowledge_docs)
        return f"{self.system_prompt}\n\n{context}\n用户问题：{user_input}\n\n请回答用户的问题。"
    
    def build_classification_prompt(self, user_input: str) -> str:
        """构建分类提示词"""
        return self.classification_prompt_template.format(user_input=user_input)
    
    def _build_knowledge_context(self, knowledge_docs: List[Dict[str, Any]]) -> str:
        """构建知识库上下文"""
        if not knowledge_docs:
            return "知识库没有足够依据。不得编造平台政策或实时业务状态，请说明无法核验并给出下一步建议。"
        
        context = "\n以下是相关的知识库信息：\n"
        for i, doc in enumerate(knowledge_docs, 1):
            context += f"{i}. {doc['content']}\n"
        context += "\n请仅基于以上信息回答；证据不足的部分必须明确说明，不得使用常识补写平台规则。\n"
        
        return context
