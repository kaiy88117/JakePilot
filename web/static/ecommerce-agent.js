(function () {
    "use strict";

    function parseSseChunk(rest, chunk) {
        const frames = `${rest}${chunk}`.split(/\r?\n\r?\n/);
        const pending = frames.pop() || "";
        const events = [];
        for (const frame of frames) {
            if (!frame.trim()) continue;
            let eventName = "message";
            const dataLines = [];
            for (const line of frame.split(/\r?\n/)) {
                if (line.startsWith("event:")) eventName = line.slice(6).trim();
                if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
            }
            if (dataLines.length) {
                events.push({ event: eventName, payload: JSON.parse(dataLines.join("\n")) });
            }
        }
        return { events, rest: pending };
    }

    async function consumeSseResponse(response, onEvent) {
        if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
        const reader = response.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let rest = "";
        let terminalReceived = false;
        while (true) {
            const { done, value } = await reader.read();
            const chunk = decoder.decode(value || new Uint8Array(), { stream: !done });
            const parsed = parseSseChunk(rest, chunk);
            rest = parsed.rest;
            for (const message of parsed.events) {
                if (message.event === "turn_ended" || message.event === "turn_failed") {
                    terminalReceived = true;
                }
                onEvent(message);
            }
            if (done) break;
        }
        if (rest.trim()) throw new Error("Incomplete SSE frame");
        if (!terminalReceived) throw new Error("Stream ended without terminal event");
    }

    function createSessionId() {
        if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
            return globalThis.crypto.randomUUID();
        }
        return `session_${Date.now()}_${Math.random().toString(16).slice(2)}`;
    }

    function buildChatPayload(message, sessionId) {
        return { message, session_id: sessionId };
    }

    function createRequestGuard() {
        let generation = 0;
        return {
            begin() {
                generation += 1;
                return generation;
            },
            invalidate() {
                generation += 1;
            },
            isCurrent(requestGeneration) {
                return requestGeneration === generation;
            }
        };
    }

    function describeRuntimeEvent(event, payload = {}) {
        const toolLabels = {
            "order.get": "查询订单",
            "logistics.get": "查询物流",
            "return.check": "核验退货条件",
            "return.create": "提交退货申请",
            "handoff.create": "创建人工接管"
        };
        const statusLabels = {
            succeeded: "成功",
            failed: "失败",
            not_found: "未找到",
            confirmation_required: "等待确认",
            handed_off: "已接管"
        };
        const toolLabel = toolLabels[payload.tool] || "业务工具";
        if (event === "tool_started") {
            return { title: "调用业务工具", detail: toolLabel };
        }
        if (event === "tool_finished") {
            const statusLabel = statusLabels[payload.status] || "已完成";
            return { title: "工具执行完成", detail: `${toolLabel} · ${statusLabel}` };
        }
        if (event === "confirmation_required") {
            return { title: "等待用户确认", detail: payload.summary || "请确认后继续执行" };
        }
        if (event === "input_required") {
            return { title: "等待补充信息", detail: payload.summary || "请补充任务信息" };
        }
        if (event === "handoff_created") {
            return { title: "已转人工客服", detail: `工单号 ${payload.ticket_no || "待生成"}` };
        }
        if (event === "knowledge_retrieval") {
            if (payload.fallback) {
                return { title: "知识服务降级", detail: "已切换本地知识库" };
            }
            const modeLabels = {
                agentic: "Agentic RAG",
                auto: "Auto RAG",
                standard: "标准 RAG",
                self_corrective: "自纠正 RAG"
            };
            const evidenceLabels = {
                sufficient: "证据充分",
                partial: "部分证据",
                insufficient: "证据不足",
                not_evaluated: "未执行证据评分"
            };
            const mode = modeLabels[payload.mode] || "知识检索";
            const evidence = evidenceLabels[payload.evidence_sufficiency] || "状态未知";
            const count = Number.isInteger(payload.citation_count) ? payload.citation_count : 0;
            return { title: "完成知识检索", detail: `${mode} · ${evidence} · ${count} 条引用` };
        }
        if (event === "memory_context") {
            const parts = [];
            if (payload.working_loaded) parts.push("恢复工作状态");
            parts.push(`${Number(payload.episodic_count) || 0} 条历史事件`);
            parts.push(`${Number(payload.profile_count) || 0} 条偏好`);
            return { title: "装配任务上下文", detail: parts.join(" · ") };
        }
        if (event === "decision_model_trace") {
            const modeLabels = {
                disabled: "强模型路径",
                shadow: "影子对比",
                local_first: "本地优先"
            };
            const fallbackLabels = {
                timeout: "本地模型超时",
                invalid_json: "非法结构",
                schema_validation: "Schema 校验失败",
                confirmed_slot_conflict: "已确认槽位冲突",
                business_precondition: "业务前置条件失败",
                action_not_allowed: "动作不在白名单",
                formal_evidence_missing: "缺少正式评测证据",
                local_error: "本地服务异常"
            };
            const mode = modeLabels[payload.mode] || "结构化决策";
            if (payload.source === "strong_model_fallback" || payload.fallback_reason) {
                const reason = fallbackLabels[payload.fallback_reason] || "已使用安全回退";
                return { title: "预约决策已回退", detail: `${mode} · ${reason}` };
            }
            const source = payload.source === "local_model" ? "本地模型生效" : "强模型生效";
            const validation = payload.validation_status === "passed" ? "校验通过" : "未执行本地校验";
            return { title: "校验预约决策", detail: `${mode} · ${source} · ${validation}` };
        }
        return null;
    }

    function describeTurnEnd(payload = {}) {
        if (payload.status === "needs_input") {
            return {
                title: "任务暂停",
                detail: "等待补充信息或确认",
                state: "needs-input",
                label: "等待输入"
            };
        }
        if (payload.status === "handed_off") {
            return {
                title: "人工接管已创建",
                detail: "等待人工客服继续处理",
                state: "handed-off",
                label: "已转人工"
            };
        }
        return {
            title: "任务结束",
            detail: "回答已完成",
            state: "completed",
            label: "已完成"
        };
    }

    if (typeof module !== "undefined" && module.exports) {
        module.exports = {
            parseSseChunk,
            consumeSseResponse,
            createSessionId,
            buildChatPayload,
            createRequestGuard,
            describeRuntimeEvent,
            describeTurnEnd
        };
    }
    if (typeof document === "undefined") return;

    const chatLog = document.getElementById("chat-log");
    const form = document.getElementById("chat-form");
    const input = document.getElementById("message-input");
    const sendButton = document.getElementById("send-button");
    const clearButton = document.getElementById("clear-button");
    const timeline = document.getElementById("execution-timeline");
    const currentRoute = document.getElementById("current-route");
    const turnStatus = document.getElementById("turn-status");
    let activeAnswer = null;
    let activeSessionId = createSessionId();
    let activeController = null;
    const requestGuard = createRequestGuard();

    function scrollChat() {
        chatLog.scrollTo({ top: chatLog.scrollHeight, behavior: "smooth" });
    }

    function createMessage(role, text, extraClass = "") {
        const article = document.createElement("article");
        article.className = `message ${role === "user" ? "user-message" : "assistant-message"}${extraClass ? ` ${extraClass}` : ""}`;
        const paragraph = document.createElement("p");
        paragraph.textContent = text;
        if (role !== "user") {
            const meta = document.createElement("div");
            meta.className = "message-meta";
            const dot = document.createElement("span");
            dot.className = "agent-dot";
            dot.setAttribute("aria-hidden", "true");
            meta.append(dot, document.createTextNode("JakePilot"));
            article.append(meta);
        }
        article.append(paragraph);
        chatLog.append(article);
        scrollChat();
        return paragraph;
    }

    function resetTimeline() {
        timeline.replaceChildren();
        currentRoute.textContent = "正在识别任务";
        turnStatus.textContent = "运行中";
        turnStatus.dataset.state = "running";
    }

    function appendTimeline(title, detail) {
        const item = document.createElement("li");
        const heading = document.createElement("strong");
        heading.textContent = title;
        item.append(heading, document.createTextNode(detail));
        timeline.append(item);
    }

    function setTerminalStatus(state, label) {
        turnStatus.textContent = label;
        turnStatus.dataset.state = state;
    }

    function handleEvent({ event, payload }) {
        if (event === "turn_started") {
            resetTimeline();
            appendTimeline("收到请求", "已创建本轮任务");
        } else if (event === "route_selected") {
            currentRoute.textContent = payload.label;
            appendTimeline("完成任务路由", payload.label);
        } else if (describeRuntimeEvent(event, payload)) {
            const description = describeRuntimeEvent(event, payload);
            if (event === "handoff_created") currentRoute.textContent = "人工客服接管";
            appendTimeline(description.title, description.detail);
        } else if (event === "answer_delta") {
            if (!activeAnswer) activeAnswer = createMessage("assistant", "");
            activeAnswer.textContent += payload.delta || "";
            scrollChat();
        } else if (event === "turn_ended") {
            const terminal = describeTurnEnd(payload);
            appendTimeline(terminal.title, terminal.detail);
            setTerminalStatus(terminal.state, terminal.label);
        } else if (event === "turn_failed") {
            if (!activeAnswer) activeAnswer = createMessage("assistant", "", "message-error");
            if (!activeAnswer.textContent) activeAnswer.textContent = payload.message || "服务暂时不可用，请稍后重试";
            appendTimeline("任务中止", payload.message || "服务处理失败");
            setTerminalStatus("failed", "需重试");
        }
    }

    async function submitMessage(message) {
        if (activeController) activeController.abort();
        const requestGeneration = requestGuard.begin();
        const requestSessionId = activeSessionId;
        const controller = new AbortController();
        activeController = controller;
        createMessage("user", message);
        activeAnswer = null;
        sendButton.disabled = true;
        sendButton.firstElementChild.textContent = "处理中";
        try {
            const response = await fetch("/api/chat/stream", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(buildChatPayload(message, requestSessionId)),
                signal: controller.signal
            });
            await consumeSseResponse(response, (event) => {
                if (requestGuard.isCurrent(requestGeneration)) handleEvent(event);
            });
        } catch (error) {
            if (!requestGuard.isCurrent(requestGeneration)) return;
            if (!activeAnswer) activeAnswer = createMessage("assistant", "", "message-error");
            if (!activeAnswer.textContent) activeAnswer.textContent = "连接中断，未能完成本次请求。请稍后重试。";
            appendTimeline("连接中断", "请求未完整返回，可重新发送");
            setTerminalStatus("failed", "需重试");
            console.error("Agent stream failed", error);
        } finally {
            if (requestGuard.isCurrent(requestGeneration)) {
                activeController = null;
                sendButton.disabled = false;
                sendButton.firstElementChild.textContent = "发送";
                input.focus();
            }
        }
    }

    form.addEventListener("submit", (event) => {
        event.preventDefault();
        const message = input.value.trim();
        if (!message || sendButton.disabled) return;
        input.value = "";
        submitMessage(message);
    });
    input.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            form.requestSubmit();
        }
    });
    document.querySelectorAll(".suggestion-chip").forEach((button) => {
        button.addEventListener("click", () => {
            input.value = button.dataset.prompt || "";
            form.requestSubmit();
        });
    });
    clearButton.addEventListener("click", () => {
        requestGuard.invalidate();
        if (activeController) activeController.abort();
        activeController = null;
        activeSessionId = createSessionId();
        chatLog.querySelectorAll(".message:not([data-welcome='true'])").forEach((node) => node.remove());
        const empty = document.createElement("li");
        empty.className = "timeline-empty";
        empty.textContent = "发送消息后，这里将展示后端真实返回的路由与终止状态。";
        timeline.replaceChildren(empty);
        currentRoute.textContent = "等待用户消息";
        setTerminalStatus("idle", "待命");
        sendButton.disabled = false;
        sendButton.firstElementChild.textContent = "发送";
        activeAnswer = null;
        input.focus();
    });
})();
