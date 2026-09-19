# 电商售后 Agent 前端 MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 JakePilot 当前的按摩预约首页改造成可演示的电商售后 Agent 工作台，并用真实 SSE 事件展示路由、执行状态和最终回答，不展示隐藏思维链或伪造工具轨迹。

**Architecture:** 保留现有 `/chat/stream` 文本流接口以兼容旧页面，新增 `/api/chat/stream` SSE 适配层，将原有 Agent 输出转换为 `turn_started`、`route_selected`、`answer_delta`、`turn_ended` 和 `turn_failed` 事件。首页改为双栏售后工作台：左侧对话与快捷问题，右侧只展示本次请求实际收到的执行事件；本阶段只暴露真实路由状态，不宣称尚未实现的订单工具、HermesRAG Trace 或三层记忆已经执行。

**Tech Stack:** FastAPI、StreamingResponse、Jinja2、原生 HTML/CSS/JavaScript、pytest

**Spec:** `docs/superpowers/specs/2026-09-17-ecommerce-agent-architecture-design.md`

## Global Constraints

- 保留 JakePilot 现有中心路由式多 Agent 结构和旧 `/chat/stream`、`/chat` 接口。
- 前端不得显示 `[THOUGHT]` 内容、模型隐藏推理或未经后端返回的工具执行记录。
- 本阶段不得声称 HermesRAG、订单、退款、记忆和后训练模块已经接入。
- 不新增生产依赖；测试默认离线，禁止调用外部模型和网络服务。
- 新 SSE 接口的每条消息必须是可独立解析的 JSON，并以 `turn_id` 关联同一轮请求。
- 移动端宽度小于 900px 时改为单栏，聊天输入和执行详情仍可使用。
- 保留现有用户未提交的设计文档和简历草稿，不修改或提交无关文件。

## Review Focus

- Agent 输出把标签与正文放在同一 token 中时，SSE 适配器必须剥离标签且保留正文。
- `[THOUGHT]`、`[SIGNAL]` 和异常字符串不得作为回答内容发送到浏览器。
- 空消息或仅空白消息必须返回 422，不能启动 Agent。
- 浏览器断流或非 2xx 响应时，页面必须结束加载状态并显示可重试错误。
- 窄屏下执行详情必须落到对话区下方，不能遮挡输入框或产生水平滚动。

---

## 文件结构

- `api/stream_protocol.py`：把现有 tagged token 流转换成稳定、可测试的 SSE 事件；不依赖模型实例。
- `web/routes.py`：保留旧接口，新增 `/api/chat/stream` 事件流入口并生成 `turn_id`。
- `web/templates/index.html`：只保留语义化页面结构和资源引用，不再内嵌大段样式与业务脚本。
- `web/static/ecommerce-agent.css`：电商售后工作台的桌面端、移动端和状态样式。
- `web/static/ecommerce-agent.js`：SSE 消费、消息渲染、执行事件渲染、快捷问题和错误恢复。
- `tests/test_stream_protocol.py`：离线验证 token 清洗、事件顺序、异常和终止行为。
- `tests/test_ecommerce_frontend.py`：离线验证页面文案、资源引用、无按摩领域残留及无隐藏思维链渲染。
- `app.py`：将应用描述更新为电商售后 Agent，但不改变启动生命周期。
- `README.md`：补充当前 MVP 的启动方法、真实能力边界和演示问题。

### Task 1: 建立安全的 Agent SSE 事件协议

**Files:**
- Create: `api/stream_protocol.py`
- Create: `tests/test_stream_protocol.py`

**Interfaces:**
- Consumes: `AsyncIterable[str]`，即现有 `ProcessUserInput_stream()` 产生的 token。
- Produces: `async def iter_sse_events(tokens: AsyncIterable[str], turn_id: str) -> AsyncIterator[str]`，输出符合 SSE 格式的字符串；`def encode_sse(event: str, payload: dict) -> str`。

- [ ] **Step 1: Write the failing protocol tests**

```python
import json

import pytest

from api.stream_protocol import iter_sse_events


async def _tokens(*values: str):
    for value in values:
        yield value


def _decode(frame: str) -> tuple[str, dict]:
    lines = frame.strip().splitlines()
    return lines[0].removeprefix("event: "), json.loads(lines[1].removeprefix("data: "))


@pytest.mark.asyncio
async def test_converts_route_and_reply_without_exposing_thought():
    source = _tokens(
        "[THOUGHT][归类机器人] 归类机器人：我发现这是一个预约任务，我将转给预约机器人处理。",
        "[REPLY][预约机器人]请提供上门时间",
        "。",
    )
    events = [_decode(frame) async for frame in iter_sse_events(source, "turn-1")]

    assert [name for name, _ in events] == [
        "turn_started",
        "route_selected",
        "answer_delta",
        "answer_delta",
        "turn_ended",
    ]
    assert events[1][1]["route"] == "service_appointment"
    assert "THOUGHT" not in json.dumps(events, ensure_ascii=False)
    assert "我发现这是" not in json.dumps(events, ensure_ascii=False)


@pytest.mark.asyncio
async def test_internal_signal_is_not_sent_as_answer():
    source = _tokens("[SIGNAL]recommendation_pending", "[REPLY][预约机器人]请确认该时段")
    events = [_decode(frame) async for frame in iter_sse_events(source, "turn-2")]
    answer = "".join(payload.get("delta", "") for _, payload in events)
    assert "SIGNAL" not in answer
    assert answer == "请确认该时段"


@pytest.mark.asyncio
async def test_error_ends_turn_as_failed():
    source = _tokens("[ERROR]预约服务暂时不可用")
    events = [_decode(frame) async for frame in iter_sse_events(source, "turn-3")]
    assert [name for name, _ in events][-1] == "turn_failed"
    assert events[-1][1]["message"] == "预约服务暂时不可用"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_stream_protocol.py -v`

Expected: FAIL during collection because `api.stream_protocol` does not exist.

- [ ] **Step 3: Implement the event encoder and token adapter**

Implement these exact event payloads:

```python
encode_sse("turn_started", {"turn_id": turn_id})
encode_sse("route_selected", {"turn_id": turn_id, "route": "service_appointment", "label": "上门服务预约 Agent"})
encode_sse("route_selected", {"turn_id": turn_id, "route": "knowledge_consultation", "label": "知识咨询 Agent"})
encode_sse("answer_delta", {"turn_id": turn_id, "delta": visible_text})
encode_sse("turn_ended", {"turn_id": turn_id, "status": "completed"})
encode_sse("turn_failed", {"turn_id": turn_id, "status": "failed", "message": error_text})
```

The adapter must discard `[SIGNAL]...`; convert only the two verified router handoff messages to `route_selected`; discard every other `[THOUGHT]...`; strip `[REPLY][角色]` before emitting its visible text; and emit exactly one terminal event.

- [ ] **Step 4: Run protocol tests**

Run: `python -m pytest tests/test_stream_protocol.py -v`

Expected: 3 tests PASS without loading model configuration.

- [ ] **Step 5: Commit the protocol**

```bash
git add api/stream_protocol.py tests/test_stream_protocol.py
git commit -m "feat: add safe agent sse protocol"
```

### Task 2: 暴露兼容的新 SSE 聊天入口

**Files:**
- Modify: `web/routes.py`
- Modify: `tests/test_stream_protocol.py`

**Interfaces:**
- Consumes: `ChatRequest.message: str` and `ProcessUserInput_stream(message)`.
- Produces: `POST /api/chat/stream` with `text/event-stream`, while `/chat/stream` and `/chat` retain their current behavior.

- [ ] **Step 1: Add a failing endpoint-generator test without importing the FastAPI app**

```python
from web.routes import build_agent_event_stream


@pytest.mark.asyncio
async def test_build_agent_event_stream_uses_supplied_processor():
    async def fake_processor(message: str):
        assert message == "查询耳机保修政策"
        yield "[REPLY][咨询机器人]保修期为一年"

    frames = [
        _decode(frame)
        async for frame in build_agent_event_stream(
            "查询耳机保修政策",
            turn_id="turn-test",
            processor=fake_processor,
        )
    ]
    assert frames[0] == ("turn_started", {"turn_id": "turn-test"})
    assert frames[-1][0] == "turn_ended"
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `python -m pytest tests/test_stream_protocol.py::test_build_agent_event_stream_uses_supplied_processor -v`

Expected: FAIL because `build_agent_event_stream` is not defined.

- [ ] **Step 3: Add the injectable stream builder and endpoint**

In `web/routes.py`, define:

```python
async def build_agent_event_stream(message: str, turn_id: str, processor=ProcessUserInput_stream):
    async for frame in iter_sse_events(processor(message), turn_id):
        yield frame
```

Add `POST /api/chat/stream`; trim and validate `message` through a Pydantic field constraint `min_length=1`, generate `turn_id = f"turn_{uuid.uuid4().hex}"`, set `media_type="text/event-stream"`, and send headers `Cache-Control: no-cache` and `X-Accel-Buffering: no`. Do not modify the two legacy endpoints.

- [ ] **Step 4: Run the complete protocol test file**

Run: `python -m pytest tests/test_stream_protocol.py -v`

Expected: 4 tests PASS.

- [ ] **Step 5: Commit the endpoint**

```bash
git add web/routes.py tests/test_stream_protocol.py
git commit -m "feat: expose agent event stream endpoint"
```

### Task 3: 重建电商售后 Agent 工作台首页

**Files:**
- Modify: `web/templates/index.html`
- Create: `web/static/ecommerce-agent.css`
- Create: `web/static/ecommerce-agent.js`
- Create: `tests/test_ecommerce_frontend.py`

**Interfaces:**
- Consumes: `POST /api/chat/stream` and the event names defined in Task 1.
- Produces: DOM regions `#chat-log`, `#execution-timeline`, `#current-route`, `#chat-form`, `#message-input`, `#send-button`, and `.suggestion-chip`.

- [ ] **Step 1: Write failing static contract tests**

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "web" / "templates" / "index.html"
JS = ROOT / "web" / "static" / "ecommerce-agent.js"
CSS = ROOT / "web" / "static" / "ecommerce-agent.css"


def test_homepage_is_ecommerce_agent_workspace():
    source = HTML.read_text(encoding="utf-8")
    assert "电商售后 Agent" in source
    assert "订单与物流" in source
    assert "退换货办理" in source
    assert "上门安装与维修" in source
    assert "按摩" not in source
    assert "技师" not in source


def test_homepage_loads_external_assets_and_real_event_endpoint():
    html = HTML.read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")
    assert "/static/ecommerce-agent.css" in html
    assert "/static/ecommerce-agent.js" in html
    assert "/api/chat/stream" in js
    assert "route_selected" in js
    assert "answer_delta" in js
    assert "[THOUGHT]" not in js


def test_mobile_layout_and_required_regions_exist():
    html = HTML.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    for element_id in ("chat-log", "execution-timeline", "current-route", "message-input"):
        assert f'id="{element_id}"' in html
    assert "@media (max-width: 900px)" in css
    assert "grid-template-columns: 1fr" in css
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_ecommerce_frontend.py -v`

Expected: FAIL because the new static assets and e-commerce DOM do not exist.

- [ ] **Step 3: Implement the semantic page structure**

Replace the current single-card massage UI with:

- Top bar: JakePilot, “电商售后多 Agent 工作台”, online status, links to knowledge management and API docs.
- Left main panel: welcome copy, four capability chips (“商品/政策咨询”“订单与物流”“退换货办理”“上门安装与维修”), conversation log, text input, send and clear actions.
- Right details panel: current route, real event timeline, and a visible note stating that tool/knowledge Trace appears only when the backend emits the corresponding event.
- Four clickable demo prompts: “耳机保修期多久？”, “帮我查询订单 JP20260919001 的物流”, “这个商品想申请退货”, and “预约周六上午上门安装空调”.
- Plain Chinese error copy and keyboard-accessible buttons. Do not include any massage, technician, invented metric, or fake completed-step text.

- [ ] **Step 4: Implement robust SSE parsing and rendering**

`ecommerce-agent.js` must:

- submit `{message}` to `/api/chat/stream`;
- parse frames split by a blank line while retaining an incomplete tail between reads;
- append only `answer_delta.payload.delta` to the assistant bubble using `textContent` rather than `innerHTML`;
- update `#current-route` and append timeline rows only for actual `turn_started`, `route_selected`, `turn_ended`, and `turn_failed` events;
- show a retryable failure message for network errors, malformed frames, and non-2xx responses;
- re-enable the send button in `finally`;
- fill the input and submit when a suggestion chip is clicked;
- clear only the current conversation and timeline, preserving the welcome card.

- [ ] **Step 5: Implement the responsive visual system**

`ecommerce-agent.css` must define a warm neutral page background, navy/teal operational palette, readable Chinese typography, two-column desktop grid, visually distinct user/assistant messages, status dots, focus-visible states, and the exact `@media (max-width: 900px)` single-column fallback asserted by the test. Avoid decorative gradients on every surface and avoid hiding overflow on the page body.

- [ ] **Step 6: Run frontend contract tests**

Run: `python -m pytest tests/test_ecommerce_frontend.py -v`

Expected: 3 tests PASS.

- [ ] **Step 7: Commit the UI**

```bash
git add web/templates/index.html web/static/ecommerce-agent.css web/static/ecommerce-agent.js tests/test_ecommerce_frontend.py
git commit -m "feat: redesign ecommerce support agent workspace"
```

### Task 4: 更新项目身份与演示边界

**Files:**
- Modify: `app.py`
- Modify: `README.md`
- Modify: `tests/test_ecommerce_frontend.py`

**Interfaces:**
- Consumes: Tasks 1-3 delivered endpoint and page.
- Produces: consistent app metadata and reproducible local demo instructions.

- [ ] **Step 1: Add failing copy and documentation assertions**

```python
def test_app_and_readme_describe_current_mvp_truthfully():
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "电商售后多 Agent" in app_source
    assert "python -m uvicorn app:app --host 127.0.0.1 --port 8001" in readme
    assert "当前已实现" in readme
    assert "规划中" in readme
    assert "HermesRAG" in readme
```

- [ ] **Step 2: Run the focused assertion to verify it fails**

Run: `python -m pytest tests/test_ecommerce_frontend.py::test_app_and_readme_describe_current_mvp_truthfully -v`

Expected: FAIL because app metadata and README still describe the imported baseline.

- [ ] **Step 3: Update metadata and README**

Set the FastAPI description to “面向电商售后的中心路由式多 Agent 服务平台”. In README, document the single canonical command `python -m uvicorn app:app --host 127.0.0.1 --port 8001`; label the current route/consultation/appointment adaptation and SSE workbench as “当前已实现”; label HermesRAG adapter, order/after-sales tools, three-layer memory, full bounded runtime, evaluation platform, and local post-training as “规划中/按阶段实现”. Include the four demo prompts from Task 3 and state that successful startup still requires the configured remote model for the legacy agents.

- [ ] **Step 4: Run all new tests**

Run: `python -m pytest tests/test_stream_protocol.py tests/test_ecommerce_frontend.py -v`

Expected: 8 tests PASS and no external model request occurs.

- [ ] **Step 5: Commit documentation and metadata**

```bash
git add app.py README.md tests/test_ecommerce_frontend.py
git commit -m "docs: align JakePilot with ecommerce agent MVP"
```

### Task 5: 回归验证与浏览器验收

**Files:**
- Modify only if verification exposes a defect in files from Tasks 1-4.

**Interfaces:**
- Consumes: complete frontend MVP.
- Produces: recorded command evidence and a visually checked local demo; no fabricated benchmark numbers.

- [ ] **Step 1: Run the full offline test suite**

Run: `python -m pytest -v`

Expected: all tests PASS. If legacy tests require a live model, record the exact failing test and run the new offline test group separately; do not call that legacy failure a pass.

- [ ] **Step 2: Start the local server**

Run: `python -m uvicorn app:app --host 127.0.0.1 --port 8001`

Expected: Uvicorn reports the application running at `http://127.0.0.1:8001` and initialization either succeeds or reports an exact configuration dependency.

- [ ] **Step 3: Verify HTTP surfaces**

Run: `Invoke-WebRequest http://127.0.0.1:8001/ -UseBasicParsing`

Expected: status 200 and response body contains “电商售后 Agent”.

Run: `Invoke-WebRequest http://127.0.0.1:8001/static/ecommerce-agent.css -UseBasicParsing`

Expected: status 200 and a CSS content type.

- [ ] **Step 4: Perform desktop and mobile visual checks**

Open `http://127.0.0.1:8001/` and verify at approximately 1440px and 390px widths: no horizontal scrolling, input remains visible, capability chips wrap, execution timeline moves below the chat on mobile, focus rings are visible, and no massage-domain wording appears.

- [ ] **Step 5: Perform one live request if credentials are configured**

Submit “预约周六上午上门安装空调”. Verify that the timeline shows only events returned by `/api/chat/stream`, the answer contains no `[THOUGHT]` or `[SIGNAL]`, and a backend/model failure produces `turn_failed` plus a retryable user message. If credentials are absent, report this as an unexecuted live integration check rather than a successful run.

- [ ] **Step 6: Inspect the final diff without pushing**

Run: `git status --short`

Run: `git diff --check`

Run: `git diff --stat`

Expected: only the planned code, tests, README, app metadata, and the already-existing user documentation changes are present; `.reference_private/` is absent; no push is performed.

