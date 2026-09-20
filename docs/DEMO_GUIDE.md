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

命令会先显示实际 SQLite 目标，只清理 `demo/user-a` 的退货申请和幂等执行记录，并刷新 `JP20260919002` 的演示退货时效。它不会删除匿名订单、物流、知识库、预约以及其他租户和用户数据。重启服务也会清除内存中的会话状态。

## 3. 启动与打开页面

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8001
```

看到 `Uvicorn running on http://127.0.0.1:8001` 后，在浏览器打开：

- 演示工作台：<http://127.0.0.1:8001/>
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
3. 用户确认后校验冻结参数和幂等键，只创建一条退货申请并返回申请编号。

讲解重点：写操作必须经过参数校验、资格核验和用户确认；重复确认不会重复写入。

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

## 5. 面试时的 30 秒讲法

> 这是一个中心路由式电商售后 Agent。通用政策通过 Knowledge Tool 调用独立 HermesRAG，并回传引用和证据状态；具体订单、物流和退货操作进入订单售后 Agent，上门安装进入预约 Agent。订单售后链路使用有界 Runtime，所有工具经过 Schema 校验，退货写操作还有参数冻结、二次确认和幂等 Action Ledger。前端通过安全 SSE 展示路由、工具和证据阶段，不暴露思维链、凭据、订单参数或内部异常。

## 6. 演示结束

回到启动服务的终端，按：

```text
Ctrl+C
```

若下次需要从干净状态重新演示，再执行第 2 节的重置命令。
