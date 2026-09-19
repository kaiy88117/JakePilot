# JakePilot

JakePilot 正在从通用预约示例改造为电商售后多 Agent 服务平台。当前版本保留 FastAPI、LangChain、FAISS 与 SQLite 基线，并以统一运行时承载知识咨询、订单售后和上门服务预约三类任务。

## 当前已实现

- 中心任务分类 Agent，将请求路由到知识咨询、订单售后或上门服务预约 Agent。
- 基于 Ollama `bge-m3`、FAISS 与电商售后种子知识的流式咨询链路。
- 统一 Turn、Outcome 与 Trace 契约，以及最多 6 步、8 次工具调用、2 次重规划的有界执行循环。
- `order.get`、`logistics.get`、`return.check`、`return.create` 四个 Schema 工具，使用匿名 SQLite 演示数据完成订单查询、物流追踪和退货办理。
- 写操作二次确认、参数冻结、Action Ledger 幂等保护，以及按会话隔离的待确认操作。
- 上门安装或维修的多轮信息补全、工程师匹配、时间检查与 SQLite 写入。
- `/api/chat/stream` 结构化 SSE 协议，过滤内部思维标记，只公开路由、工具状态、确认请求、回答和终止事件。
- 电商售后工作台首页，支持快捷问题、流式回答、运行时间线和移动端布局。
- 旧 `/chat/stream` 与 `/chat` 文本流接口继续保留。

## 规划中

- HermesRAG 适配器，包括答案、引用、证据充分性和管理员 Trace。
- Working、Episodic、Profile 三层记忆与 Context Engine。
- Checkpoint、异常恢复及轨迹级评测。
- 面向预约槽位抽取与下一动作选择的本地小模型后训练。

详细设计见 `docs/superpowers/specs/2026-09-17-ecommerce-agent-architecture-design.md`。
本地启动、数据重置和面试演示顺序见 [`docs/DEMO_GUIDE.md`](docs/DEMO_GUIDE.md)。

## 请求链路

```text
浏览器工作台
   ↓  POST /api/chat/stream
SSE 安全适配层
   ↓
Task Classification Agent
   ├─ Consultation Agent → FAISS → 流式知识回答
   ├─ Order After-sales Agent → 有界 Loop → 订单/物流/退货工具
   └─ Appointment Agent       → 槽位补全 → 可用性检查 → 预约写入
```

SSE 适配层只公开可解释的运行事件，不向用户展示模型隐藏思维链。

## 本地启动

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python -m uvicorn app:app --host 127.0.0.1 --port 8001
```

访问：

- 工作台：`http://127.0.0.1:8001/`
- API 文档：`http://127.0.0.1:8001/docs`

旧 Agent 运行仍需要在 `.env` 中配置可用的 LLM 和 Embedding Provider。不要提交真实密钥。

## 演示问题

- `耳机保修期多久？`
- `帮我查询订单 JP20260919001 的物流`
- `申请退货 JP20260919002，原因是商品破损`，随后回复 `确认提交`
- `预约周六上午上门安装空调`

订单号、物流节点和退货记录均为本地匿名演示数据，不连接真实电商系统。当前聊天入口使用演示租户与演示用户身份；演示结果应以实际运行输出为准。

## 测试

离线验证运行时、订单售后、SSE 与前端链路：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_runtime_contracts.py tests/test_tool_runtime.py tests/test_order_after_sales_tools.py tests/test_order_after_sales_agent.py tests/test_order_after_sales_e2e.py tests/test_stream_protocol.py tests/test_ecommerce_frontend.py -v
```

完整测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

部分历史测试会初始化外部模型；未配置 Provider 时需如实记录为环境阻塞，不能记为测试通过。

## 项目沿革与授权提示

JakePilot 的基线代码由 [smart-appointment-ai-agent](https://github.com/jerry-ai-dev/smart-appointment-ai-agent) 导入后重新初始化 Git 历史。品牌化、电商场景、事件协议、记忆系统与评测改造在本仓库中独立迭代。

原仓库在本次导入时未包含明确的 `LICENSE` 文件，因此本仓库不额外声明原代码的授权范围。如需商业使用或再分发，请先向原作者确认授权。
