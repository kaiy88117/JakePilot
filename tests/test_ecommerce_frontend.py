import json
import subprocess
from html.parser import HTMLParser
from pathlib import Path

from jinja2 import Environment, FileSystemLoader


ROOT = Path(__file__).resolve().parents[1]


class _RenderedPageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids: set[str] = set()
        self.assets: set[str] = set()
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"])
        for key in ("href", "src"):
            if values.get(key):
                self.assets.add(values[key])

    def handle_data(self, data: str):
        stripped = data.strip()
        if stripped:
            self.text.append(stripped)


def _render_homepage() -> _RenderedPageParser:
    environment = Environment(
        loader=FileSystemLoader(ROOT / "web" / "templates"),
        autoescape=True,
    )
    rendered = environment.get_template("index.html").render()
    parser = _RenderedPageParser()
    parser.feed(rendered)
    return parser


def test_rendered_homepage_exposes_ecommerce_agent_workspace():
    page = _render_homepage()
    visible_text = " ".join(page.text)

    assert "电商售后 Agent" in visible_text
    assert "订单与物流" in visible_text
    assert "退换货办理" in visible_text
    assert "上门安装与维修" in visible_text
    assert "按摩" not in visible_text
    assert "技师" not in visible_text
    assert {
        "chat-log",
        "execution-timeline",
        "current-route",
        "chat-form",
        "message-input",
        "send-button",
    } <= page.ids
    assert "/static/ecommerce-agent.css" in page.assets
    assert "/static/ecommerce-agent.js" in page.assets


def test_javascript_parser_handles_an_sse_frame_split_across_chunks():
    script_path = ROOT / "web" / "static" / "ecommerce-agent.js"
    node_program = f"""
const {{ parseSseChunk }} = require({json.dumps(str(script_path))});
const first = parseSseChunk('', 'event: answer_delta\\ndata: {{"turn_id":"t1","delta":"保');
const second = parseSseChunk(first.rest, '修一年"}}\\n\\n');
process.stdout.write(JSON.stringify({{ first, second }}));
"""
    result = subprocess.run(
        ["node", "-e", node_program],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    parsed = json.loads(result.stdout)

    assert parsed["first"]["events"] == []
    assert parsed["second"]["events"] == [
        {
            "event": "answer_delta",
            "payload": {"turn_id": "t1", "delta": "保修一年"},
        }
    ]
    assert parsed["second"]["rest"] == ""


def test_javascript_consumer_rejects_eof_without_terminal_event():
    script_path = ROOT / "web" / "static" / "ecommerce-agent.js"
    node_program = f"""
const {{ consumeSseResponse }} = require({json.dumps(str(script_path))});
const chunks = [
  Buffer.from('event: turn_started\\ndata: {{"turn_id":"t1"}}\\n\\nevent: answer_delta\\ndata: {{"turn_id":"t1","delta":"部分回答"}}\\n\\n')
];
const response = {{
  ok: true,
  status: 200,
  body: {{ getReader() {{ return {{ async read() {{ return chunks.length ? {{done:false,value:chunks.shift()}} : {{done:true}}; }} }}; }} }}
}};
consumeSseResponse(response, () => {{}})
  .then(() => process.stdout.write('resolved'))
  .catch((error) => process.stdout.write(error.message));
"""
    result = subprocess.run(
        ["node", "-e", node_program],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.stdout == "Stream ended without terminal event"


def test_app_metadata_identifies_the_ecommerce_agent_platform():
    from app import app

    assert app.title == "JakePilot"
    assert app.description == "面向电商售后的中心路由式多 Agent 服务平台"


def test_frontend_creates_and_sends_an_isolated_session_id():
    script_path = ROOT / "web" / "static" / "ecommerce-agent.js"
    node_program = f"""
const {{ createSessionId, buildChatPayload }} = require({json.dumps(str(script_path))});
const first = createSessionId();
const second = createSessionId();
process.stdout.write(JSON.stringify({{ first, second, payload: buildChatPayload('查询订单', first) }}));
"""
    result = subprocess.run(
        ["node", "-e", node_program],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    parsed = json.loads(result.stdout)

    assert parsed["first"] != parsed["second"]
    assert parsed["payload"] == {
        "message": "查询订单",
        "session_id": parsed["first"],
    }
    assert "新对话" in " ".join(_render_homepage().text)


def test_new_conversation_invalidates_the_previous_request():
    script_path = ROOT / "web" / "static" / "ecommerce-agent.js"
    node_program = f"""
const {{ createRequestGuard }} = require({json.dumps(str(script_path))});
const guard = createRequestGuard();
const oldRequest = guard.begin();
const before = guard.isCurrent(oldRequest);
guard.invalidate();
const after = guard.isCurrent(oldRequest);
const newRequest = guard.begin();
process.stdout.write(JSON.stringify({{ before, after, current: guard.isCurrent(newRequest) }}));
"""
    result = subprocess.run(
        ["node", "-e", node_program],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert json.loads(result.stdout) == {
        "before": True,
        "after": False,
        "current": True,
    }


def test_frontend_describes_runtime_tool_and_confirmation_events():
    script_path = ROOT / "web" / "static" / "ecommerce-agent.js"
    node_program = f"""
const {{ describeRuntimeEvent }} = require({json.dumps(str(script_path))});
const started = describeRuntimeEvent('tool_started', {{tool: 'logistics.get'}});
const finished = describeRuntimeEvent('tool_finished', {{tool: 'logistics.get', status: 'succeeded'}});
const confirmation = describeRuntimeEvent('confirmation_required', {{summary: '为订单提交退货申请'}});
const inputRequired = describeRuntimeEvent('input_required', {{summary: '请补充退货原因'}});
process.stdout.write(JSON.stringify({{ started, finished, confirmation, inputRequired }}));
"""
    result = subprocess.run(
        ["node", "-e", node_program],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert json.loads(result.stdout) == {
        "started": {"title": "调用业务工具", "detail": "查询物流"},
        "finished": {"title": "工具执行完成", "detail": "查询物流 · 成功"},
        "confirmation": {"title": "等待用户确认", "detail": "为订单提交退货申请"},
        "inputRequired": {"title": "等待补充信息", "detail": "请补充退货原因"},
    }
