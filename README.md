# JakePilot

JakePilot 正在从通用预约示例改造为电商售后多 Agent 服务平台。当前版本保留 FastAPI、LangChain、FAISS 与 SQLite 基线，并以统一运行时承载知识咨询、订单售后和上门服务预约三类任务。

## 当前已实现

- 中心任务分类 Agent，将请求路由到知识咨询、订单售后或上门服务预约 Agent。
- 将独立 HermesRAG 封装为 Knowledge Tool，统一映射回答、引用、RAG 模式、证据充分性与终止状态；服务不可用时自动降级到基于 Ollama `bge-m3` 和 FAISS 的本地知识链路。
- 统一 Turn、Outcome 与 Trace 契约，以及最多 6 步、8 次工具调用、2 次重规划的有界执行循环。
- `order.get`、`logistics.get`、`return.check`、`return.create` 四个 Schema 工具，使用匿名 SQLite 演示数据完成订单查询、物流追踪和退货办理。
- 写操作二次确认、参数冻结、Action Ledger 幂等保护，以及按会话隔离的待确认操作。
- Working、Episodic、Profile 三层业务记忆，以及按租户/用户/会话隔离、30 分钟过期的任务恢复。
- 统一 Context Engine 按领域、来源和 Token 预算装配上下文；最多注入 3 条历史事件与 3 条偏好，不用记忆替代订单实时数据。
- 退货草稿和待确认动作可跨 Agent 实例恢复；确认时再次调用 `return.check` 回源，通过后才允许 `return.create` 写入。
- Working Memory、Action Ledger 与 Turn Journal 分离：分别记录任务状态、幂等业务写入和 SSE 投递结果，可识别“业务已成功但回答断线”并安全重试。
- 上门安装或维修的多轮信息补全、工程师匹配、时间检查与 SQLite 写入。
- `/api/chat/stream` 结构化 SSE 协议，过滤内部思维标记，只公开路由、工具状态、确认请求、回答和终止事件。
- 电商售后工作台首页，支持快捷问题、流式回答、运行时间线和移动端布局。
- 离线 Agent Smoke 评测：冻结 Case、复现匿名 SQLite 工具环境，确定性校验工具顺序、禁止调用、终态、确认、写入次数与步数上限，并生成绑定 Git SHA 的脱敏 Evidence Report。
- 正式评测开发门禁：支持多领域冻结 Case、三次重复运行、Semantic Judge 隔离、不可变 Evidence Store 与 CI 证据校验；完整 Golden Set 和真实指标仍需后续执行。
- 显式转人工工具、二次确认、幂等工单和脱敏接管摘要，并在终态异步沉淀可审计的长期记忆。
- 预约结构化决策模型的 Schema、业务 Guard、Shadow Gateway、强模型回退以及数据和组件评测骨架。
- 旧 `/chat/stream` 与 `/chat` 文本流接口继续保留。

## 后续工作

- 扩充并人工审核 200 条系统级 Golden Set，运行 Baseline 与 JakePilot 的正式重复对比。
- 构建预约决策训练数据，执行 SFT、DPO、GGUF 量化与 Shadow 验证；真实训练完成前不填写后训练效果指标。

详细设计见 `docs/superpowers/specs/2026-09-17-ecommerce-agent-architecture-design.md`。
本地启动、数据重置和面试演示顺序见 [`docs/DEMO_GUIDE.md`](docs/DEMO_GUIDE.md)。
后训练的实施步骤、故障推演和面试问答见 [`docs/interview/APPOINTMENT_POST_TRAINING_INTERVIEW_GUIDE.md`](docs/interview/APPOINTMENT_POST_TRAINING_INTERVIEW_GUIDE.md)。

## 请求链路

```text
浏览器工作台
   ↓  POST /api/chat/stream
SSE 安全适配层
   ↓
Task Classification Agent
   ├─ Consultation Agent → HermesRAG Knowledge Tool → 回答 / 引用 / 证据状态
   │                         └─ 不可用时回退本地 FAISS
   ├─ Order After-sales Agent → Context Engine → 有界 Loop → 订单/物流/退货工具
   │                              └─ Working / Episodic / Profile Memory
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

每次 `/api/chat/stream` 会在首个 `turn_started` 事件返回 `turn_id`。演示环境可通过 `GET /api/turns/{turn_id}?session_id={session_id}` 查询脱敏生命周期、最后事件、投递状态及是否已有成功写入；会话不匹配时返回 404。

旧 Agent 运行仍需要在 `.env` 中配置可用的 LLM 和 Embedding Provider。不要提交真实密钥。

### 可选：接入本地 HermesRAG

先在 `D:\superhermes agentic` 按主项目 README 启动依赖和端口 `8000` 的 HermesRAG 服务，再在 JakePilot 的本地 `.env` 增加：

```dotenv
HERMESRAG_ENABLED=true
HERMESRAG_BASE_URL=http://127.0.0.1:8000
HERMESRAG_USERNAME=你的本地账号
HERMESRAG_PASSWORD=你的本地密码
HERMESRAG_TIMEOUT_SECONDS=45
```

JakePilot 通过 `/auth/login` 获取短期 Token，再调用非流式 `/chat`；凭据、原始 Trace 和检索片段全文不会发送到浏览器。若未启用、认证失败或服务不可达，知识咨询会显示降级状态并继续使用本地 FAISS，不影响订单售后与预约链路。

## 演示问题

- `耳机保修期多久？`（HermesRAG 开启时，时间线展示模式、证据状态和引用数）
- `帮我查询订单 JP20260919001 的物流`
- `申请退货 JP20260919002`，补充 `原因是商品破损`，随后回复 `确认提交`
- `预约周六上午上门安装空调`

订单号、物流节点和退货记录均为本地匿名演示数据，不连接真实电商系统。退货多轮状态写入 SQLite，重建 Agent 后仍可恢复；过期状态、其他会话和其他用户均不可恢复。当前聊天入口使用演示租户与演示用户身份；演示结果应以实际运行输出为准。

## 测试

离线验证运行时、订单售后、SSE 与前端链路：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_runtime_contracts.py tests/test_tool_runtime.py tests/test_order_after_sales_tools.py tests/test_order_after_sales_agent.py tests/test_order_after_sales_e2e.py tests/test_order_memory_integration.py tests/test_memory_manager.py tests/test_context_engine.py tests/test_stream_protocol.py tests/test_ecommerce_frontend.py -v
```

完整测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

部分历史测试会初始化外部模型；未配置 Provider 时需如实记录为环境阻塞，不能记为测试通过。

### 离线 Agent Smoke 评测

```powershell
.\.venv\Scripts\python.exe -m scripts.run_agent_eval
```

默认运行 6 条订单售后确定性 Smoke Case，报告写入 `artifacts/eval/reports/`。报告包含数据集版本、Git SHA、每项断言的分子/分母、脱敏轨迹和回答摘要哈希，不保存完整问题、答案或工具参数。

这 6 条 Case 只用于验证评测框架和关键安全闭环，不是设计文档中规划的 200 条正式 Golden Set，也不能作为简历中的正式业务效果指标。正式指标必须扩充数据集、冻结版本并按同一配置重复运行后再填写。

## 项目沿革与授权提示

JakePilot 的基线代码由 [smart-appointment-ai-agent](https://github.com/jerry-ai-dev/smart-appointment-ai-agent) 导入后重新初始化 Git 历史。品牌化、电商场景、事件协议、记忆系统与评测改造在本仓库中独立迭代。

原仓库在本次导入时未包含明确的 `LICENSE` 文件，因此本仓库不额外声明原代码的授权范围。如需商业使用或再分发，请先向原作者确认授权。
