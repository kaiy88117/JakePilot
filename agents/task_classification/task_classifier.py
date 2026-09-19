"""
任务分类器 - 专门负责判断用户请求的类型

职责：
1. 接收用户输入，分析其意图
2. 根据预定义的分类规则，将任务归类为：
   - appointment（预约任务）
   - query（查询任务）  
   - pay（支付任务）
   - statistics（统计任务）
   - other（其他任务）
3. 提供清晰的分类结果和置信度
"""

from langchain.prompts import PromptTemplate
from langchain_core.language_models.chat_models import BaseChatModel
from typing import Dict, Any


class TaskClassifier:
    """任务分类器 - 使用LLM进行智能任务分类"""
    
    def __init__(self, llm: BaseChatModel):
        self.llm = llm
        self._initialize_prompt()
        self.chain = self.prompt | self.llm
    
    def _initialize_prompt(self):
        """初始化分类提示词模板"""
        self.prompt = PromptTemplate(
            input_variables=["task"],
            template=(
                "你是电商售后 Agent 的任务路由器，需要判断当前用户请求应交给哪个领域 Agent。\n"
                "商品说明、保修政策和通用退换货规则归类为 knowledge。\n"
                "包含具体订单号的订单状态、订单物流进度、退货资格或退货申请归类为 order_after_sales。\n"
                "明确要求预约上门安装、检测或维修，或者正在补充上门服务信息，归类为 appointment。\n"
                "支付结果通知归类为 pay，内部服务完成通知归类为 statistics。\n"
                "闲聊以及与商品、订单、售后和上门服务无关的问题归类为 other。\n"
                "请将以下任务归类为以下类别，输出只能选择以下之一：\n"
                "1. appointment（预约任务）\n"
                "2. knowledge（知识与政策咨询）\n"
                "3. order_after_sales（订单、物流与退货办理）\n"
                "4. pay（支付任务）\n"
                "5. statistics（统计任务）\n"
                "6. other（其它任务）\n"
                "只返回类别英文名。\n\n"
                "示例：'耳机保修期多久'输出knowledge。\n"
                "示例：'帮我查询订单JP20260919001的物流'输出order_after_sales。\n"
                "示例：'预约周六上午上门安装空调'输出appointment。\n"
                "示例：'给我讲一个笑话'输出other。\n"
                "以下是本次归类任务:\n"
                "任务内容：{task}"
            )
        )
    
    async def classify_task(self, task: str) -> str:
        """
        分类任务
        
        Args:
            task: 用户输入的任务内容
            
        Returns:
            str: 分类结果 ('appointment', 'query', 'pay', 'statistics', 'other')
        """
        try:
            category_msg = await self.chain.ainvoke({"task": task})
            category = category_msg.content.strip().lower()
            
            # 验证分类结果是否有效
            valid_categories = {
                'appointment',
                'knowledge',
                'order_after_sales',
                'query',
                'pay',
                'statistics',
                'other',
            }
            if category not in valid_categories:
                return 'other'  # 默认归类为其他
                
            return category
            
        except Exception as e:
            print(f"任务分类失败: {str(e)}")
            return 'other'  # 发生错误时默认归类为其他
    
    def get_category_description(self, category: str) -> str:
        """获取分类类别的描述信息"""
        descriptions = {
            'appointment': '上门服务预约 - 安装、检测或维修预约',
            'query': '电商售后查询 - 商品、政策、订单、物流或退款咨询',
            'knowledge': '知识咨询 - 商品说明、保修与通用售后政策',
            'order_after_sales': '订单售后 - 订单、物流、退货资格与退货申请',
            'pay': '支付任务 - 支付结果相关事务',
            'statistics': '服务统计 - 工程师上报服务完成状态',
            'other': '其他任务 - 与电商售后无关的请求'
        }
        return descriptions.get(category, '未知任务类型')
