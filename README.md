# JakePilot

JakePilot 是一个基于 FastAPI、LangChain、FAISS 和 SQLite 的多 Agent 服务咨询与预约编排平台。当前基线版本聚焦预约服务，将意图识别、RAG 知识问答、人员匹配、时间冲突检查、预约写入和用户偏好分析组成一条可追踪的 Agent 链路。

> 项目正在向电商售后客服与订单处理场景迭代。本文档只描述当前已实现的基线能力。

## 核心能力

- **任务路由**：识别咨询、预约和行为分析任务，分发给对应 Agent。
- **RAG 咨询**：基于 FAISS 检索服务知识，支持流式回答。
- **预约编排**：多轮补全时间、服务和人员偏好，检查可用性后写入预约。
- **用户偏好**：记录咨询与预约行为，为后续推荐提供依据。
- **分层架构**：Web/API、Agents、Services 和 DB 各层保持单向依赖。

## 请求链路

```text
用户输入
   ↓
Task Classification Agent
   ├─ Consultation Agent → FAISS 检索 → 知识回答
   ├─ Appointment Agent  → 槽位补全 → 人员匹配 → 预约写入
   └─ User Behavior Agent → 偏好更新 → 个性化提醒
```

## 项目结构

```text
JakePilot/
├── agents/       # Agent 路由、咨询、预约和用户行为逻辑
├── api/          # FastAPI 路由与响应编排
├── services/     # 知识、预约、推荐和 Embedding 服务
├── db/           # SQLAlchemy 模型与 Repository
├── config/       # 数据库与模型 Provider 配置
├── web/          # Jinja2 页面与静态资源
├── tests/        # 核心链路测试
└── app.py        # 应用入口
```

## 快速开始

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

启动后可访问 `http://127.0.0.1:8000` 和 `http://127.0.0.1:8000/docs`。请在 `.env` 中填写模型和 Embedding Provider 配置，不要提交真实密钥。

## 测试

```powershell
pip install -r requirements-dev.txt
python -m pytest -q
```

部分测试需要可用的 LLM/Embedding Provider；未配置外部服务时，应优先运行不依赖网络的单元测试。

## 迭代路线

1. 使用公开电商任务型对话数据替换当前服务预约示例。
2. 将 HermesRAG 封装为售后政策、商品说明和故障手册检索工具。
3. 参考 Pico 引入工作记忆、事件记忆、持久记忆与上下文预算管理。
4. 增加任务成功率、工具调用准确率、槽位 F1 和异常恢复率评测。

## 项目沿革与授权提示

JakePilot 的基线代码由 [smart-appointment-ai-agent](https://github.com/jerry-ai-dev/smart-appointment-ai-agent) 导入后重新初始化 Git 历史。后续的品牌化、电商场景、记忆系统与评测改造将在本仓库中独立迭代。

原仓库在本次导入时未包含明确的 `LICENSE` 文件，因此本仓库不额外声明原代码的授权范围。如需商业使用或再分发，请先向原作者确认授权。
