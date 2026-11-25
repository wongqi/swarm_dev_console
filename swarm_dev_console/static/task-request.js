"use strict";

// Task creation is an explicit draft -> review -> publish flow. MQTT sees only
// the reviewed envelope; observing state and editing files never publish.
class TaskRequest {
    constructor({ onDraft, onPublished }) {
        this.onDraft = onDraft;
        this.onPublished = onPublished;
        this.method = "task.execute";
        this.current = null;
        this.selected = null;
        $("new-task").addEventListener("click", () => this.open("task.execute"));
        for (const [id, method] of [["add-tasks", "task.add"], ["stop-task", "task.stop"], ["reset-task", "task.reset"]]) {
            $(id).addEventListener("click", () => this.open(method));
        }
        $("close-request").addEventListener("click", () => $("task-request").close());
        $("file").addEventListener("change", async () => {
            const file = $("file").files[0];
            if (!file) return;
            try {
                $("data").value = await file.text();
                this.updateDraft();
            } catch { $("draft-error").textContent = "任务文件读取失败"; }
        });
        let timer;
        $("data").addEventListener("input", () => {
            clearTimeout(timer);
            timer = setTimeout(() => this.updateDraft(), 250);
        });
        $("preview-map").addEventListener("click", () => {
            try {
                this.onDraft(this.read());
                $("task-request").close();
                $("allocation").scrollIntoView({ behavior: "smooth", block: "start" });
            } catch (error) { $("draft-error").textContent = error.message; }
        });
        $("request").addEventListener("submit", (event) => {
            event.preventDefault();
            this.prepare();
        });
        $("dismiss").addEventListener("click", () => {
            $("review").close();
            if (jsonMethod(this.method)) $("task-request").showModal();
        });
        $("send").addEventListener("click", () => this.send());
    }

    update(state) {
        this.current = state.mission;
        const valid = Boolean(this.current?.mission_id && this.current?.round_epoch);
        $("add-tasks").disabled = !valid || !["ACTIVE", "PLANNED", "PAUSING"].includes(this.current.state);
        $("stop-task").disabled = !valid || terminal.includes(this.current.state) || this.current.state === "EMPTY";
        $("reset-task").disabled = !valid;
    }

    open(method) {
        if (this.method !== method) {
            $("data").value = "";
            $("file").value = "";
        }
        this.method = method;
        this.target = method === "task.execute" ? null : this.current && {
            bid: this.current.mission_id, round_epoch: this.current.round_epoch,
        };
        this.selected = null;
        $("review-error").textContent = "";
        if (method !== "task.execute" && !this.target) return;
        if (!jsonMethod(method)) {
            this.selected = { bid: this.target.bid, method, data: { round_epoch: this.target.round_epoch } };
            this.review();
            return;
        }
        $("request-title").textContent = methods[method];
        $("request-target").textContent = this.target ? "目标任务 " + this.target.bid : "导入任务文件，轮次与任务 ID 自动生成";
        this.updateDraft();
        $("task-request").showModal();
    }

    read() {
        const data = JSON.parse($("data").value);
        if (!data || Array.isArray(data) || typeof data !== "object") throw new Error("任务 JSON 需要对象");
        if (!Array.isArray(data.tasks) || !Array.isArray(data.robot_ids) || !data.robot_ids.length ||
            typeof data.map_package !== "string" || !data.map_package.startsWith("/")) {
            throw new Error("请填写 robot_ids、地图绝对路径 map_package 和 tasks");
        }
        return data;
    }

    updateDraft() {
        try {
            const data = this.read();
            $("draft-summary").replaceChildren(...this.summary(data));
            $("draft-error").textContent = "";
            $("prepare-request").disabled = false;
            $("preview-map").disabled = false;
        } catch (error) {
            $("draft-summary").replaceChildren();
            $("draft-error").textContent = $("data").value.trim() ? error.message : "";
            $("prepare-request").disabled = true;
            $("preview-map").disabled = true;
        }
    }

    summary(data) {
        return [metric("任务点", data.tasks.length + " 个"), metric("设备", data.robot_ids.join("、")),
        metric("地图", data.map_package.split("/").filter(Boolean).pop())];
    }

    async prepare() {
        $("prepare-request").disabled = true;
        try {
            const data = this.read();
            if (this.method === "task.execute") {
                const result = await this.post("/api/epoch", {});
                data.round_epoch = result.round_epoch;
                this.selected = { bid: String(data.round_epoch), method: this.method, data };
            } else {
                data.round_epoch = this.target.round_epoch;
                this.selected = { bid: this.target.bid, method: this.method, data };
            }
            // Apply the explicit identity assignment in the editor before review.
            $("data").value = format(data);
            this.onDraft(data);
            $("task-request").close();
            this.review();
        } catch (error) { $("draft-error").textContent = error.message; }
        finally { $("prepare-request").disabled = false; }
    }

    review() {
        const { bid, method, data } = this.selected;
        $("review-title").textContent = "确认" + methods[method];
        $("review-target").textContent = "任务 " + bid + " · 轮次 " + data.round_epoch;
        $("review-summary").replaceChildren(...(jsonMethod(method) ? this.summary(data) : [
            metric("操作", method === "task.stop" ? "停止本轮任务" : "重置本轮协同状态"),
        ]));
        $("preview").textContent = format(this.selected);
        $("send").textContent = "确认" + methods[method];
        $("send").classList.toggle("danger", method === "task.stop");
        $("review-error").textContent = "";
        $("review").querySelector("details").open = false;
        $("review").showModal();
    }

    async post(path, payload) {
        const session = await (await fetch("/api/session")).json();
        const response = await fetch(path, {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-Swarm-Session": session.token },
            body: JSON.stringify(payload)
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "请求失败");
        return result;
    }

    async send() {
        if (!this.selected || $("send").disabled) return;
        $("send").disabled = true;
        try {
            if (this.method !== "task.execute" && (this.current?.round_epoch !== this.target.round_epoch ||
                this.current?.mission_id !== this.target.bid)) throw new Error("当前任务已变化，请关闭并重新选择操作");
            const result = await this.post("/api/request", this.selected);
            $("result").textContent = methods[this.method] + "：" + result.status + " · 任务 " + result.bid;
            $("review").close();
            this.onPublished(this.selected);
            this.selected = null;
        } catch (error) { $("review-error").textContent = error.message; }
        finally { $("send").disabled = false; }
    }
}
