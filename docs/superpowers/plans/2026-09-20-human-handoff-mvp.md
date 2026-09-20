# Human Handoff MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将用户明确要求人工客服的请求转换为可幂等、可持久化、可追踪的人工接管工单，并通过安全 SSE 事件和本地管理员页展示。

**Architecture:** 保留现有中心路由式架构；`ClassificationProcessor` 在调用 LLM 分类前只对“明确要求人工”做确定性短路由。`HumanHandoffAgent` 使用现有 `BoundedAgentRuntime` 调用 `handoff.create` 控制类工具，`HandoffService` 以 `(tenant_id, turn_id)` 保证幂等落库。用户仅看到工单号和接管状态；管理页仅展示脱敏摘要，不展示原始消息、隐藏推理或工具参数。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, Pydantic v2, SQLite, SSE, pytest

**Spec:** `docs/superpowers/specs/2026-09-17-ecommerce-agent-architecture-design.md` 第 3、4.3、4.5、5.8、9、10 节

## Global Constraints

- 不调用私有网络服务，所有测试默认离线。
- 不在普通用户 SSE 或管理页暴露原始消息、地址、电话、工具参数、Prompt 或隐藏思维链。
- 人工接管是内部控制面写入，不等同于退款、取消订单等外部业务写入，不需要二次确认，但必须幂等。
- 明确人工请求必须在任何领域流程状态下生效，不允许被当前预约或退货状态吞掉。
- 本计划不实现客服坐席分配、工单回复、通知通道或生产 JWT/RBAC；只实现本地演示可验证的接管创建和安全投影。
- 不修改 `.reference_private/`，不推送 GitHub。

## Review Focus

- 重放同一 `turn_id` 时必须返回同一工单，不能重复写入。
- 不同租户或用户不能读取对方的接管工单。
- 当前会话处于退货待确认状态时，“转人工”仍必须优先执行，且不得执行 `return.create`。
- 工具或数据库失败时不得伪造工单号，必须输出安全失败终态。
- SSE 和管理页只能展示白名单字段；即使存储层含有敏感测试字段，也不能投影出去。

---

### Task 1: 建立人工接管工单的幂等持久化

**Files:**
- Modify: `db/models.py`
- Create: `db/repositories/handoff_repository.py`
- Create: `services/handoff_service.py`
- Create: `tests/test_human_handoff.py`

**Interfaces:**
- Produces: `HandoffService.create_or_get(tenant_id, user_id, session_id, turn_id, reason_code, summary, verified_facts, evidence_refs, failed_steps) -> dict`
- Produces: `HandoffService.list_recent(tenant_id, limit=20) -> list[dict]`
- Consumes: existing `SessionManager.session_scope()` and SQLAlchemy `Base.metadata.create_all()` migration behavior.

- [ ] **Step 1: Write the failing persistence tests**

```python
def test_create_or_get_handoff_is_idempotent_and_tenant_scoped(tmp_path):
    service = HandoffService(f"sqlite:///{tmp_path / 'handoff.db'}")
    first = service.create_or_get(
        tenant_id="tenant-a", user_id="user-a", session_id="session-a",
        turn_id="turn-1", reason_code="user_requested",
        summary="用户明确请求人工客服", verified_facts=[],
        evidence_refs=[], failed_steps=[],
    )
    second = service.create_or_get(
        tenant_id="tenant-a", user_id="user-a", session_id="session-a",
        turn_id="turn-1", reason_code="user_requested",
        summary="重复投递不应改写", verified_facts=[],
        evidence_refs=[], failed_steps=[],
    )
    assert first["ticket_no"] == second["ticket_no"]
    assert service.count("tenant-a") == 1
    assert service.list_recent("tenant-b") == []


def test_handoff_rejects_unbounded_or_sensitive_summary(tmp_path):
    service = HandoffService(f"sqlite:///{tmp_path / 'handoff.db'}")
    with pytest.raises(ValueError, match="summary"):
        service.create_or_get(
            tenant_id="tenant-a", user_id="user-a", session_id="session-a",
            turn_id="turn-1", reason_code="user_requested",
            summary="13800138000" * 30, verified_facts=[],
            evidence_refs=[], failed_steps=[],
        )
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/Scripts/python.exe -m pytest tests/test_human_handoff.py -q`

Expected: FAIL because `HandoffService` and `HumanHandoffTicket` do not exist.

- [ ] **Step 3: Implement the model, repository, and service**

Add `HumanHandoffTicket` with a unique constraint on `(tenant_id, turn_id)` and fields: `ticket_no`, `tenant_id`, `user_id`, `session_id`, `turn_id`, `reason_code`, `summary`, `verified_facts_json`, `evidence_refs_json`, `failed_steps_json`, `status`, `created_at`, `updated_at`.

`HandoffRepository.create_or_get(...)` must query the scoped unique key before insert and recover from a uniqueness race by re-reading the existing row. `list_recent()` must always filter by `tenant_id`, order newest first, and return no raw ORM objects. `HandoffService` must allow only reason codes `user_requested`, `evidence_insufficient`, `risk_threshold`, `tool_failure`, and `intent_unstable`; summary length is `1..240`, each list has at most 20 scalar strings, and phone/email-like content is rejected from `summary`.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `.venv/Scripts/python.exe -m pytest tests/test_human_handoff.py tests/test_database_startup.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add db/models.py db/repositories/handoff_repository.py services/handoff_service.py tests/test_human_handoff.py
git commit -m "feat: persist idempotent human handoffs"
```

### Task 2: 将人工接管建模为有界 Runtime 控制工具

**Files:**
- Modify: `runtime/contracts.py`
- Modify: `runtime/tools.py`
- Modify: `runtime/loop.py`
- Create: `runtime/handoff.py`
- Modify: `tests/test_human_handoff.py`

**Interfaces:**
- Consumes: `HandoffService.create_or_get(...)` from Task 1.
- Produces: `TurnStatus.HANDED_OFF`, `ToolRisk.CONTROL`, `register_handoff_tool(registry, service)`, and `ToolResult.status == "handed_off"`.

- [ ] **Step 1: Write the failing Runtime tests**

```python
def test_handoff_control_tool_needs_no_second_confirmation_but_is_idempotent(tmp_path):
    service = HandoffService(f"sqlite:///{tmp_path / 'handoff.db'}")
    registry = ToolRegistry()
    register_handoff_tool(registry, service)
    context = ToolContext(
        tenant_id="tenant-a", user_id="user-a", session_id="session-a",
        turn_id="turn-1", idempotency_key="handoff-turn-1",
    )
    result = asyncio.run(registry.execute(
        "handoff.create",
        {"reason_code": "user_requested", "summary": "用户明确请求人工客服"},
        context,
    ))
    assert result.status == "handed_off"
    assert result.data["ticket_no"].startswith("HO")
    assert service.count("tenant-a") == 1


def test_bounded_runtime_stops_with_handed_off_terminal_status(tmp_path):
    # A one-action planner invokes handoff.create.
    run = asyncio.run(runtime.run(turn, planner, context))
    assert run.outcome.status == TurnStatus.HANDED_OFF
    assert run.outcome.tool_calls == 1
    assert run.events[-1].data["status"] == "handed_off"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/Scripts/python.exe -m pytest tests/test_human_handoff.py -q`

Expected: FAIL because the control risk, handoff tool, result status, and terminal status are absent.

- [ ] **Step 3: Implement minimal Runtime support**

Add `CONTROL` to `ToolRisk`; it bypasses business-write confirmation but still requires a non-empty `ToolContext.idempotency_key`. Extend `ToolResult.status` with `handed_off`. `register_handoff_tool()` validates this input shape:

```python
class HandoffArgs(BaseModel):
    reason_code: Literal[
        "user_requested", "evidence_insufficient", "risk_threshold",
        "tool_failure", "intent_unstable",
    ]
    summary: str = Field(min_length=1, max_length=240)
    verified_facts: list[str] = Field(default_factory=list, max_length=20)
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)
    failed_steps: list[str] = Field(default_factory=list, max_length=20)
```

The tool calls `HandoffService.create_or_get()` with identity from `ToolContext`. `BoundedAgentRuntime` maps `handed_off` directly to `TurnStatus.HANDED_OFF`, records `turn_finished`, and must not ask the planner for another action.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `.venv/Scripts/python.exe -m pytest tests/test_human_handoff.py tests/test_runtime_contracts.py tests/test_tool_runtime.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add runtime/contracts.py runtime/tools.py runtime/loop.py runtime/handoff.py tests/test_human_handoff.py
git commit -m "feat: add bounded handoff control tool"
```

### Task 3: 接入明确转人工路由与安全 SSE 投影

**Files:**
- Create: `agents/human_handoff_agent.py`
- Modify: `agents/task_classification_agent.py`
- Modify: `agents/task_classification/classification_processor.py`
- Modify: `agents/task_classification/agent_router.py`
- Modify: `api/chat_handler.py`
- Modify: `api/stream_protocol.py`
- Modify: `web/static/ecommerce-agent.js`
- Modify: `tests/test_human_handoff.py`
- Modify: `tests/test_stream_protocol.py`
- Modify: `tests/test_ecommerce_frontend.py`

**Interfaces:**
- Consumes: Task 2 `handoff.create` and `TurnStatus.HANDED_OFF`.
- Produces: internal event `handoff_created` with `ticket_no`, `reason_code`, `status`; public SSE event with the same allowlisted fields plus `turn_id`.
- Produces: `HumanHandoffAgent.run_stream(message) -> AsyncGenerator[str, None]`.

- [ ] **Step 1: Write failing routing and state-priority tests**

```python
def test_explicit_human_request_bypasses_llm_and_active_return_flow(tmp_path):
    # Build a real ClassificationProcessor with a classifier that raises if called.
    tokens = asyncio.run(collect(processor.process_task_stream("请转人工客服")))
    assert "handoff_created" in tokens
    assert service.count("demo") == 1
    assert order_service.count_return_requests() == 0


def test_handoff_failure_does_not_fabricate_ticket_number():
    tokens = asyncio.run(collect(failing_agent.run_stream("转人工")))
    assert "HO" not in tokens
    assert "暂时无法创建人工接管工单" in tokens
```

The deterministic matcher accepts `转人工`, `人工客服`, `真人客服`, `人工处理`, and `客服介入`; it must not match informational phrases such as `人工客服上班时间`.

- [ ] **Step 2: Write failing SSE and frontend tests**

```python
def test_handoff_event_projects_only_safe_fields():
    events = decode(asyncio.run(collect_sse([
        '[EVENT]{"type":"handoff_created","data":'
        '{"ticket_no":"HO0001","reason_code":"user_requested",'
        '"status":"open","raw_message":"secret"}}'
    ])))
    payload = next(data for name, data in events if name == "handoff_created")
    assert payload == {
        "turn_id": "turn-1", "ticket_no": "HO0001",
        "reason_code": "user_requested", "status": "open",
    }
```

Add a frontend unit assertion that `describeRuntimeEvent('handoff_created', ...)` returns title `已转人工客服` and detail containing only the ticket number.

- [ ] **Step 3: Run tests and verify RED**

Run: `.venv/Scripts/python.exe -m pytest tests/test_human_handoff.py tests/test_stream_protocol.py tests/test_ecommerce_frontend.py -q`

Expected: FAIL on missing agent, deterministic route, SSE allowlist, and frontend event description.

- [ ] **Step 4: Implement the Agent and route**

`HumanHandoffAgent` creates one `TurnRequest`, invokes a one-action planner for `handoff.create`, yields its safe Runtime events, then yields a user answer containing only the ticket number. `ClassificationProcessor` checks `is_explicit_handoff_request(task)` before `should_classify()` so active order/appointment flows cannot shadow it. `AgentRouter.route_to_handoff()` resets the domain state after the handoff terminates. `api.chat_handler` creates one shared `HandoffService` and injects a per-session `HumanHandoffAgent`.

- [ ] **Step 5: Implement SSE and frontend projection**

Allowlist only `ticket_no`, `reason_code`, and `status` for `handoff_created`; map it to a visible timeline row without exposing the summary. A handed-off turn ends with public `turn_ended.status == "handed_off"`, not `completed` or `failed`.

- [ ] **Step 6: Run tests and verify GREEN**

Run: `.venv/Scripts/python.exe -m pytest tests/test_human_handoff.py tests/test_stream_protocol.py tests/test_ecommerce_frontend.py tests/test_order_after_sales_e2e.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add agents/human_handoff_agent.py agents/task_classification_agent.py agents/task_classification/classification_processor.py agents/task_classification/agent_router.py api/chat_handler.py api/stream_protocol.py web/static/ecommerce-agent.js tests/test_human_handoff.py tests/test_stream_protocol.py tests/test_ecommerce_frontend.py
git commit -m "feat: route explicit requests to human handoff"
```

### Task 4: 在本地管理员观测页展示脱敏接管工单

**Files:**
- Modify: `services/observability_service.py`
- Modify: `web/routes.py`
- Modify: `web/templates/observability.html`
- Modify: `web/static/observability.css`
- Modify: `tests/test_observability_dashboard.py`
- Modify: `tests/test_human_handoff.py`

**Interfaces:**
- Consumes: `HandoffService.list_recent(tenant_id="demo", limit=20)`.
- Produces: `snapshot["handoffs"]` and `snapshot["handoff_summary"]` containing only ticket number, reason label, status, and timestamps.

- [ ] **Step 1: Write failing observability projection tests**

```python
def test_observability_projects_handoffs_without_raw_summary(tmp_path):
    snapshot = ObservabilityService(
        journal=FakeJournal(), reports_dir=tmp_path,
        handoff_reader=FakeHandoffReader(),
    ).snapshot()
    assert snapshot["handoff_summary"] == {"total": 1, "open": 1}
    assert snapshot["handoffs"][0]["ticket_no"] == "HO0001"
    serialized = json.dumps(snapshot, ensure_ascii=False)
    assert "13800138000" not in serialized
    assert "raw_message" not in serialized
```

Add route rendering coverage with one handoff and verify the page has a `人工接管` section and no raw summary.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/Scripts/python.exe -m pytest tests/test_observability_dashboard.py tests/test_human_handoff.py -q`

Expected: FAIL because `ObservabilityService` has no handoff reader or safe projection.

- [ ] **Step 3: Implement safe admin projection**

Inject an optional `handoff_reader` into `ObservabilityService`; absence returns an empty handoff state so existing callers remain compatible. Map reason codes to fixed Chinese labels server-side. Update `/admin/observability` to use the shared `HandoffService`; render a table with ticket number, reason label, status, and creation time only. Keep the existing local-client restriction and `Cache-Control: no-store`.

- [ ] **Step 4: Run focused and maintained suites**

Run: `.venv/Scripts/python.exe -m pytest tests/test_human_handoff.py tests/test_observability_dashboard.py tests/test_stream_protocol.py tests/test_ecommerce_frontend.py tests/test_order_after_sales_e2e.py tests/test_session_isolation.py -q`

Expected: PASS.

Run: `.venv/Scripts/python.exe -m pytest -q`

Expected: no new failures compared with the recorded baseline of 22 historical failures; report the exact pass/fail counts.

- [ ] **Step 5: Live verification**

Start the server and send `请转人工客服` to `POST /api/chat/stream` with a fresh `session_id`. Verify the stream contains exactly one `handoff_created`, a terminal `turn_ended` with `status=handed_off`, no raw summary, and the same ticket appears on `/admin/observability` after refresh.

- [ ] **Step 6: Commit**

```powershell
git add services/observability_service.py web/routes.py web/templates/observability.html web/static/observability.css tests/test_observability_dashboard.py tests/test_human_handoff.py
git commit -m "feat: expose safe handoff observability"
```

## Completion Contract

- 明确转人工请求不调用 LLM 分类，且在任何活跃领域流中优先终止并接管。
- 同一 `tenant_id + turn_id` 最多产生一条工单。
- Runtime 终态、SSE 终态和管理页状态都为 `handed_off`，不伪装成成功答复。
- 工单失败不返回伪造编号；用户和管理页都不暴露原始请求、工具参数或隐藏推理。
- 聚焦测试全绿；全量测试无新失败；在线 SSE 和管理页用同一工单号完成交叉验证。

