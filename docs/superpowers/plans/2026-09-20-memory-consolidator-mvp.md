# Memory Consolidator MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不阻塞用户回答、不保存原始聊天和不改写业务事实的前提下，把已结束任务转成可追溯、可幂等的 Episodic/Profile Memory 候选并安全落库。

**Architecture:** 在现有 `MemoryManager` 之上增加独立 `MemoryConsolidator`，输入为最小化 `TurnCompletion`，输出为白名单化 `MemoryCandidate`。首版使用确定性规则提取器并保留可注入的 Extractor 协议；后台 Dispatcher 只消费成功结束或明确中止的 Turn，异步执行且失败仅记录内部日志。Episodic Memory 用稳定事件键防重复，Profile Memory 仅接受用户显式表达；行为推断仍保持关闭。

**Tech Stack:** Python 3.12, Pydantic v2, SQLAlchemy, SQLite, asyncio, pytest

**Spec:** `docs/superpowers/specs/2026-09-17-ecommerce-agent-architecture-design.md` 第 4.3、6.1—6.7、9、10 节

## Global Constraints

- 不修改或读取 `.reference_private/` 中的实现，不调用私有网络服务。
- Consolidator 不是业务事实源；订单、物流、退款、预约状态仍由业务工具回源查询。
- 不保存原始聊天、完整回答、电话、详细地址、支付凭证、证件号、银行卡号或 API 凭证。
- 后台沉淀失败不得改变 Turn 终态、延迟最终回答或重放业务写操作。
- 同一 `tenant_id + user_id + turn_id + candidate_type + candidate_key` 重放只能产生同一条记忆。
- 首版只写入任务事件和用户显式偏好；“根据行为推断偏好”不进入本计划。
- 所有测试离线运行，不新增生产依赖，不执行 `git push`。

## Review Focus

- 流式请求断开、失败或仍等待用户输入时，不得产生“任务已完成”的长期记忆。
- 同一完成事件被重复调度时只落一条记忆，且不能覆盖来自另一用户或租户的记录。
- 用户消息含手机号、完整地址或支付信息时，候选必须被拒绝而不是脱敏后猜测写入。
- 提取器或数据库抛异常时，主回答照常完成，Dispatcher 能回收任务且不泄漏异常内容。
- 明确偏好被更新时走现有 `superseded_by` 版本链；普通一次性表达不得被误写为稳定偏好。

---

### Task 1: 定义任务结束与候选记忆契约

**Files:**
- Create: `services/memory_consolidator.py`
- Create: `tests/test_memory_consolidator.py`

**Interfaces:**
- Produces: `TurnCompletion`, `MemoryCandidate`, `MemoryCandidateExtractor`, `RuleBasedMemoryCandidateExtractor`.
- Consumes: Pydantic v2 and the existing `MemoryManager` public interface.

- [ ] **Step 1: Write failing contract and extraction tests**

```python
def test_rule_extractor_emits_minimal_return_event_without_raw_chat():
    completion = TurnCompletion(
        tenant_id="demo", user_id="user-a", session_id="session-a",
        turn_id="turn-1", status="completed", route="order_after_sales",
        user_message="订单 JP20260920001 退货已确认",
        public_result="退货申请已提交，申请编号为 AS-001。",
    )
    candidates = RuleBasedMemoryCandidateExtractor().extract(completion)
    assert candidates[0].event_type == "return_requested"
    assert candidates[0].entity_refs == ("JP20260920001",)
    assert "退货申请已提交" in candidates[0].summary
    assert "用户消息" not in candidates[0].model_dump_json()


@pytest.mark.parametrize("status", ["failed", "needs_input"])
def test_non_terminal_success_status_has_no_candidates(status):
    completion = completion_fixture(status=status)
    assert RuleBasedMemoryCandidateExtractor().extract(completion) == ()


def test_explicit_preference_requires_stable_language():
    completion = completion_fixture(
        user_message="以后上门维修尽量安排在上午",
        route="service_appointment",
    )
    candidate = RuleBasedMemoryCandidateExtractor().extract(completion)[0]
    assert candidate.kind == "profile"
    assert candidate.memory_key == "service_time_preference"
    assert candidate.memory_value == "morning"
    assert candidate.source_type == "explicit"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/Scripts/python.exe -m pytest tests/test_memory_consolidator.py -q`

Expected: FAIL because the new contracts and extractor do not exist.

- [ ] **Step 3: Implement frozen contracts and the first rule extractor**

```python
class TurnCompletion(BaseModel):
    model_config = ConfigDict(frozen=True)
    tenant_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    turn_id: str = Field(min_length=1, max_length=128)
    status: Literal["completed", "cancelled", "handed_off", "failed", "needs_input"]
    route: Literal[
        "knowledge_consultation", "order_after_sales",
        "service_appointment", "human_handoff", "unsupported",
    ]
    user_message: str = Field(max_length=2000)
    public_result: str = Field(max_length=2000)


class MemoryCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["episodic", "profile"]
    candidate_key: str = Field(min_length=1, max_length=128)
    event_type: str | None = None
    summary: str | None = Field(default=None, max_length=240)
    outcome: str | None = None
    entity_refs: tuple[str, ...] = ()
    memory_key: str | None = None
    memory_value: str | None = None
    source_type: Literal["explicit"] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
```

The extractor returns no candidate for `failed` or `needs_input`; recognizes only validated order IDs, returned after-sales references, appointment IDs, handoff ticket IDs, `以后/今后/以后都` preference phrases, and fixed profile keys already used by `ContextEngine`. It never copies the full user message or full model answer into a candidate.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `.venv/Scripts/python.exe -m pytest tests/test_memory_consolidator.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add services/memory_consolidator.py tests/test_memory_consolidator.py
git commit -m "feat: define safe memory consolidation candidates"
```

### Task 2: 实现敏感信息门禁与幂等落库

**Files:**
- Modify: `db/repositories/memory_repository.py`
- Modify: `services/memory_manager.py`
- Modify: `services/memory_consolidator.py`
- Modify: `tests/test_memory_consolidator.py`
- Modify: `tests/test_memory_manager.py`

**Interfaces:**
- Consumes: Task 1 `TurnCompletion` and `MemoryCandidate`.
- Produces: `MemoryManager.record_event_once(...) -> dict`, `MemoryConsolidator.consolidate(completion) -> ConsolidationResult`.

- [ ] **Step 1: Write failing idempotency, tenant, and safety tests**

```python
def test_consolidation_is_idempotent_per_scoped_candidate(tmp_path):
    manager = MemoryManager(sqlite_url(tmp_path))
    consolidator = MemoryConsolidator(manager)
    first = consolidator.consolidate(return_completion())
    second = consolidator.consolidate(return_completion())
    assert first.written == 1
    assert second.written == 0
    assert manager.recall_events("demo", "user-a", limit=3) == first.memories


@pytest.mark.parametrize("unsafe", [
    "手机号 13800138000", "银行卡 6222020202020202",
    "地址 北京市朝阳区某街道 88 号 2 单元 301",
])
def test_sensitive_candidate_is_rejected(tmp_path, unsafe):
    extractor = FixedExtractor(profile_candidate(memory_value=unsafe))
    result = MemoryConsolidator(MemoryManager(sqlite_url(tmp_path)), extractor).consolidate(
        completion_fixture()
    )
    assert result.written == 0
    assert result.rejected == 1
```

Add a tenant-isolation assertion that identical `turn_id` and candidate key under two tenants produce two scoped records and neither tenant recalls the other tenant's record.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/Scripts/python.exe -m pytest tests/test_memory_consolidator.py tests/test_memory_manager.py -q`

Expected: FAIL on missing `record_event_once` and `MemoryConsolidator` behavior.

- [ ] **Step 3: Add deterministic event identity and safe persistence**

`MemoryManager.record_event_once()` computes a stable event ID from only scoped metadata:

```python
material = "\x1f".join([
    tenant_id, user_id, source_trace_id, event_type, candidate_key,
])
event_id = "mem_evt_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
```

`MemoryRepository.record_event_once()` first queries by primary key, inserts if absent, and on `IntegrityError` re-reads the same scoped row. The returned object includes `created: bool`; another tenant or user can never reuse an existing event row.

`MemoryConsolidator` must validate candidates through a fixed allowlist:

- episodic types: `return_requested`, `service_booked`, `handoff_created`;
- profile keys: `service_time_preference`, `communication_language`, `address_region_ref`, `product_category_preference`;
- summaries `1..240`, scalar profile values `1..80`, entity refs at most 8;
- reject phone, email, bank-card-like digit runs, identity-card-like values, credential markers, and detailed-address patterns;
- write profiles only with `source_type="explicit"` and confidence `1.0`.

Return only counts plus persisted safe records:

```python
class ConsolidationResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    written: int
    skipped: int
    rejected: int
    memories: tuple[dict, ...] = ()
```

- [ ] **Step 4: Run tests and verify GREEN**

Run: `.venv/Scripts/python.exe -m pytest tests/test_memory_consolidator.py tests/test_memory_manager.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add db/repositories/memory_repository.py services/memory_manager.py services/memory_consolidator.py tests/test_memory_consolidator.py tests/test_memory_manager.py
git commit -m "feat: persist consolidated memories idempotently"
```

### Task 3: 在流式 Turn 结束后异步调度沉淀

**Files:**
- Modify: `api/chat_handler.py`
- Modify: `api/stream_protocol.py`
- Modify: `web/routes.py`
- Modify: `tests/test_stream_protocol.py`
- Modify: `tests/test_memory_consolidator.py`
- Modify: `tests/test_session_isolation.py`

**Interfaces:**
- Consumes: Task 2 `MemoryConsolidator.consolidate()`.
- Produces: `MemoryConsolidationDispatcher.submit(completion)`, `await dispatcher.drain()` for deterministic tests, and an internal terminal callback from `iter_sse_events`.

- [ ] **Step 1: Write failing terminal-hook and failure-isolation tests**

```python
def test_completed_stream_submits_one_minimal_completion():
    dispatcher = RecordingDispatcher()
    frames = asyncio.run(collect_sse(
        iter_sse_events(tokens(), "turn-1", on_terminal=dispatcher.submit)
    ))
    assert decode(frames)[-1][1]["status"] == "completed"
    assert len(dispatcher.items) == 1
    assert dispatcher.items[0].turn_id == "turn-1"


def test_failed_or_needs_input_stream_is_not_persisted_as_completed():
    # Feed ERROR and confirmation_required streams separately.
    # Assert their completion statuses are failed/needs_input and produce no
    # completed episodic candidate.


def test_dispatcher_failure_never_changes_public_stream():
    dispatcher = FailingDispatcher()
    events = asyncio.run(collect_sse(iter_sse_events(tokens(), "turn-1", on_terminal=dispatcher.submit)))
    assert decode(events)[-1] == ("turn_ended", {"turn_id": "turn-1", "status": "completed"})
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/Scripts/python.exe -m pytest tests/test_stream_protocol.py tests/test_memory_consolidator.py tests/test_session_isolation.py -q`

Expected: FAIL because the stream has no typed terminal callback or dispatcher.

- [ ] **Step 3: Implement a bounded background dispatcher**

`MemoryConsolidationDispatcher` owns a set of pending `asyncio.Task` objects, caps it at 100 tasks, runs synchronous SQLite consolidation with `asyncio.to_thread`, removes completed tasks in a done callback, and logs only exception type plus `turn_id`. `submit()` returns `False` when the queue is full; it never raises into the response path. `drain()` exists only as a deterministic lifecycle/test helper.

Extend `iter_sse_events()` with an optional `on_terminal(status, public_result)` callback. The adapter already knows the authoritative public terminal status; it collects at most 2,000 characters from `answer_delta`, invokes the callback exactly once before emitting `turn_ended`, and invokes it with `failed` on `turn_failed`. The callback receives no tool arguments, hidden thoughts, raw runtime events, or headers.

`web.routes` builds the `TurnCompletion` with server-side `tenant_id="demo"`, `user_id="user-a"`, validated `session_id`, `turn_id`, normalized route and current message. The dispatcher is injected from `api.chat_handler`; callback errors are caught and do not alter SSE frames.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `.venv/Scripts/python.exe -m pytest tests/test_memory_consolidator.py tests/test_memory_manager.py tests/test_stream_protocol.py tests/test_session_isolation.py tests/test_order_memory_integration.py tests/test_ecommerce_frontend.py -q`

Expected: PASS.

- [ ] **Step 5: Run the maintained suite and live check**

Run the maintained 192-test command recorded in the current project audit. Expected: all tests pass with no new warning class.

Live checks:

1. Submit a completed return or appointment turn with a fresh `turn_id`.
2. Verify the answer finishes before consolidation is awaited.
3. Query the same user's memory and verify one minimal event with matching `source_trace_id`.
4. Replay the terminal callback and verify no duplicate row.
5. Send a failed and a needs-input turn and verify neither is stored as a completed event.

- [ ] **Step 6: Commit**

```powershell
git add api/chat_handler.py api/stream_protocol.py web/routes.py services/memory_consolidator.py tests/test_memory_consolidator.py tests/test_stream_protocol.py tests/test_session_isolation.py
git commit -m "feat: consolidate terminal turn memories"
```

## Completion Contract

- 已完成任务可沉淀最小 Episodic Memory；明确稳定偏好可写入 Profile Memory。
- 原始聊天、完整答案、隐藏推理、工具参数和高敏感字段不会进入长期记忆。
- 重放同一 Turn 不重复写入，租户与用户范围严格隔离。
- 失败、等待输入和流式断开不会被伪装成成功记忆。
- 提取或落库失败不改变用户答案和 Turn 终态。
- 聚焦与维护测试全绿；真实页面可演示“任务完成后跨会话召回”，但不声称已完成规模化记忆评测。
