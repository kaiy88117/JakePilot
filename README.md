# JakePilot

JakePilot 正在从通用预约示例改造为电商售后多 Agent 服务平台。当前版本保留 FastAPI、LangChain、FAISS 与 SQLite 基线，并新增结构化 SSE 工作台，用真实事件展示任务开始、Agent 路由、回答和终止状态。

## 当前已实现

- 中心任务分类 Agent，将请求路由到知识咨询或服务预约 Agent。
- 基于 FAISS 的知识咨询链路与流式回答。
- 多轮预约信息补全、人员匹配、时间检查与 SQLite 写入。
- `/api/chat/stream` 结构化 SSE 协议，过滤内部 `THOUGHT` 与 `SIGNAL` 标记。
- 电商售后工作台首页，支持快捷问题、流式回答、真实路由时间线和移动端布局。
- 旧 `/chat/stream` 与 `/chat` 文本流接口继续保留。

当前业务底层仍沿用导入项目的咨询和预约实现，尚未完成订单、退款、上门安装等电商工具替换。页面会明确标注这一边界，不把规划能力显示为已执行能力。

## 规划中

- 订单、物流、退换货、维修和上门安装的确定性工具与写操作确认。
- HermesRAG 适配器，包括答案、引用、证据充分性和管理员 Trace。
- Working、Episodic、Profile 三层记忆与 Context Engine。
- 有界 Agent Loop、Checkpoint、幂等、异常恢复及轨迹级评测。
- 面向预约槽位抽取与下一动作选择的本地小模型后训练。

详细设计见 `docs/superpowers/specs/2026-09-17-ecommerce-agent-architecture-design.md`。

## 请求链路

```text
浏览器工作台
   ↓  POST /api/chat/stream
SSE 安全适配层
   ↓
Task Classification Agent
   ├─ Consultation Agent → FAISS → 流式知识回答
   └─ Appointment Agent  → 槽位补全 → 可用性检查 → 预约写入
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
- `这个商品想申请退货`
- `预约周六上午上门安装空调`

前两类电商业务工具尚在规划中，当前路由可能返回能力边界提示；预约类问题可用于演示现有多轮链路。演示结果应以实际运行输出为准。

## 测试

离线验证本次 SSE 与前端改造：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_stream_protocol.py tests/test_ecommerce_frontend.py -v
```

完整测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

部分历史测试会初始化外部模型；未配置 Provider 时需如实记录为环境阻塞，不能记为测试通过。

## 项目沿革与授权提示

JakePilot 的基线代码由 [smart-appointment-ai-agent](https://github.com/jerry-ai-dev/smart-appointment-ai-agent) 导入后重新初始化 Git 历史。品牌化、电商场景、事件协议、记忆系统与评测改造在本仓库中独立迭代。

原仓库在本次导入时未包含明确的 `LICENSE` 文件，因此本仓库不额外声明原代码的授权范围。如需商业使用或再分发，请先向原作者确认授权。
