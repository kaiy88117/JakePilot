# Agent Evaluation MVP Implementation Plan

> **Execution:** implement inline with TDD. The evaluation path is offline-only and must never call production write services or fabricate benchmark results.

**Goal:** 为 JakePilot 建立可重复运行的轨迹级离线评测最小闭环，冻结 Case、确定性校验工具序列/终态/写入次数，并将每次运行的输入、代码版本、实际轨迹和断言结果写入版本化 Evidence Report。

**Architecture:** `evaluation` 包提供 Case Contract、Deterministic Verifier、Evidence Store 与订单售后 Runner。首批只覆盖完全离线且可确定验证的订单/物流/退货/记忆安全案例；语义 Judge 和 200 条正式 Golden Set 后续扩充，不把小规模 Smoke Set 包装成正式业务指标。

**Tech Stack:** Python 3.11、Pydantic 2、SQLite、pytest、JSON/JSONL

## Task 1: Case Contract 与 Deterministic Verifier

**Files:**
- Create: `evaluation/contracts.py`
- Create: `evaluation/verifier.py`
- Create: `tests/test_evaluation_verifier.py`

- [x] 先写失败测试，覆盖工具顺序、禁止工具、终态、回答片段、写入次数和有界终止。
- [x] 实现严格 Case/Run/EvaluationResult 契约及确定性评分。

## Task 2: Offline Runner 与 Evidence Store

**Files:**
- Create: `evaluation/order_runner.py`
- Create: `evaluation/evidence_store.py`
- Create: `evaluation/cases/order_after_sales_smoke.json`
- Create: `tests/test_evaluation_runner.py`

- [x] 先写失败测试，证明 Case 使用临时 SQLite、可重复运行且 Evidence 不包含完整私密消息。
- [x] 实现订单售后多轮 Case Adapter、Runner、聚合指标和原子化 JSON 报告写入。
- [x] 冻结少量 Smoke Set，明确它不是 200 条正式 Golden Set。

## Task 3: CLI、文档与回归

**Files:**
- Create: `scripts/run_agent_eval.py`
- Modify: `README.md`
- Modify: `docs/DEMO_GUIDE.md`
- Create/Modify: CLI tests

- [x] 提供 `python -m scripts.run_agent_eval`，默认运行仓库内 Smoke Set 并输出报告路径。
- [x] 报告数据集版本、代码版本、Case 数、各确定性指标的分子/分母；不生成 LLM Judge 分数。
- [x] 运行维护套件、全量测试与一次 CLI 实测后提交。

## Completion Gate

- 相同代码与数据集重复运行得到相同确定性断言结果。
- 高风险写入必须校验确认链路、工具顺序和最终写入次数。
- Evidence Report 绑定 Case 版本与 Git SHA，且不保存完整聊天、工具参数或凭据。
- 报告明确标注 `smoke`，不能作为简历正式指标，直到扩充并冻结正式 Golden Set。
