# JakePilot 本地演示手册

## 1. 第一次运行前检查

项目目录：

```powershell
Set-Location 'D:\superhermes agentic\JakePilot'
```

确认 Python 环境和 Ollama Embedding 模型可用：

```powershell
.\.venv\Scripts\python.exe --version
ollama list
```

`ollama list` 中应能看到 `bge-m3`。如果 Ollama 尚未启动，先打开 Ollama；不要重复运行多个 `ollama serve`。

项目通过环境变量使用：

- `MODEL_PROVIDER=deepseek`
- `LLM_MODEL=deepseek-flash`
- `EMBEDDING_PROVIDER=ollama`
- `EMBEDDING_MODEL=bge-m3`
- `EMBEDDING_BASE_URL=http://127.0.0.1:11434`

API Key 只放在本地 `.env` 或系统环境变量中，不要提交到 Git。

### 可选：启用 HermesRAG Knowledge Tool

如需演示两个项目的完整联动，先打开另一个 PowerShell：

```powershell
Set-Location 'D:\superhermes agentic'
docker compose up -d
uv run uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

确认已在 HermesRAG 注册可登录的本地账号，然后在 `JakePilot\.env` 中增加：

```dotenv
HERMESRAG_ENABLED=true
HERMESRAG_BASE_URL=http://127.0.0.1:8000
HERMESRAG_USERNAME=你的本地账号
HERMESRAG_PASSWORD=你的本地密码
HERMESRAG_TIMEOUT_SECONDS=45
```

不要把真实账号、密码或 Bearer Token 提交到 Git。若暂时只演示 JakePilot，将 `HERMESRAG_ENABLED=false` 即可继续使用本地 FAISS。

## 2. 每次演示前重置

先在运行服务的终端按 `Ctrl+C`，再执行：

```powershell
.\.venv\Scripts\python.exe -m scripts.reset_demo --yes
```

命令会先显示实际 SQLite 目标，只清理 `demo/user-a` 的退货申请、幂等执行记录和三层记忆，并刷新 `JP20260919002` 的演示退货时效。它不会删除匿名订单、物流、知识库、预约以及其他租户和用户数据。

## 3. 启动与打开页面

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8001
```

看到 `Uvicorn running on http://127.0.0.1:8001` 后，在浏览器打开：

- 演示工作台：<http://127.0.0.1:8001/>
- 本地运行观测：<http://127.0.0.1:8001/admin/observability>
- API 文档：<http://127.0.0.1:8001/docs>

如果提示端口 `8001` 已被占用，说明服务已经启动，直接打开工作台即可；不要再次启动。

## 4. 推荐演示顺序

### 场景一：知识咨询

输入：

```text
耳机保修期多久？
```

预期时间线（启用 HermesRAG 时）：

```text
知识咨询 Agent → 完成知识检索 → Agentic/标准模式 → 证据状态与引用数量
```

讲解重点：中心分类 Agent 将通用政策问题交给知识咨询 Agent；Knowledge Tool 只依赖稳定 HTTP 契约，不复制 HermesRAG 的 Milvus 与检索实现。HermesRAG 返回答案、引用和证据状态；服务不可用时前端明确显示降级，并切换本地 `bge-m3` + FAISS 链路。

### 场景二：物流查询

输入：

```text
帮我查询订单 JP20260919001 的物流
```

预期时间线：

```text
订单售后 Agent → 查询物流 → 工具执行成功 → 返回物流节点
```

讲解重点：模型只负责路由，订单号、用户身份和物流状态由确定性业务工具核验；前端看不到工具私有参数。

### 场景三：多轮退货与写操作确认

依次输入：

```text
申请退货 JP20260919002
原因是商品破损
确认提交
```

预期过程：

1. 首轮返回“请补充退货原因”，状态为 `needs_input`。
2. 补充原因后调用 `return.check`，再由 `return.create` 请求确认，此时数据库尚未创建退货申请。
3. 用户确认后再次调用 `return.check` 获取实时资格，再校验冻结参数和幂等键，只创建一条退货申请并返回申请编号。

讲解重点：草稿与待确认动作保存在 30 分钟有效的 Working Memory 中，即使重建 Agent 也能继续；写操作仍必须经过实时资格核验和用户确认，记忆不能替代业务事实，重复确认不会重复写入。前端时间线只展示是否恢复状态及召回数量，不展示记忆正文。

系统将恢复语义拆成三层：Working Memory 保存待补槽位与待确认动作；Action Ledger 保存幂等写入结果；Turn Journal 保存 SSE 是否完整投递。若业务已经提交但浏览器断线，检查点会保留“写入成功、投递断开”两个独立状态，用户重试时返回原业务结果，不重复创建申请。

### 场景四：规则拒绝

输入：

```text
订单 JP20260919003 能退货吗？
```

预期：系统说明已激活商品不符合当前演示退货规则，不创建任何申请。

### 场景五：会话隔离

先完成“原因是商品破损”但不要确认，点击“清空会话”或打开新的浏览器窗口，再输入：

```text
确认提交
```

预期：新会话返回“当前没有待确认操作”。这证明待确认操作不会跨会话执行。

### 可选场景：上门服务预约

输入：

```text
预约周六上午上门安装空调
```

该链路会继续询问缺失槽位，可用于说明同一中心路由下还保留原有预约 Agent。

预约结构化模型运行边界已接入，但演示默认配置为
`APPOINTMENT_DECISION_MODE=disabled`，因此不会访问本地推理端点。需要比较本地
模型时可改为 `shadow`：本地模型会生成结构化决策并接受 Schema、已确认槽位和
业务前置条件校验，但远端强模型结果仍是唯一权威结果。当前旧预约处理器尚未迁移
到新动作契约，因此 `local_first` 即使存在不少于 150 条冻结样本产生、且
`promotion_eligible=true` 的组件报告，也会以 `integration_runtime_not_ready`
自动降为 `shadow`；完成端到端执行顺序改造与冻结集对照前不会开放。
超时、非法 JSON、未允许动作或已确认槽位冲突均回退强模型；Trace 只展示模式、
来源、耗时、校验状态和回退原因，不包含原始消息、槽位值或凭据。

数据校验和组件评测命令如下；运行正式评测前需要先准备合规的外部 JSONL 和本地
OpenAI 兼容模型服务，项目仓库不附带训练数据或权重：

```powershell
.\.venv\Scripts\python.exe -m scripts.validate_appointment_dataset --input D:\data\appointment.jsonl --formal
.\.venv\Scripts\python.exe -m scripts.run_appointment_model_eval --input D:\data\appointment.jsonl --output-dir artifacts\appointment-model\reports --code-revision <git-sha> --prompt-version appointment-json-v1 --model-version <model-id> --hardware-label <hardware> --formal
```

### 可选场景：展示离线评测证据

另开终端执行：

```powershell
Set-Location 'D:\superhermes agentic\JakePilot'
.\.venv\Scripts\python.exe -m scripts.run_agent_eval
```

命令会运行 6 条离线订单售后 Smoke Case，覆盖查询、物流、退货资格、规则拒绝、确认写入和重启恢复，并输出 Evidence Report 文件名。完成后刷新 <http://127.0.0.1:8001/admin/observability>，可以在同一页面展示最新报告、各项确定性断言和最近 Turn 的运行/投递状态。讲解时应说明这是评测框架的可重复 Smoke Set，不是 200 条正式 Golden Set；可以展示工具顺序、确认、终态、写入次数和步数上限的确定性校验结果，但不要把 6/6 写成正式简历指标。

`--formal` 是失败关闭门禁，不是把 Smoke 改名成正式评测。它要求数据集版本为
`*-golden-vN`、总量不少于 200 条、七类业务配额完整、固定模型/Prompt/工具桩版本，
并声明每条 Case 重复运行 3 次。直接对仓库内 Smoke Set 执行会返回
`formal_gate_rejected`，且不会生成报告：

```powershell
.\.venv\Scripts\python.exe -m scripts.run_agent_eval --formal --model-version <model-id> --prompt-version <prompt-id> --tool-fixture-version <fixture-id>
```

当前尚未提交 200 条合规 Golden Set，也未接入多领域 Baseline 与 Semantic Judge；
因此管理员页只能展示 Smoke 报告。只有未来报告同时携带完整门禁证据时，页面才会标记为
“正式 Golden Set”。

当 Baseline 与 JakePilot 的正式报告都已生成后，可运行以下发布门禁。命令会校验两份
报告是否来自同一数据集和工具桩，检查 3 次重复、固定版本、核心质量/安全阈值，以及
任务成功率、工具选择率等指标是否出现超过 2 个百分点的回退：

```powershell
.\.venv\Scripts\python.exe -m scripts.check_agent_eval_release --baseline D:\eval\baseline.json --candidate D:\eval\jakepilot.json
```

当前没有正式报告时不得运行该命令生成简历结论；缺文件、非法 JSON、证据不完整、阈值
不达标或数据集不一致都会返回非零退出码。

运行观测页仅允许本机访问，且只展示脱敏生命周期，不包含用户原文、回答正文、工具参数或隐藏推理。它是面试演示入口，不代表生产 JWT/RBAC 已完成。

## 5. 面试时的 30 秒讲法

> 这是一个中心路由式电商售后 Agent。通用政策通过 Knowledge Tool 调用独立 HermesRAG；订单、物流和退货进入订单售后 Agent。系统用 Working、Episodic、Profile 三层记忆和 Context Engine 恢复任务，但订单事实始终通过工具实时回源。退货写操作还有参数冻结、二次确认和幂等 Action Ledger；前端通过安全 SSE 展示路由、工具、证据和记忆装配摘要，不暴露思维链、凭据或记忆正文。

## 6. 演示结束

回到启动服务的终端，按：

```text
Ctrl+C
```

若下次需要从干净状态重新演示，再执行第 2 节的重置命令。
