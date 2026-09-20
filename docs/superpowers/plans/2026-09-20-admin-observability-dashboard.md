# JakePilot 管理员观测页实施计划

## 目标

为本地面试演示增加一个只读管理员观测页，复用现有 Turn Journal 和离线评测证据，不展示用户原文、答案正文、工具参数或隐藏推理。

## 约束

- 仅允许本机访问，当前版本不宣称已实现生产 JWT/RBAC。
- 只读取 `turn_checkpoints` 的安全投影和 `artifacts/eval/reports` 的脱敏 JSON。
- Smoke Set 结果必须明确标记为开发门禁，不得描述为正式 Golden Set 指标。
- 使用现有 Jinja 模板和独立 CSS，不增加生产依赖。
- 保持主页与现有 API 向后兼容。

## 任务

1. 为 Turn Journal 增加最近执行的安全只读查询，并用测试证明不包含会话、用户输入和答案。
2. 新增 ObservabilityService，容错读取最新评测报告，输出显式的 `formal_benchmark: false`。
3. 新增本地管理员路由与观测模板，展示系统状态、Smoke 指标和最近 Turn。
4. 在主页增加“运行观测”入口，完成桌面端与 375px 视口验证。
5. 更新演示文档和架构设计中的实施状态，运行聚焦测试、完整测试与浏览器验收。

## 视觉方向

- **Visual thesis**：深色运维控制台，低眩光、高信息密度，保留 JakePilot 的青绿色状态语义。
- **Content plan**：运行状态摘要 → 最新评测门禁 → 最近 Turn → 数据边界说明。
- **Interaction thesis**：链接按压缩放，状态点轻微呼吸；在 `prefers-reduced-motion` 下关闭动效。

## 验收

- `/admin/observability` 在本机可访问，非本机请求返回 403。
- 没有评测报告时页面展示待运行状态而不是空白或虚假通过。
- 最新报告和最近 Turn 来自真实文件与数据库。
- 页面不包含原始消息、回答、工具参数、session_id、user_id。
- 桌面与 375px 宽度无横向溢出，键盘焦点可见。
