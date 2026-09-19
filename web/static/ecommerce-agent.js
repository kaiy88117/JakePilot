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

    if (typeof module !== "undefined" && module.exports) module.exports = { parseSseChunk };
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
        } else if (event === "answer_delta") {
            if (!activeAnswer) activeAnswer = createMessage("assistant", "");
            activeAnswer.textContent += payload.delta || "";
            scrollChat();
        } else if (event === "turn_ended") {
            appendTimeline("任务结束", "回答已完成");
            setTerminalStatus("completed", "已完成");
        } else if (event === "turn_failed") {
            if (!activeAnswer) activeAnswer = createMessage("assistant", "", "message-error");
            if (!activeAnswer.textContent) activeAnswer.textContent = payload.message || "服务暂时不可用，请稍后重试";
            appendTimeline("任务中止", payload.message || "服务处理失败");
            setTerminalStatus("failed", "需重试");
        }
    }

    async function submitMessage(message) {
        createMessage("user", message);
        activeAnswer = null;
        sendButton.disabled = true;
        sendButton.firstElementChild.textContent = "处理中";
        try {
            const response = await fetch("/api/chat/stream", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message })
            });
            if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);

            const reader = response.body.getReader();
            const decoder = new TextDecoder("utf-8");
            let rest = "";
            while (true) {
                const { done, value } = await reader.read();
                const chunk = decoder.decode(value || new Uint8Array(), { stream: !done });
                const parsed = parseSseChunk(rest, chunk);
                rest = parsed.rest;
                parsed.events.forEach(handleEvent);
                if (done) break;
            }
            if (rest.trim()) throw new Error("Incomplete SSE frame");
        } catch (error) {
            if (!activeAnswer) activeAnswer = createMessage("assistant", "", "message-error");
            if (!activeAnswer.textContent) activeAnswer.textContent = "连接中断，未能完成本次请求。请稍后重试。";
            appendTimeline("连接中断", "请求未完整返回，可重新发送");
            setTerminalStatus("failed", "需重试");
            console.error("Agent stream failed", error);
        } finally {
            sendButton.disabled = false;
            sendButton.firstElementChild.textContent = "发送";
            input.focus();
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
        chatLog.querySelectorAll(".message:not([data-welcome='true'])").forEach((node) => node.remove());
        const empty = document.createElement("li");
        empty.className = "timeline-empty";
        empty.textContent = "发送消息后，这里将展示后端真实返回的路由与终止状态。";
        timeline.replaceChildren(empty);
        currentRoute.textContent = "等待用户消息";
        setTerminalStatus("idle", "待命");
        activeAnswer = null;
        input.focus();
    });
})();
