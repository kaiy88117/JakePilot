# Turn Checkpoint and Delivery Journal Implementation Plan

**Goal:** 将任务草稿、业务副作用和 SSE 投递状态分开持久化，使服务能判断一次 Turn 是尚未执行、等待输入、业务已写入但回答断线，还是已经完整投递。

**Architecture:** Working Memory 继续保存可恢复任务状态，Action Ledger 继续保证写操作幂等；新增 `TurnCheckpoint` 只记录脱敏生命周期、最后公开事件、是否发生成功写入和投递状态。Web SSE 适配层在公开事件边界更新检查点，断连时标记 `cancelled/disconnected`，不保存问题、回答或工具参数。

## Task 1: Turn Journal 数据模型

- Modify: `db/models.py`
- Create: `db/repositories/turn_checkpoint_repository.py`
- Create: `services/turn_journal.py`
- Create: `tests/test_turn_journal.py`

- [x] TDD 覆盖租户/用户/会话隔离、状态转换、写入标记和无原文存储。
- [x] 实现 begin/observe/cancel/deliver/get 接口。

## Task 2: SSE 生命周期接入

- Modify: `web/routes.py`
- Modify: `tests/test_stream_protocol.py`

- [x] 正常流结束时记录 completed/delivered。
- [x] 断连或取消时记录 cancelled/disconnected，并保留此前已观察到的成功写入标记。
- [x] 增加按 turn_id + session_id 查询的安全状态接口。

## Task 3: 重置、文档与验证

- Modify: `scripts/reset_demo.py`
- Modify: `tests/test_demo_reset.py`
- Modify: `README.md`
- Modify: `docs/DEMO_GUIDE.md`

- [x] 演示重置清理 demo 用户的 Turn Checkpoint。
- [x] 运行维护套件、全量回归和真实 HTTP 检查后提交。

## Completion Gate

- 不存储消息正文、回答正文、工具参数、凭据或隐藏推理。
- Working Memory、Action Ledger、Turn Checkpoint 三类状态职责不重叠。
- 客户端断线不得回滚或重复已经成功提交的业务写操作。
- 查询接口必须同时匹配 turn_id 和 session_id。
