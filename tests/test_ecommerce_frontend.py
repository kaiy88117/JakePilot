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
