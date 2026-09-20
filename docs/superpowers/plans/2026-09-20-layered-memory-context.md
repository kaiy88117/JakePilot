# Layered Memory and Context Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 JakePilot 落地租户隔离的 Working、Episodic、Profile 三层业务记忆和统一 Context Engine，并让订单售后任务可恢复但不以记忆替代实时订单事实。

**Architecture:** `MemoryRepository` 负责 SQLAlchemy 持久化，`MemoryManager` 提供确定性写入、过期、版本和召回规则，`ContextEngine` 生成带来源与信任级别的最小上下文投影。订单售后 Agent 只将草稿、待确认状态和已完成事件写入记忆；订单与物流结论仍调用业务工具回源。

**Tech Stack:** Python 3.11、SQLAlchemy、Pydantic 2、SQLite、pytest

**Spec:** `docs/superpowers/specs/2026-09-17-ecommerce-agent-architecture-design.md`

## Global Constraints

- 所有读写强制按 `tenant_id`、`user_id` 隔离。
- Working Memory 默认 30 分钟过期；长期记忆不保存完整聊天或支付敏感信息。
- Profile 的显式偏好和推断偏好分开，冲突更新保留版本来源。
- 记忆不是订单、物流、退款和预约的事实源。
- Context Engine 是上下文筛选入口，最多注入 3 条事件和 3 条偏好。
- 测试使用临时 SQLite，默认离线。

## Review Focus

- 过期 Working Memory 不得恢复待确认写操作。
- 不同租户或用户使用相同 session/order 标识时不得互相召回。
- Profile 冲突更新必须使旧版本失效，不能返回两个“当前值”。
- Context 超预算时必须保留当前请求与任务状态，先裁剪低优先级长期记忆。
- 恢复的订单号和退货原因只能作为任务草稿，执行前仍需订单工具回源和用户确认。

---

### Task 1: 三层记忆数据模型与 Memory Manager

**Files:**
- Modify: `db/models.py`
- Create: `db/repositories/memory_repository.py`
- Modify: `db/repositories/__init__.py`
- Create: `services/memory_manager.py`
- Create: `tests/test_memory_manager.py`

**Interfaces:**
- Produces: `MemoryManager.save/get/clear_working`、`record/recall_event`、`set/recall_profile`。

- [x] 先写失败测试，覆盖隔离、过期、事件排序、Profile 覆盖和敏感字段拒绝。
- [x] 实现最小 SQLAlchemy 模型、Repository 与 Manager。
- [x] 运行记忆测试并提交 `feat: add layered memory manager`。

### Task 2: Context Engine 与预算投影

**Files:**
- Create: `runtime/context_engine.py`
- Create: `tests/test_context_engine.py`

**Interfaces:**
- Consumes: Task 1 的三层读取接口。
- Produces: `ContextEngine.build(...) -> ContextProjection`，每个 Segment 含来源、信任级别、注入原因和估算 Token。

- [x] 先写失败测试，覆盖领域筛选、最多 3+3、过期过滤和预算裁剪顺序。
- [x] 实现确定性 Context Engine，不调用 LLM。
- [x] 运行测试并提交 `feat: build bounded context projections`。

### Task 3: 订单售后任务恢复与 Trace

**Files:**
- Modify: `agents/order_after_sales_agent.py`
- Modify: `api/chat_handler.py`
- Modify: `api/stream_protocol.py`
- Modify: `web/static/ecommerce-agent.js`
- Create: `tests/test_order_memory_integration.py`
- Modify: `tests/test_stream_protocol.py`
- Modify: `tests/test_ecommerce_frontend.py`
- Modify: `README.md`
- Modify: `docs/DEMO_GUIDE.md`

**Interfaces:**
- Consumes: `MemoryManager` 与 `ContextEngine`。
- Produces: 可跨 Agent 实例恢复的退货草稿/待确认状态、完成事件，以及脱敏 `memory_context` SSE 摘要。

- [x] 先写失败测试，验证草稿恢复、过期不恢复、用户隔离、确认前回源和完成后清理。
- [x] 注入共享 Memory Manager；持久化结构化草稿，不保存完整对话。
- [x] 公开仅含召回数量与类型的 `memory_context` 事件并更新前端。
- [x] 更新文档，运行维护套件与全量测试，提交 `feat: persist layered task memory`。

## Completion Gate

- 三层记忆均有真实持久化、租户隔离、过期/版本规则和离线测试。
- Context Engine 的注入选择可解释且受预算与条数上限约束。
- 退货任务重建 Agent 后可恢复草稿，但写入仍经过实时资格工具、确认和幂等门禁。
- 浏览器只看到记忆召回摘要，不暴露原始记忆、订单参数或隐私字段。
