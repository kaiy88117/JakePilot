# Agent Runtime 与订单售后闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保留现有知识咨询与上门预约能力的前提下，引入统一 Turn/Trace、Schema 工具注册和有界执行循环，并新增可演示、可确认、可幂等的订单物流查询与退货申请闭环。

**Architecture:** 新建独立 `runtime` 包承载领域无关的请求、事件、预算、工具与执行循环；现有 Agent 不重写，订单售后 Agent 先接入新 Runtime，知识咨询和预约继续通过兼容适配器运行。订单、物流和退货使用 JakePilot 独立 SQLite 中的匿名 Mock 数据；所有读写都强制携带租户与用户上下文，写操作先冻结参数并要求用户确认，再通过幂等键落库。

**Tech Stack:** Python 3.11、FastAPI、Pydantic v2、SQLAlchemy、SQLite、pytest、原生 async generator、现有 SSE 前端。

**Spec:** `docs/superpowers/specs/2026-09-17-ecommerce-agent-architecture-design.md`

## Global Constraints

- SuperHermes/HermesRAG 仍是独立知识服务，本计划不复制其向量检索实现。
- 保留现有知识咨询、预约、`/chat`、`/chat/stream` 和 `/api/chat/stream` 行为。
- 单轮最多 6 个计划步骤、8 次工具调用和 2 次重规划；任何路径必须产生明确终态。
- 写操作必须经过参数校验、用户确认和幂等执行；模型不得绕过确定性门禁。
- 实时订单与售后数据库是事实源；对话、摘要和模型输出不得覆盖事实源。
- 测试默认离线，使用 Fake Planner、临时 SQLite 和 Mock 工具，不调用 DeepSeek、Ollama 或 HermesRAG。
- 不新增生产依赖；不修改、复制或提交 `.reference_private/`。
- 本阶段不实现长期记忆、HermesRAG 适配、评测平台或后训练。

## Review Focus

- 同一 `idempotency_key` 被重复确认或网络重试时，只允许产生一个退货单，且两次返回同一业务编号。
- 用户使用自己的会话查询其他用户订单时，必须返回“未找到”，不得泄漏订单是否存在。
- Planner 连续返回非法工具、重复等价调用或超过预算时，Loop 必须停止并输出安全终态。
- 用户确认后参数被篡改或与冻结 `payload_hash` 不一致时，写工具必须拒绝执行。
- SSE 中途取消、工具异常或数据库超时时，Trace 必须保留已发生步骤，且不能把内部异常文本发送给浏览器。

---

### Task 1: 建立 Turn、Outcome 与 Trace 契约

**Files:**
- Create: `runtime/__init__.py`
- Create: `runtime/contracts.py`
- Create: `runtime/trace.py`
- Test: `tests/test_runtime_contracts.py`

**Interfaces:**
- Consumes: FastAPI 入口已有的 `message`、`session_id` 和服务端生成的 `turn_id`。
- Produces: `TurnRequest`, `RuntimeEvent`, `TurnOutcome`, `ExecutionBudget`, `TraceRecorder.record()` 和 `TraceRecorder.snapshot()`，后续所有 Runtime 与工具测试只依赖这些稳定接口。

- [ ] **Step 1: 写失败测试，固定契约与脱敏行为**

```python
from runtime.contracts import ExecutionBudget, RuntimeEvent, TurnRequest
from runtime.trace import TraceRecorder


def test_turn_request_rejects_blank_identity_and_message():
    with pytest.raises(ValueError):
        TurnRequest(turn_id="t1", session_id="", user_id="u1", tenant_id="demo", message="x")
    with pytest.raises(ValueError):
        TurnRequest(turn_id="t1", session_id="s1", user_id="u1", tenant_id="demo", message=" ")


def test_budget_uses_the_approved_hard_limits():
    budget = ExecutionBudget()
    assert (budget.max_steps, budget.max_tool_calls, budget.max_replans) == (6, 8, 2)


def test_trace_snapshot_redacts_sensitive_fields():
    trace = TraceRecorder("trace-1")
    trace.record(RuntimeEvent(type="tool_started", data={"tool": "order.get", "phone": "13800138000"}))
    assert trace.snapshot(public=True)[0].data == {"tool": "order.get"}
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_runtime_contracts.py -q`

Expected: FAIL，提示 `runtime.contracts` 不存在。

- [ ] **Step 3: 实现最小契约**

`runtime/contracts.py` 定义：

```python
class TurnStatus(StrEnum):
    COMPLETED = "completed"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"
    CANCELLED = "cancelled"

class TurnRequest(BaseModel):
    turn_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=2000)

class ExecutionBudget(BaseModel):
    max_steps: int = Field(default=6, ge=1, le=6)
    max_tool_calls: int = Field(default=8, ge=1, le=8)
    max_replans: int = Field(default=2, ge=0, le=2)

class RuntimeEvent(BaseModel):
    type: str
    data: dict[str, Any] = Field(default_factory=dict)

class TurnOutcome(BaseModel):
    status: TurnStatus
    answer: str
    trace_id: str
    tool_calls: int = 0
    steps: int = 0
```

`runtime/trace.py` 用内存列表记录 `RuntimeEvent`；`snapshot(public=True)` 仅保留 `tool`、`status`、`step`、`route`、`reason`、`elapsed_ms`、`external_ref` 七个允许公开的字段。

- [ ] **Step 4: 运行测试并确认 GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_runtime_contracts.py -q`

Expected: 3 passed。

- [ ] **Step 5: 提交**

```powershell
git add runtime tests/test_runtime_contracts.py
git commit -m "feat: add agent runtime contracts"
```

### Task 2: 实现 Schema 工具注册与有界 Loop

**Files:**
- Create: `runtime/tools.py`
- Create: `runtime/loop.py`
- Test: `tests/test_tool_runtime.py`

**Interfaces:**
- Consumes: Task 1 的 `TurnRequest`, `RuntimeEvent`, `TurnOutcome`, `ExecutionBudget`, `TraceRecorder`。
- Produces: `ToolRisk`, `ToolContext`, `ToolSpec`, `ToolResult`, `ToolRegistry.register/execute`, `PlanAction`, `Planner` Protocol 和 `BoundedAgentRuntime.run()`。

- [ ] **Step 1: 写失败测试，固定参数验证、权限、确认和幂等入口**

```python
class LookupArgs(BaseModel):
    order_id: str = Field(pattern=r"^JP\d{11}$")

async def lookup(args: LookupArgs, context: ToolContext) -> ToolResult:
    return ToolResult.ok({"order_id": args.order_id})

def test_registry_rejects_invalid_arguments_before_handler_runs():
    registry = ToolRegistry()
    registry.register(ToolSpec(name="order.get", args_model=LookupArgs, risk=ToolRisk.READ, handler=lookup))
    result = asyncio.run(registry.execute("order.get", {"order_id": "bad"}, fake_context()))
    assert result.status == "invalid_arguments"

def test_write_tool_requires_matching_confirmation_hash():
    result = asyncio.run(write_registry().execute("return.create", valid_args(), fake_context()))
    assert result.status == "confirmation_required"
```

- [ ] **Step 2: 写失败测试，固定 Loop 的边界与重复调用终止**

```python
def test_loop_stops_after_six_steps():
    outcome, events = asyncio.run(run_with_planner(AlwaysReplanPlanner()))
    assert outcome.status == "failed"
    assert outcome.steps == 6
    assert events[-1].data["reason"] == "step_budget_exhausted"

def test_loop_stops_repeating_the_same_tool_call():
    outcome, events = asyncio.run(run_with_planner(RepeatedLookupPlanner()))
    assert outcome.status == "failed"
    assert sum(e.type == "tool_started" for e in events) == 1
    assert events[-1].data["reason"] == "repeated_tool_call"
```

- [ ] **Step 3: 运行测试并确认 RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_tool_runtime.py -q`

Expected: FAIL，提示 `runtime.tools` 或 `runtime.loop` 不存在。

- [ ] **Step 4: 实现 Tool Registry**

`runtime/tools.py` 使用以下契约：

```python
class ToolRisk(StrEnum):
    READ = "read"
    WRITE = "write"

class ToolContext(BaseModel):
    tenant_id: str
    user_id: str
    session_id: str
    turn_id: str
    idempotency_key: str | None = None
    confirmed_payload_hash: str | None = None

class ToolResult(BaseModel):
    status: Literal["succeeded", "failed", "not_found", "invalid_arguments", "confirmation_required"]
    data: dict[str, Any] = Field(default_factory=dict)
    public_message: str = ""

class ToolSpec(BaseModel):
    name: str
    args_model: type[BaseModel]
    risk: ToolRisk
    handler: Callable[[BaseModel, ToolContext], Awaitable[ToolResult]]
```

`ToolRegistry.execute()` 的固定顺序是：查工具 → Pydantic 校验参数 → 对 WRITE 工具计算 canonical JSON 的 SHA-256 → 校验确认哈希与幂等键 → 调 handler。内部异常只记录日志并返回通用 `failed`，不得进入 `public_message`。

- [ ] **Step 5: 实现 Bounded Loop**

`runtime/loop.py` 定义 `PlanAction(kind, tool_name, arguments, answer, reason)`；`Planner.next_action(turn, history)` 返回下一步。`BoundedAgentRuntime.run()` 每轮记录 `plan_selected`，执行工具时记录 `tool_started/tool_finished`，对相同 `tool_name + canonical arguments` 的重复调用立即终止；达到 6 步、8 次工具或 2 次重规划分别以明确 reason 失败。生成器关闭或取消时记录 `cancelled` 并释放资源。

- [ ] **Step 6: 运行测试并确认 GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_tool_runtime.py -q`

Expected: 全部通过，且测试不访问网络。

- [ ] **Step 7: 提交**

```powershell
git add runtime/tools.py runtime/loop.py tests/test_tool_runtime.py
git commit -m "feat: add bounded schema tool runtime"
```

### Task 3: 建立匿名订单、物流、退货事实源与幂等 Action Ledger

**Files:**
- Modify: `db/models.py`
- Create: `db/repositories/order_repository.py`
- Modify: `db/repositories/__init__.py`
- Modify: `db/db_router.py`
- Create: `services/order_after_sales_service.py`
- Create: `runtime/ecommerce_tools.py`
- Test: `tests/test_order_after_sales_tools.py`

**Interfaces:**
- Consumes: Task 2 的 `ToolRegistry`, `ToolSpec`, `ToolContext`, `ToolResult`。
- Produces: `OrderRepository.get_order`, `get_logistics`, `check_return_eligibility`, `create_return_request`; `register_ecommerce_tools(registry, service)` 注册 `order.get`、`logistics.get`、`return.check`、`return.create` 四项工具。

- [ ] **Step 1: 写失败测试，固定租户隔离和 Mock 订单**

```python
def test_order_lookup_is_scoped_by_tenant_and_user(tmp_path):
    service = make_service(tmp_path)
    service.seed_demo_data()
    assert service.get_order("demo", "user-a", "JP20260919001") is not None
    assert service.get_order("demo", "user-b", "JP20260919001") is None

def test_logistics_returns_ordered_public_events(tmp_path):
    events = make_service(tmp_path).get_logistics("demo", "user-a", "JP20260919001")
    assert [item["status"] for item in events] == ["shipped", "in_transit"]
```

- [ ] **Step 2: 写失败测试，固定退货资格与幂等写入**

```python
def test_return_creation_is_idempotent(tmp_path):
    service = seeded_service(tmp_path)
    first = service.create_return_request("demo", "user-a", "JP20260919002", "商品破损", "idem-1")
    second = service.create_return_request("demo", "user-a", "JP20260919002", "商品破损", "idem-1")
    assert first["request_id"] == second["request_id"]
    assert service.count_return_requests() == 1

def test_non_returnable_order_never_creates_a_request(tmp_path):
    service = seeded_service(tmp_path)
    result = service.check_return_eligibility("demo", "user-a", "JP20260919003")
    assert result == {"eligible": False, "reason": "activated_product"}
```

- [ ] **Step 3: 运行测试并确认 RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_order_after_sales_tools.py -q`

Expected: FAIL，提示订单模型或服务不存在。

- [ ] **Step 4: 新增数据库模型与 Repository**

在 `db/models.py` 新增：

```python
class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
    order_no = Column(String, nullable=False, unique=True, index=True)
    item_name = Column(String, nullable=False)
    status = Column(String, nullable=False)
    delivered_at = Column(DateTime, nullable=True)
    return_policy = Column(String, nullable=False, default="seven_day")

class LogisticsEvent(Base):
    __tablename__ = "logistics_events"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    status = Column(String, nullable=False)
    description = Column(String, nullable=False)
    occurred_at = Column(DateTime, nullable=False)

class AfterSalesRequest(Base):
    __tablename__ = "after_sales_requests"
    id = Column(Integer, primary_key=True)
    request_no = Column(String, nullable=False, unique=True)
    tenant_id = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
    order_no = Column(String, nullable=False, index=True)
    reason = Column(String, nullable=False)
    status = Column(String, nullable=False)
    idempotency_key = Column(String, nullable=False, unique=True)

class ActionExecution(Base):
    __tablename__ = "action_executions"
    id = Column(Integer, primary_key=True)
    action_id = Column(String, nullable=False, unique=True)
    tool_name = Column(String, nullable=False)
    payload_hash = Column(String, nullable=False)
    idempotency_key = Column(String, nullable=False, unique=True)
    status = Column(String, nullable=False)
    external_ref = Column(String, nullable=True)
    confirmed_by = Column(String, nullable=True)
```

Repository 每个查询都必须同时过滤 `tenant_id` 和 `user_id`。演示数据固定为三个订单：运输中、已签收且可退、已激活且不可退；不得使用真实姓名、电话或地址。

- [ ] **Step 5: 实现四项业务工具**

`runtime/ecommerce_tools.py` 为每个工具声明独立 Pydantic Args；`return.create` 标为 WRITE，并把 `ToolContext.idempotency_key` 传给 Service。资格检查必须先于创建；不允许由模型传入 `tenant_id` 或 `user_id`，二者只能来自 `ToolContext`。

- [ ] **Step 6: 运行测试并确认 GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_database_startup.py tests/test_order_after_sales_tools.py -q`

Expected: 全部通过，重复写入始终只有一条记录。

- [ ] **Step 7: 提交**

```powershell
git add db services/order_after_sales_service.py runtime/ecommerce_tools.py tests/test_order_after_sales_tools.py
git commit -m "feat: add idempotent order after-sales tools"
```

### Task 4: 新增订单售后 Agent 并接入中心路由

**Files:**
- Create: `agents/order_after_sales_agent.py`
- Modify: `agents/task_classification_agent.py`
- Modify: `agents/task_classification/task_classifier.py`
- Modify: `agents/task_classification/agent_router.py`
- Modify: `agents/task_classification/classification_processor.py`
- Modify: `agents/task_classification/state_manager.py`
- Modify: `config/constants.py`
- Modify: `api/chat_handler.py`
- Test: `tests/test_order_after_sales_agent.py`
- Modify: `tests/test_session_isolation.py`

**Interfaces:**
- Consumes: Task 2 的 `BoundedAgentRuntime`、Task 3 注册的四项工具、现有按 `session_id` 隔离的 Agent Registry。
- Produces: `OrderAfterSalesAgent.run_stream(message)`；路由类别 `order_after_sales`；状态 `StateEnum.ORDER_AFTER_SALES`。

- [ ] **Step 1: 写失败测试，固定路由边界**

```python
@pytest.mark.parametrize("message", [
    "查询订单 JP20260919001 的物流",
    "订单 JP20260919002 能退货吗",
    "申请退货 JP20260919002，原因是商品破损",
])
def test_order_messages_route_to_order_after_sales(message):
    events = asyncio.run(run_router_with_fake_classifier("order_after_sales", message))
    assert events[0].data["route"] == "order_after_sales"

def test_policy_question_stays_in_knowledge_agent():
    assert classify_with_fake("七天无理由退货有什么规则") == "knowledge"
```

- [ ] **Step 2: 写失败测试，固定多轮确认与参数冻结**

```python
def test_return_request_requires_confirmation_before_write():
    agent = make_agent()
    first = asyncio.run(collect(agent.run_stream("申请退货 JP20260919002，商品破损")))
    assert event(first, "confirmation_required")["order_id"] == "JP20260919002"
    assert fake_service(agent).count_return_requests() == 0

def test_confirmation_executes_the_frozen_action_once():
    agent = make_agent()
    asyncio.run(collect(agent.run_stream("申请退货 JP20260919002，商品破损")))
    answer = asyncio.run(collect(agent.run_stream("确认提交")))
    assert "退货申请" in public_answer(answer)
    assert fake_service(agent).count_return_requests() == 1
```

- [ ] **Step 3: 运行测试并确认 RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_order_after_sales_agent.py -q`

Expected: FAIL，提示 `OrderAfterSalesAgent` 不存在。

- [ ] **Step 4: 实现订单售后 Planner 与 Agent**

首版 Planner 使用确定性意图与实体提取保证可演示性：订单号匹配 `JP\d{11}`；“物流/到哪”选择 `logistics.get`；“能否退/退货条件”先 `return.check`；“申请退货”依次执行 `return.check → confirmation_required → return.create`；缺订单号或退货原因时返回 `needs_input`。未知表达可以调用现有 `deepseek-flash` 做结构化分类，但模型输出只允许映射到上述四类动作，不能直接执行工具。

Agent 在会话内保存 `PendingAction(tool_name, frozen_arguments, payload_hash, idempotency_key)`；仅“确认/确认提交”执行，用户补充或修改参数时废弃旧 PendingAction 并重新生成确认摘要。

- [ ] **Step 5: 接入中心路由并保持构造兼容**

`TaskClassificationAgent.__init__(appointment_agent, consultant_agent, order_after_sales_agent=None)` 保持旧测试与调用方可用。分类类别改为 `appointment`、`knowledge`、`order_after_sales`、`pay`、`statistics`、`other`；订单、物流进度和具体退货操作进入新 Agent，通用规则仍进入 Knowledge Agent。`_create_task_agent(session_id)` 为每个会话创建独立的 OrderAfterSalesAgent。

- [ ] **Step 6: 运行测试并确认 GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_order_after_sales_agent.py tests/test_session_isolation.py tests/test_stream_protocol.py -q`

Expected: 全部通过；A 会话的待确认退货不得被 B 会话确认。

- [ ] **Step 7: 提交**

```powershell
git add agents config/constants.py api/chat_handler.py tests/test_order_after_sales_agent.py tests/test_session_isolation.py
git commit -m "feat: route order after-sales agent"
```

### Task 5: 将 Runtime/Tool 事件安全展示到前端并完成离线验收

**Files:**
- Modify: `api/stream_protocol.py`
- Modify: `web/static/ecommerce-agent.js`
- Modify: `web/templates/index.html`
- Modify: `web/static/ecommerce-agent.css`
- Modify: `README.md`
- Test: `tests/test_stream_protocol.py`
- Test: `tests/test_ecommerce_frontend.py`
- Create: `tests/test_order_after_sales_e2e.py`

**Interfaces:**
- Consumes: Task 1 的 RuntimeEvent、Task 4 的订单售后流和现有 `/api/chat/stream`。
- Produces: 公共 SSE 事件 `route_selected`、`tool_started`、`tool_finished`、`confirmation_required`、`answer_delta`、`turn_ended/turn_failed`；管理员 Trace 仍留待阶段四。

- [ ] **Step 1: 写失败测试，固定公共事件白名单与异常脱敏**

```python
def test_tool_events_expose_name_and_status_but_not_arguments():
    frames = collect_events(runtime_tokens_with_private_order_payload())
    started = first(frames, "tool_started")
    assert started["tool"] == "logistics.get"
    assert "arguments" not in started
    assert "phone" not in json.dumps(frames)

def test_tool_failure_never_exposes_internal_exception():
    frames = collect_events(failing_tool_tokens("SQLITE_PRIVATE_ERROR"))
    assert last_event(frames) == "turn_failed"
    assert "SQLITE_PRIVATE_ERROR" not in json.dumps(frames)
```

- [ ] **Step 2: 写失败端到端测试，覆盖查询、确认、幂等和跨会话隔离**

```python
def test_order_and_return_flow_with_two_sessions(test_client, seeded_db):
    logistics = sse(test_client, "A", "查询订单 JP20260919001 的物流")
    pending = sse(test_client, "A", "申请退货 JP20260919002，商品破损")
    foreign_confirm = sse(test_client, "B", "确认提交")
    confirmed = sse(test_client, "A", "确认提交")
    repeated = sse(test_client, "A", "确认提交")

    assert route(logistics) == "order_after_sales"
    assert has_event(pending, "confirmation_required")
    assert public_answer(foreign_confirm) == "当前没有待确认操作"
    assert "退货申请" in public_answer(confirmed)
    assert public_answer(repeated) == "当前没有待确认操作"
    assert seeded_db.count_return_requests() == 1
```

- [ ] **Step 3: 运行测试并确认 RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_stream_protocol.py tests/test_ecommerce_frontend.py tests/test_order_after_sales_e2e.py -q`

Expected: 新的工具与确认事件断言失败。

- [ ] **Step 4: 扩展 SSE 安全适配器**

只允许向浏览器发送工具名、状态、耗时、公开摘要和业务引用号；订单详情、地址、手机号、工具参数、模型提示和异常栈均不得进入 SSE。旧 `[THOUGHT]/[REPLY]/[ERROR]` 协议继续兼容。

- [ ] **Step 5: 更新工作台展示**

订单物流快捷问题显示真实 `order_after_sales` 路由；时间线展示“查询订单”“查询物流”“核验退货条件”“等待用户确认”“已创建退货申请”等真实事件。确认仍通过普通对话输入完成，不增加绕过后端门禁的前端写按钮。普通用户只看阶段摘要，不展示内部参数或推理。

- [ ] **Step 6: 更新 README 的已实现边界**

把“订单与售后工具、统一 Runtime”从规划中移到已实现，并明确：数据为匿名 Mock；状态为单进程、SQLite 演示实现；HermesRAG 适配、长期记忆、完整评测平台和后训练仍未实现。

- [ ] **Step 7: 运行聚焦与回归测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_database_startup.py tests/test_ecommerce_domain.py tests/test_ecommerce_frontend.py tests/test_model_provider.py tests/test_session_isolation.py tests/test_stream_protocol.py tests/test_runtime_contracts.py tests/test_tool_runtime.py tests/test_order_after_sales_tools.py tests/test_order_after_sales_agent.py tests/test_order_after_sales_e2e.py -q`

Expected: 全部通过；不要求旧按摩领域和未实现用户行为测试转绿，但必须单独报告其现状。

- [ ] **Step 8: 浏览器验收**

Run: `.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8001`

依次验证：

1. `查询订单 JP20260919001 的物流` 返回运输节点并展示两次只读工具事件。
2. `申请退货 JP20260919002，原因是商品破损` 只产生确认摘要，不写数据库。
3. `确认提交` 只创建一个退货申请并显示业务编号。
4. 再次发送 `确认提交` 不重复创建。
5. 新对话发送 `确认提交` 看不到旧会话 PendingAction。
6. `耳机保修期多久？` 与上门预约流程保持原有结果。

- [ ] **Step 9: 提交**

```powershell
git add api/stream_protocol.py web README.md tests/test_stream_protocol.py tests/test_ecommerce_frontend.py tests/test_order_after_sales_e2e.py
git commit -m "feat: expose order after-sales runtime trace"
```

## Completion Gate

- 四项订单售后工具均通过 Schema 校验、租户/用户过滤和离线测试。
- 退货写入必须经过参数冻结、确认和幂等键；重复确认不会重复创建。
- 新 Agent 受 6 步、8 次工具、2 次重规划硬限制约束。
- 两个会话的 PendingAction、工具历史和确认状态互不影响。
- 前端只显示允许公开的 Runtime/Tool 事件，不显示推理、完整参数或内部异常。
- 现有知识咨询、预约、SSE、模型配置和会话隔离测试继续通过。
- README 与简历草稿只把真实实现标为完成；本计划不填写模拟性能指标。
