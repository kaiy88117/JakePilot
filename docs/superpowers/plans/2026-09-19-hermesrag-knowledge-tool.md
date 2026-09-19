# HermesRAG Knowledge Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将独立运行的 HermesRAG 通过稳定 HTTP 适配器接入 JakePilot 知识咨询 Agent，返回答案、引用、证据状态与安全 Trace，并在服务不可用时降级到现有本地知识链路。

**Architecture:** JakePilot 只依赖内部 `KnowledgeResult` 契约；`HermesRagClient` 负责认证、请求与字段映射，`ConsultantAgent` 负责选择 HermesRAG 或本地降级链路，SSE 适配层只公开脱敏后的知识检索摘要。HermesRAG 保持独立服务，JakePilot 不访问其 Milvus 或内部模块。

**Tech Stack:** Python 3.11、FastAPI、Pydantic 2、requests、pytest、原生 JavaScript SSE

**Spec:** `docs/superpowers/specs/2026-09-17-ecommerce-agent-architecture-design.md`

## Global Constraints

- 保留现有标准知识问答、订单售后和预约链路。
- HermesRAG 必须通过适配器调用，不复制向量检索代码。
- 凭据只从环境变量读取，不写日志、不进入 SSE。
- 普通用户只看到回答、引用与阶段摘要，不暴露模型隐藏推理或管理员 Trace。
- 外部服务失败不得阻断订单售后和预约能力；知识咨询回退现有本地链路。
- 测试默认离线，使用确定性 Fake Transport。

## Review Focus

- HermesRAG 返回普通 RAG 与 Agentic RAG 两种响应结构时，都映射为同一 `KnowledgeResult`。
- 401 后只允许刷新认证一次，不能形成无限登录循环。
- 超时、非 JSON、字段缺失和 `failed/degraded` 状态必须安全降级且不得泄露内部异常。
- `insufficient_evidence` 不得被伪装为充分证据，也不得补写虚构引用。
- SSE 只能公开允许字段，Bearer Token、用户名、工具参数和原始 Trace 不得进入浏览器。

---

### Task 1: HermesRAG 客户端与内部结果契约

**Files:**
- Create: `services/hermesrag_client.py`
- Create: `tests/test_hermesrag_client.py`
- Modify: `.env.example`

**Interfaces:**
- Consumes: HermesRAG `POST /auth/login` 和 `POST /chat` HTTP JSON 契约。
- Produces: `KnowledgeCitation`、`KnowledgeResult`、`HermesRagClient.query(message, session_id, mode)` 和 `HermesRagError`。

- [ ] 编写失败测试，覆盖 Agentic 响应映射、标准响应映射、401 单次刷新、异常状态和敏感字段隔离。
- [ ] 运行 `pytest tests/test_hermesrag_client.py -q`，确认因模块不存在而失败。
- [ ] 实现最小客户端：环境配置、一次登录、一次 401 刷新、超时、响应校验和统一结果映射。
- [ ] 再次运行测试并确认通过。
- [ ] 提交 `feat: add HermesRAG knowledge client`。

### Task 2: 知识咨询 Agent 接入与本地降级

**Files:**
- Modify: `agents/consultant_agent.py`
- Modify: `agents/consultant/consultation_processor.py`
- Create: `tests/test_knowledge_tool_integration.py`

**Interfaces:**
- Consumes: Task 1 的 `HermesRagClient.query(...) -> KnowledgeResult`。
- Produces: 知识咨询 token 流中的 `knowledge_retrieval` 内部事件和现有 `[REPLY]` 回答；外部失败时回退当前 FAISS 链路。

- [ ] 编写失败测试，覆盖成功回答、引用呈现、证据不足、服务失败回退和非流式一致性。
- [ ] 运行测试，确认新行为不存在而失败。
- [ ] 通过依赖注入接入客户端；仅在显式启用时调用 HermesRAG，失败时记录通用降级事件并调用现有本地链路。
- [ ] 运行知识咨询、路由和会话隔离测试，确认通过。
- [ ] 提交 `feat: route consultation through HermesRAG tool`。

### Task 3: 公开证据事件、前端时间线与文档

**Files:**
- Modify: `api/stream_protocol.py`
- Modify: `web/static/ecommerce-agent.js`
- Modify: `tests/test_stream_protocol.py`
- Modify: `tests/test_ecommerce_frontend.py`
- Modify: `README.md`
- Modify: `docs/DEMO_GUIDE.md`

**Interfaces:**
- Consumes: Task 2 的 `knowledge_retrieval` 内部事件。
- Produces: SSE `knowledge_retrieval` 公开事件，字段仅含 `mode`、`pipeline_status`、`evidence_sufficiency`、`citation_count`、`terminal_reason`、`fallback`。

- [ ] 编写失败测试，验证事件白名单、敏感字段脱敏和前端可读描述。
- [ ] 运行测试，确认新事件尚未被转发和展示。
- [ ] 扩展 SSE 白名单与前端时间线，展示知识模式、证据状态和引用数量。
- [ ] 更新环境变量、启动步骤、降级说明和演示问题。
- [ ] 运行聚焦测试和完整测试；单独报告历史失败，不把环境阻塞记为通过。
- [ ] 提交 `feat: expose HermesRAG evidence status`。

## Completion Gate

- HermesRAG 开启时，知识咨询经过独立适配器返回答案、引用、状态和公开 Trace 摘要。
- HermesRAG 关闭或不可用时，现有本地知识链路继续回答；订单售后和预约链路不受影响。
- 普通 SSE 不包含凭据、原始请求参数、内部异常、检索片段全文或隐藏推理。
- 所有新增测试通过，原有维护中测试无新增回归。
