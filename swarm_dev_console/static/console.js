"use strict";
const $ = (id) => document.getElementById(id);
const methods = {
    "task.execute": "新任务",
    "task.add": "追加任务点",
    "task.stop": "停止任务",
    "task.reset": "重置任务"
};
const states = {
    EMPTY: "无任务",
    PREPARING: "设备准备中",
    PLANNING: "规划中",
    PLANNED: "计划已提交",
    ACTIVE: "执行中",
    PAUSING: "暂停确认中",
    STOPPING: "停止确认中",
    COMPLETED: "已完成",
    FAILED: "失败",
    CANCELLED: "已取消"
};
const phases = {
    prepared: "路线已生成",
    starting: "正在启动",
    started: "路线已启动",
    stopped: "已停止",
    finalized: "结果已封存",
    failed: "启动失败"
};
const hubStates = {
    0: "空闲",
    1: "执行中",
    2: "已暂停",
    3: "已完成",
    4: "失败",
    5: "已取消"
};
const roles = { leader: "Leader", follower: "Follower", candidate: "候选" };
const terminal = ["COMPLETED", "FAILED", "CANCELLED"];
const format = (value) => JSON.stringify(value, null, 2);
const clock = (seconds) => seconds == null ? "—" : new Date(seconds * 1000).toLocaleTimeString();
const age = (now, received) => (Math.max(0, now - received)).toFixed(1) + " s";
const jsonMethod = (method) => ["task.execute", "task.add"].includes(method);
const showValue = (value) => value == null || value === "" ? "—" : String(value);
const list = (values) => Array.isArray(values) && values.length ? values.join("、") : "—";
let current = null;
let selected = null;
let events = [];
let wire = [];
let frozen = false;
let wireLoading = false;
let inspectedMessage = 0;
const signatures = new Map();

function node(tag, content, className = "") {
    const result = document.createElement(tag);
    if (content != null) result.textContent = content;
    if (className) result.className = className;
    return result;
}

function button(text, action) {
    const result = node("button", text, "link");
    result.type = "button";
    result.addEventListener("click", action);
    return result;
}

function row(values) {
    const item = node("tr");
    for (const value of values) {
        const cell = node("td");
        if (value instanceof Node) cell.append(value);
        else cell.textContent = showValue(value);
        item.append(cell);
    }
    return item;
}

function emptyRow(columns, message) {
    const item = node("tr");
    const cell = node("td", message, "empty");
    cell.colSpan = columns;
    item.append(cell);
    return item;
}

function cell(title, details = []) {
    const part = node("div");
    part.append(node("span", title, "cell-main"));
    for (const detail of details.filter(Boolean)) part.append(node("small", detail, "cell-detail"));
    return part;
}

function renderIfChanged(id, value, render) {
    const signature = JSON.stringify(value);
    if (signature !== signatures.get(id)) {
        signatures.set(id, signature);
        $(id).replaceChildren(...render());
    }
}

function inspect(title, value) {
    inspectedMessage++;
    $("inspect-title").textContent = title;
    $("inspect-json").textContent = typeof value === "string" ? value : format(value);
    if (!$("inspect").open) $("inspect").showModal();
}

async function inspectMessage(id, title = "MQTT 消息") {
    inspect(title, "正在读取…");
    const marker = inspectedMessage;
    try {
        const response = await fetch("/api/messages?id=" + encodeURIComponent(id));
        const value = await response.json();
        if (marker === inspectedMessage && $("inspect").open) {
            $("inspect-json").textContent = response.ok ? format(value) : value.error || "消息读取失败";
        }
    } catch {
        if (marker === inspectedMessage) $("inspect-json").textContent = "连接调试台失败";
    }
}

function metric(label, value) {
    const group = node("div");
    group.append(node("dt", label), node("dd", showValue(value)));
    return group;
}

function renderOverview(state) {
    const leader = state.devices.find((r) => r.authority === true &&
        r.round_epoch === current?.round_epoch && r.robot_id === r.leader_id &&
        state.connected && state.time - r.received_at < 5);
    const stale = current && !terminal.includes(current.state) &&
        (!state.connected || state.time - current.received_at >= 5);
    $("mission").textContent = current ?
        [current.mission_id, "轮次 " + current.round_epoch, "修订 " + current.plan_revision,
        stale ? "状态更新超时" : "", "最近接收 " + clock(current.received_at)].filter(Boolean).join(" · ") :
        "等待任务状态";
    const values = [
        ["任务状态", states[current?.state] ?? current?.state], ["导航到达 / 总点数", current ? [showValue(current.completed_tasks), showValue(current.total_tasks)].join(" / ") : null],
        ["待完成", current?.unfinished_tasks], ["Leader", leader?.robot_id ?? (current ? "待确认" : null)],
        ["任期 / 提交", current ? [showValue(current.term), showValue(current.commit_index)].join(" / ") : null],
        ["保底补执行", current?.retry_performed === true ? "已触发" : current?.retry_performed === false ? "未触发" : null],
    ];
    renderIfChanged("metrics", values, () => values.map(([label, value]) => metric(label, value)));
    $("progress").max = Math.max(1, current?.total_tasks ?? 0);
    $("progress").value = current?.completed_tasks ?? 0;
    $("detail").textContent = [current?.map_package, current?.message,
    current?.report_key && "报告 key " + current.report_key].filter(Boolean).join(" · ");
    if ($("state-detail").open) $("state-json").textContent = current ? format(current) : "等待任务状态";
}

function renderDevices(state) {
    const devices = [...state.devices].sort((a, b) => a.robot_id.localeCompare(b.robot_id));
    const live = devices.filter((device) => state.connected && state.time - device.received_at < 5).length;
    $("fleet-count").textContent = devices.length ? "近期收到 " + live + " / " + devices.length + " 台" : "等待设备心跳";
    renderIfChanged("devices", [devices, state.connected, devices.map((d) => age(state.time, d.received_at))],
        () => devices.length ? devices.map((device) => {
            const fresh = state.connected && state.time - device.received_at < 5;
            const identity = cell(device.robot_id, [
                device.sn && device.sn !== device.robot_id ? "SN " + device.sn : "",
                clock(device.received_at) + " · " + age(state.time, device.received_at),
            ]);
            identity.append(node("span", fresh ? "近期收到" : "接收超时", "badge " + (fresh ? "good" : "warn")));
            identity.append(document.createTextNode(" "), button("JSON", () => inspect(device.robot_id + " 心跳", device)));
            const pose = device.pose;
            const coords = Array.isArray(pose?.pos) ? pose.pos.map((n) => typeof n === "number" ? n.toFixed(2) : showValue(n)).join(", ") : "";
            const planning = device.planner_metrics ?? {};
            const computations = [
                typeof planning.wall_seconds === "number" ? "规划 " + planning.wall_seconds.toFixed(2) + " s" : "",
                typeof planning.compute_seconds === "number" ? "计算 " + planning.compute_seconds.toFixed(2) + " s" : "",
                typeof planning.peak_rss_kib === "number" ? (planning.peak_rss_kib / 1024).toFixed(1) + " MiB" : "",
            ];
            return row([
                identity,
                cell(pose?.frame_id ?? "暂无有效位姿", [
                    coords && "[" + coords + "] m",
                    "能力 " + list(device.capabilities),
                    typeof device.speed === "number" ? "标称速度 " + device.speed + " m/s" : "",
                ]),
                cell(device.ready === true ? "就绪" : device.ready === false ? "未就绪" : "—", [
                    device.services_ready == null ? "" : "服务" + (device.services_ready ? "可用" : "未就绪"),
                    device.task_hub_state == null ? "" : "TaskHub " + (hubStates[device.task_hub_state] ?? showValue(device.task_hub_state)),
                    device.expired ? "执行授权已到期" : "",
                ]),
                cell(roles[device.consensus_role] ?? showValue(device.consensus_role), [
                    "任期 " + showValue(device.term) + " · 轮次 " + showValue(device.round_epoch),
                    "提交 " + showValue(device.commit_index) + " · 已应用 " + showValue(device.applied_index),
                    device.leader_id && "Leader " + device.leader_id,
                    device.quorum_required ? "多数派门槛 " + device.quorum_required + "/" + showValue(device.voter_count) : "",
                    device.authority ? (fresh ? "具有 Leader 授权" : "上次报告具有授权") : "",
                ]),
                cell(phases[device.start_phase] ?? (device.start_phase || "—"), [
                    device.route_name, computations.filter(Boolean).join(" · "),
                ]),
            ]);
        }) : [emptyRow(5, "尚未接收设备心跳")]);
}

function reasonList(value) {
    if (!value || !Object.keys(value).length) return "—";
    if (Array.isArray(value)) return value.map((item) => typeof item === "object" ?
        [item.task_id, item.reason].filter(Boolean).join(" · ") : String(item)).join("、");
    return Object.entries(value).map(([id, reason]) =>
        id + (typeof reason === "string" ? " · " + reason : reason?.reason ? " · " + reason.reason : "")).join("、");
}

function renderAllocation(state) {
    const plan = state.plan?.round_epoch === current?.round_epoch ? state.plan : null;
    $("plan-label").textContent = plan ? "最近收到的计划修订 " + showValue(plan.plan_revision) : "等待本轮计划事件";
    $("plan-json").hidden = !plan?.message_id;
    $("plan-json").onclick = () => inspectMessage(plan.message_id, "完整计划 JSON");
    const routes = plan?.routes ?? [];
    renderIfChanged("routes", routes, () => routes.length ? routes.map((route) =>
        row([route.robot_id, cell((route.task_ids?.length ?? 0) + " 个", [list(route.task_ids)]),
        route.checkpoint_count, list(route.dependencies)])) : [emptyRow(4, "暂无分配数据")]);
    const exceptions = [["导航跳过", current?.skipped_tasks], ["结果未确认", current?.unconfirmed_tasks], ["能力不匹配", current?.unassignable_tasks]];
    renderIfChanged("exceptions", exceptions, () => current ? exceptions.map(([title, data]) => {
        const part = node("dl");
        part.append(node("dt", title), node("dd", reasonList(data)));
        return part;
    }) : []);
}

function renderEvents() {
    const filter = $("filter").value.toLowerCase().trim();
    const rows = events.filter((entry) => !filter || JSON.stringify(entry).toLowerCase().includes(filter)).slice().reverse();
    renderIfChanged("events", rows, () => rows.length ? rows.map((entry) => {
        const item = node("li");
        const bar = node("div", null, "event-main");
        bar.append(node("small", clock(entry.time)), node("span", entry.bid || "—"),
            node("span", entry.stage || methods[entry.method] || entry.method || "回执", "badge neutral"));
        if (typeof entry.result === "number") bar.append(node("span", entry.result === 0 ? "受理" : "拒绝", entry.result === 0 ? "badge good" : "badge bad"));
        bar.append(button("JSON", () => inspectMessage(entry.id, "事件或回执")));
        item.append(bar);
        if (entry.message) item.append(node("span", entry.message, "event-text"));
        return item;
    }) : [node("li", filter ? "没有匹配的事件" : "尚未接收事件或回执", "empty")]);
}

function renderTopics(state) {
    const topics = state.topics ?? {};
    const subscriptions = state.subscriptions ?? {};
    const choices = Object.keys(topics).sort();
    if (JSON.stringify(choices) !== signatures.get("topic-filter")) {
        signatures.set("topic-filter", JSON.stringify(choices));
        const selection = $("topic-filter").value;
        $("topic-filter").replaceChildren(new Option("全部主题", ""), ...choices.map((topic) => new Option(topic, topic)));
        if (choices.includes(selection)) $("topic-filter").value = selection;
    }
    const names = [...new Set([...Object.keys(subscriptions), ...choices])].sort();
    renderIfChanged("topic-stats", [subscriptions, topics], () => names.length ? names.map((topic) => {
        const qos = subscriptions[topic];
        return row([topic, qos == null ? "—" : qos >= 128 ? "Broker 拒绝" : "QoS " + qos,
            topics[topic]?.count ?? 0, clock(topics[topic]?.last_received)]);
    }) : [emptyRow(4, "等待订阅回执")]);
}

function renderWire() {
    const topic = $("topic-filter").value;
    const filter = $("wire-filter").value.toLowerCase().trim();
    const rows = wire.filter((entry) => (!topic || entry.topic === topic) &&
        (!filter || JSON.stringify(entry).toLowerCase().includes(filter)));
    $("traffic-count").textContent = "显示 " + rows.length + " / " + wire.length + " 条近期消息";
    renderIfChanged("wire", rows.map((entry) => entry.id), () => rows.length ? rows.map((entry) => row([
        clock(entry.time), entry.topic, entry.robot_id || entry.bid,
        entry.stage || methods[entry.method] || entry.method,
        [entry.round_epoch, entry.plan_revision].filter((v) => v != null).join(" / "),
        entry.bytes < 1024 ? entry.bytes + " B" : (entry.bytes / 1024).toFixed(1) + " KiB",
        button("JSON", () => inspectMessage(entry.id, entry.topic)),
    ])) : [emptyRow(7, "暂无匹配消息")]);
}

async function refreshWire() {
    if (frozen || wireLoading) return;
    wireLoading = true;
    try {
        const response = await fetch("/api/messages");
        if (response.ok && !frozen) {
            wire = await response.json();
            renderWire();
        }
    } finally {
        wireLoading = false;
    }
}

function render(state) {
    current = state.mission;
    $("view-report").disabled = !current?.report_key;
    const rejection = !state.connected && state.connection_result > 0;
    $("broker").textContent = (state.connected ? "MQTT 已连接" : rejection ?
        "MQTT 连接被拒绝 [" + state.connection_result + "]" : "MQTT 尚未连接") + " · " + state.broker;
    $("broker").className = "badge " + (state.connected ? "good" : rejection ? "bad" : "warn");
    renderOverview(state);
    renderDevices(state);
    renderAllocation(state);
    const requests = state.requests.map((request) => ({
        ...request, method: methods[request.method] ?? request.method,
    }));
    $("requests").textContent = requests.length ? format(requests) : "暂无发送记录";
    events = state.events;
    renderEvents();
    renderTopics(state);
}

async function refresh() {
    try {
        const response = await fetch("/api/state?compact=1");
        if (!response.ok) throw new Error("state unavailable");
        render(await response.json());
    } catch {
        $("broker").textContent = "本地调试台连接中断";
        $("broker").className = "badge bad";
    } finally {
        refreshWire().catch(() => { });
        window.setTimeout(refresh, 1000);
    }
}

$("topic-filter").addEventListener("change", renderWire);
$("wire-filter").addEventListener("input", renderWire);
$("freeze").addEventListener("click", () => {
    frozen = !frozen;
    $("freeze").setAttribute("aria-pressed", String(frozen));
    $("freeze").textContent = frozen ? "恢复消息刷新" : "暂停消息刷新";
    if (!frozen) refreshWire().catch(() => { });
});
$("method").addEventListener("change", () => {
    const method = $("method").value;
    $("json-fields").hidden = !jsonMethod(method);
    $("epoch-field").hidden = jsonMethod(method);
    $("new-epoch").hidden = method !== "task.execute";
    if (method !== "task.execute" && current) {
        $("bid").value = current.mission_id;
        $("epoch").value = current.round_epoch;
    }
});
$("filter").addEventListener("input", renderEvents);
$("file").addEventListener("change", async () => {
    const file = $("file").files[0];
    if (file) $("data").value = await file.text();
});
$("new-epoch").addEventListener("click", async () => {
    try {
        const data = JSON.parse($("data").value);
        if (!data || Array.isArray(data) || typeof data !== "object") throw new Error("请先导入或填写任务 JSON");
        const session = await (await fetch("/api/session")).json();
        const response = await fetch("/api/epoch", {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-Swarm-Session": session.token },
            body: "{}"
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "轮次生成失败");
        data.round_epoch = result.round_epoch;
        $("data").value = format(data);
        $("result").textContent = "已更新编辑区的 round_epoch，请预览后发送。";
    } catch (error) {
        $("result").textContent = error.message;
    }
});
$("request").addEventListener("submit", (event) => {
    event.preventDefault();
    const method = $("method").value;
    try {
        const data = jsonMethod(method) ? JSON.parse($("data").value) : {};
        if (!data || Array.isArray(data) || typeof data !== "object") throw new Error("任务 JSON 需要对象");
        if (!jsonMethod(method)) {
            data.round_epoch = Number($("epoch").value);
            if (!Number.isSafeInteger(data.round_epoch) || data.round_epoch < 1) throw new Error("请填写有效轮次");
        }
        if (!Number.isSafeInteger(data.round_epoch) || data.round_epoch < 1) {
            throw new Error("任务 JSON 需要有效的 round_epoch");
        }
        selected = { bid: $("bid").value, method, data };
        $("preview").textContent = format(selected);
        $("review").showModal();
    } catch (error) {
        $("result").textContent = error.message;
    }
});
$("dismiss").addEventListener("click", () => $("review").close());
$("send").addEventListener("click", async () => {
    $("send").disabled = true;
    try {
        const session = await (await fetch("/api/session")).json();
        const response = await fetch("/api/request", {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-Swarm-Session": session.token },
            body: JSON.stringify(selected)
        });
        const result = await response.json();
        $("result").textContent = result.error || result.status + " · " + result.round_epoch;
        $("review").close();
    } catch (error) {
        $("result").textContent = error.message;
    } finally {
        $("send").disabled = false;
    }
});
let replay = [];

function showReport(url) {
    $("report-frame").src = url + "&view=1";
    $("report-download").href = url + "&download=1";
    if (!$("report-dialog").open) $("report-dialog").showModal();
}

$("view-report").addEventListener("click", () =>
    showReport("/api/report?key=" + encodeURIComponent(current.report_key)));
$("report-dialog").addEventListener("close", () => $("report-frame").removeAttribute("src"));

function renderReplay(reset = false) {
    const robot = $("replay-robot").value;
    const rows = robot ? replay.filter((row) => row.robot === robot) : replay;
    const step = $("replay-step");
    step.max = Math.max(0, rows.length - 1);
    if (reset) step.value = 0;
    const i = Number(step.value);
    const row = rows[i];
    const value = row?.event;
    $("replay-detail").textContent = row ?
        [String(i + 1) + " / " + rows.length, row.robot, clock(value.time), value.kind,
        value.revision && "修订 " + value.revision].filter(Boolean).join(" · ") : "暂无可回放事件";
    $("replay-data").textContent = value ? format(value) : "";
}
$("replay-robot").addEventListener("change", () => renderReplay(true));
$("replay-step").addEventListener("input", () => renderReplay());

async function replayRound(archives, selected) {
    const group = archives.filter((archive) => archive.round_epoch === selected.round_epoch && archive.events);
    const histories = await Promise.all(group.map(async (archive) => {
        const response = await fetch("/api/archive/file?id=" + archive.id + "&name=timeline.json");
        if (!response.ok) throw new Error("时间线读取失败");
        return (await response.json()).map((event) => ({
            event, robot: event.robot_id || archive.robot_id || archive.name,
        }));
    }));
    replay = histories.flat().sort((a, b) => (a.event.time ?? 0) - (b.event.time ?? 0));
    $("replay-robot").replaceChildren(new Option("所有设备", ""), ...[...new Set(replay.map((row) => row.robot))].sort()
        .map((name) => new Option(name, name)));
    $("replay").hidden = false;
    renderReplay(true);
}

async function refreshLibrary() {
    const response = await fetch("/api/archive");
    if (!response.ok) throw new Error("证据库读取失败");
    const archives = await response.json();
    $("evidence-library").replaceChildren(...archives.map((archive) => {
        const row = node("div", null, "actions");
        row.append(node("span", [archive.name, archive.round_epoch, archive.robot_id].filter(Boolean).join(" · ")));
        if (archive.events) row.append(button("回放本轮", () => replayRound(archives, archive)
            .catch((error) => $("report-status").textContent = error.message)));
        archive.reports.forEach((name, i) => row.append(button("查看报告 " + (i + 1),
            () => showReport("/api/archive/file?id=" + archive.id + "&name=" + encodeURIComponent(name)))));
        return row;
    }));
}

$("import-evidence").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    $("report-status").textContent = "正在导入…";
    try {
        const session = await (await fetch("/api/session")).json();
        const response = await fetch("/api/archive?name=" + encodeURIComponent(file.name),
            { method: "POST", headers: { "X-Swarm-Session": session.token }, body: file });
        const info = await response.json();
        if (!response.ok) throw new Error(info.error || "导入失败");
        await refreshLibrary();
        $("report-status").textContent = "已导入 " + info.name;
    } catch (error) {
        $("report-status").textContent = error.message;
    } finally {
        event.target.value = "";
    }
});
refreshLibrary().catch((error) => $("report-status").textContent = error.message);
refresh();
