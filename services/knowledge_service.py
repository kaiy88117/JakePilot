# services/knowledge_service.py

import numpy as np
import faiss
from typing import List, Dict, Tuple, Optional
from db.db_router import DatabaseRouter
from .text_embedding import embed_input
import logging

logger = logging.getLogger(__name__)

class KnowledgeService:
    """知识库服务类 - 结合数据库存储和向量检索"""
    
    def __init__(self, db_path: str = 'sqlite:///data/jakepilot.db'):
        # 使用统一的DatabaseRouter，符合架构设计
        self.db_router = DatabaseRouter(db_path)
        self.db = self.db_router.knowledge  # 通过router访问knowledge repository
        self.index = None
        self.document_ids = []  # 维护文档ID与索引位置的映射
        self.initialized = False
        
        # 默认知识库内容
        self.default_knowledge = [
            {
                "content": "我们推拿房的营业时间是每天上午9点到晚上10点，全年无休。",
                "category": "营业时间",
                "keywords": ["营业时间", "开门", "关门", "几点", "时间"]
            },
            {
                "content": "我们提供多种推拿服务：全身推拿（120元/60分钟）、肩颈推拿（80元/30分钟）、足底按摩（100元/45分钟）、背部推拿（90元/40分钟）。",
                "category": "服务项目",
                "keywords": ["服务", "推拿", "按摩", "价格", "收费", "多少钱"]
            },
            {
                "content": "我们有专业的男女技师为您服务。所有技师都经过专业培训，持有相关资格证书。您可以根据个人喜好选择男技师或女技师。",
                "category": "技师信息",
                "keywords": ["技师", "师傅", "男", "女", "专业", "资格"]
            },
            {
                "content": "我们店的位置位于北京海淀区中关村大街27号，交通便利，地铁2号线A口向北步行100米即可到达",
                "category": "门店地址",
                "keywords": ["地址", "门店信息", "到达方式", "交通"]
            },
            {
                "content": "全身推拿能够舒缓全身肌肉疲劳，促进血液循环，缓解压力。特别适合久坐办公室的上班族和体力劳动者。",
                "category": "服务介绍",
                "keywords": ["全身推拿", "效果", "作用", "好处", "适合"]
            },
            {
                "content": "肩颈推拿专门针对颈椎和肩部问题，能有效缓解颈椎疼痛、肩膀僵硬等问题。特别推荐给长期使用电脑的人群。",
                "category": "服务介绍",
                "keywords": ["肩颈推拿", "颈椎", "肩膀", "疼痛", "僵硬"]
            },
            {
                "content": "足底按摩通过刺激足部穴位，能够调节全身气血运行，缓解疲劳，改善睡眠质量。",
                "category": "服务介绍",
                "keywords": ["足底按摩", "脚", "穴位", "睡眠", "疲劳"]
            },
            {
                "content": "我们的技师都有3年以上的专业经验，定期接受培训以确保服务质量。我们注重客户体验，力求为每位客户提供最舒适的服务。",
                "category": "服务质量",
                "keywords": ["经验", "专业", "培训", "质量", "舒适"]
            },
            {
                "content": "如需取消或更改预约，请提前至少2小时通知我们。临时取消可能会产生一定的费用。",
                "category": "预约政策",
                "keywords": ["取消", "更改", "改期", "退约", "政策"]
            },
            {
                "content": "我们提供会员卡服务，充值500元送50元，充值1000元送150元。会员还可享受预约优先权和生日优惠。",
                "category": "会员服务",
                "keywords": ["会员", "充值", "优惠", "折扣", "生日"]
            }
        ]
        self.legacy_default_contents = {
            item["content"] for item in self.default_knowledge
        }
        self.default_knowledge = [
            {
                "content": "演示商城销售的普通电子商品默认提供12个月有限保修；具体期限以商品详情页、订单和厂商保修卡为准。",
                "category": "电商-保修政策",
                "keywords": ["保修", "质保", "耳机", "电子商品", "12个月"],
            },
            {
                "content": "符合条件的商品可在签收次日起七日内申请七日无理由退货；商品需保持完好，定制、激活或依法不适用的商品除外。",
                "category": "电商-退换货政策",
                "keywords": ["七日无理由", "退货", "签收", "商品完好"],
            },
            {
                "content": "订单物流属于实时业务数据，需要提供订单号并通过订单工具核验；知识库不能替代实时物流查询。",
                "category": "电商-订单物流",
                "keywords": ["订单", "物流", "快递", "订单号", "实时查询"],
            },
            {
                "content": "退款到账时间取决于售后审核结果和原支付渠道。退款申请成功不等于已经到账，应以订单售后记录为准。",
                "category": "电商-退款政策",
                "keywords": ["退款", "到账", "审核", "支付渠道"],
            },
            {
                "content": "上门安装或维修预约需要确认商品与服务类型、期望上门时间和预计服务时长；系统匹配可用工程师后才能确认预约。",
                "category": "电商-上门服务",
                "keywords": ["上门", "安装", "维修", "预约", "工程师"],
            },
            {
                "content": "涉及创建退货、换货、维修或退款工单的操作必须由用户确认；当前演示环境未接入真实订单系统时只能提供流程说明。",
                "category": "电商-安全边界",
                "keywords": ["售后单", "确认", "退货", "换货", "维修"],
            },
        ]

    async def initialize(self):
        """初始化知识库服务"""
        try:
            # 检查数据库中是否已有数据
            existing_docs = self.db.get_all_documents()
            
            legacy_docs = [
                doc for doc in existing_docs
                if doc.get("content") in self.legacy_default_contents
            ]
            for doc in legacy_docs:
                self.db.delete_document(doc["id"], soft_delete=True)
            if legacy_docs:
                logger.info("已迁移 %s 条项目内置旧领域知识", len(legacy_docs))

            active_docs = self.db.get_all_documents()
            await self._create_default_knowledge(
                existing_contents={doc.get("content") for doc in active_docs}
            )
            
            # 构建向量索引
            await self._build_vector_index()
            self.initialized = True
            logger.info("知识库服务初始化完成")
            
        except Exception as e:
            logger.error(f"知识库服务初始化失败: {e}")
            raise

    async def _create_default_knowledge(self, existing_contents=None):
        """创建默认知识库"""
        existing_contents = existing_contents or set()
        for knowledge in self.default_knowledge:
            if knowledge["content"] in existing_contents:
                continue
            try:
                # 生成嵌入向量
                text_for_embedding = f"{knowledge['content']} {' '.join(knowledge['keywords'])}"
                embedding = embed_input(text_for_embedding)
                
                # 保存到数据库
                self.db.add_document(
                    content=knowledge['content'],
                    category=knowledge['category'],
                    keywords=knowledge['keywords'],
                    embedding=embedding
                )
                logger.debug(f"添加默认知识: {knowledge['content'][:50]}...")
                
            except Exception as e:
                logger.error(f"添加默认知识失败: {e}")

    async def _build_vector_index(self):
        """构建向量索引"""
        try:
            documents = self.db.get_all_documents()
            if not documents:
                logger.warning("没有文档可用于构建索引")
                return

            embeddings = []
            self.document_ids = []
            
            for doc in documents:
                if doc.get('embedding'):
                    embeddings.append(doc['embedding'])
                    self.document_ids.append(doc['id'])
                else:
                    # 如果没有嵌入向量，生成一个
                    logger.warning(f"文档 {doc['id']} 缺少嵌入向量，正在生成...")
                    text_for_embedding = f"{doc['content']} {' '.join(doc.get('keywords', []))}"
                    embedding = embed_input(text_for_embedding)
                    
                    # 更新数据库
                    self.db.update_document(doc['id'], embedding=embedding)
                    
                    embeddings.append(embedding)
                    self.document_ids.append(doc['id'])

            if embeddings:
                # 创建FAISS索引
                embeddings_array = np.array(embeddings).astype('float32')
                dimension = embeddings_array.shape[1]
                self.index = faiss.IndexFlatIP(dimension)  # 内积相似度
                self.index.add(embeddings_array)
                logger.info(f"构建向量索引完成，包含 {len(embeddings)} 个向量")
            else:
                logger.warning("没有有效的嵌入向量，无法构建索引")

        except Exception as e:
            logger.error(f"构建向量索引失败: {e}")
            raise

    async def search(self, query: str, top_k: int = 3, category: str = None) -> List[Dict]:
        """搜索相关文档"""
        if not self.initialized or self.index is None:
            logger.warning("知识库服务未初始化或索引不可用")
            return []

        try:
            # 生成查询的嵌入向量
            query_embedding = embed_input(query)
            query_array = np.array([query_embedding]).astype('float32')
            
            # 向量搜索
            scores, indices = self.index.search(query_array, min(top_k * 2, len(self.document_ids)))  # 多检索一些候选
            
            results = []
            for score, idx in zip(scores[0], indices[0]):
                if idx < len(self.document_ids):
                    doc_id = self.document_ids[idx]
                    doc = self.db.get_document(doc_id)
                    
                    if doc:
                        # 如果指定了分类过滤
                        if category and doc.get('category') != category:
                            continue
                            
                        doc['score'] = float(score)
                        doc['rank'] = len(results) + 1
                        results.append(doc)
                        
                        # 达到所需数量就停止
                        if len(results) >= top_k:
                            break
            
            return results
            
        except Exception as e:
            logger.error(f"搜索知识库失败: {e}")
            return []

    async def add_document(self, content: str, category: str, keywords: List[str] = None) -> bool:
        """添加新文档"""
        try:
            if keywords is None:
                keywords = []
            
            # 生成嵌入向量
            text_for_embedding = f"{content} {' '.join(keywords)}"
            embedding = embed_input(text_for_embedding)
            
            # 保存到数据库
            doc_id = self.db.add_document(content, category, keywords, embedding)
            
            # 重建索引
            await self._build_vector_index()
            
            logger.info(f"成功添加文档 {doc_id}: {content[:50]}...")
            return True
            
        except Exception as e:
            logger.error(f"添加文档失败: {e}")
            return False

    async def update_document(self, doc_id: int, content: str = None, category: str = None, keywords: List[str] = None) -> bool:
        """更新文档"""
        try:
            # 如果更新了内容或关键词，需要重新生成嵌入向量
            embedding = None
            if content is not None or keywords is not None:
                # 获取当前文档信息
                current_doc = self.db.get_document(doc_id)
                if not current_doc:
                    return False
                
                # 使用新值或保持原值
                final_content = content if content is not None else current_doc['content']
                final_keywords = keywords if keywords is not None else current_doc.get('keywords', [])
                
                # 生成新的嵌入向量
                text_for_embedding = f"{final_content} {' '.join(final_keywords)}"
                embedding = embed_input(text_for_embedding)
            
            # 更新数据库
            success = self.db.update_document(doc_id, content, category, keywords, embedding)
            
            if success and embedding is not None:
                # 重建索引
                await self._build_vector_index()
            
            return success
            
        except Exception as e:
            logger.error(f"更新文档失败: {e}")
            return False

    async def delete_document(self, doc_id: int, soft_delete: bool = True) -> bool:
        """删除文档"""
        try:
            success = self.db.delete_document(doc_id, soft_delete)
            
            if success:
                # 重建索引
                await self._build_vector_index()
            
            return success
            
        except Exception as e:
            logger.error(f"删除文档失败: {e}")
            return False

    def get_all_documents(self, include_inactive: bool = False) -> List[Dict]:
        """获取所有文档"""
        return self.db.get_all_documents(include_inactive)

    def get_document(self, doc_id: int) -> Dict:
        """获取指定文档"""
        return self.db.get_document(doc_id)

    def get_all_categories(self) -> List[str]:
        """获取所有分类"""
        return self.db.get_all_categories()

    def get_documents_count(self) -> int:
        """获取文档总数"""
        return self.db.get_documents_count()

    def search_by_category(self, category: str) -> List[Dict]:
        """按分类搜索文档"""
        return self.db.search_documents_by_category(category)

    def search_by_keywords(self, keywords: List[str]) -> List[Dict]:
        """按关键词搜索文档"""
        return self.db.search_documents_by_keywords(keywords)
