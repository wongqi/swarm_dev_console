"use strict";

function planRoutes(plan) {
    return Array.isArray(plan?.routes) ? plan.routes : Object.values(plan?.routes || {});
}

function allocationName(revision) {
    return revision === 1 ? "首次分配" : "第 " + (revision - 1) + " 次重分配";
}

// Only HTTP reads of the local plan archive. Selection never queries a robot.
class AllocationView {
    constructor(map) {
        this.map = map;
        this.mode = "live";
        this.round = null;
        this.revision = null;
        this.sequence = 0;
        this.signature = "";
        this.cache = new Map();
        $("plan-round").addEventListener("change", () => {
            if ($("plan-round").value === "draft") return;
            this.mode = "history";
            this.pending = null;
            this.round = Number($("plan-round").value);
            this.revision = null;
            this.update(this.state, true);
        });
        $("follow-plan").addEventListener("click", () => {
            this.mode = "live";
            this.pending = null;
            this.update(this.state, true);
        });
    }

    update(state, force = false) {
        if (!state) return;
        this.state = state;
        if (this.pending && state.mission?.round_epoch >= this.pending) this.pending = null;
        if (this.mode === "live") {
            this.round = this.pending ?? state.mission?.round_epoch ?? state.rounds?.[0]?.round_epoch ?? null;
        }
        this.renderRounds();
        if (this.mode === "draft") return;
        const signature = [this.round, state.history_version, this.mode].join(":");
        if (force || signature !== this.signature) {
            this.signature = signature;
            this.loadIndex();
        }
    }

    renderRounds() {
        const rows = [...(this.state?.rounds || [])];
        if (this.round && !rows.some((p) => p.round_epoch === this.round)) rows.unshift({ round_epoch: this.round });
        const key = JSON.stringify([rows, this.round, this.mode]);
        if (key !== this.roundKey) {
            this.roundKey = key;
            const options = rows.map((p) => new Option(String(p.round_epoch), String(p.round_epoch)));
            if (this.mode === "draft") options.unshift(new Option("未发送任务预览", "draft"));
            if (!options.length) options.push(new Option("尚无任务", ""));
            $("plan-round").replaceChildren(...options);
            $("plan-round").value = this.mode === "draft" ? "draft" : String(this.round || "");
        }
        $("follow-plan").classList.toggle("active", this.mode === "live");
        $("follow-plan").setAttribute("aria-pressed", String(this.mode === "live"));
    }

    draft(data) {
        this.sequence++;
        this.mode = "draft";
        this.plan = null;
        this.renderRounds();
        this.map.setPlan(null);
        this.map.setTask(data);
        $("plan-label").textContent = "任务文件预览";
        $("plan-revisions").replaceChildren(node("span", "未发送", "badge neutral"));
        $("plan-notice").textContent = "";
        $("allocation-title").textContent = "任务内容";
        $("allocation-summary").textContent = (data.tasks?.length || 0) + " 个任务点 · " + (data.robot_ids?.length || 0) + " 台设备";
        $("routes").replaceChildren(cell("参与设备", [list(data.robot_ids)]), cell("任务点 ID", [list(data.tasks?.map((t) => t.id))]));
        $("plan-json").hidden = true;
    }

    published(payload) {
        this.mode = "live";
        this.pending = payload.data.round_epoch;
        this.pendingData = payload.method === "task.execute" ? payload.data : null;
        this.round = payload.data.round_epoch;
        this.revision = null;
        this.update(this.state, true);
    }

    async fetchPlan(epoch, revision) {
        const key = epoch + ":" + revision;
        if (this.cache.has(key)) return this.cache.get(key);
        const response = await fetch(`/api/plans?epoch=${epoch}&revision=${revision}`);
        if (!response.ok) throw new Error("分配结果读取失败");
        const plan = await response.json();
        this.cache.set(key, plan);
        if (this.cache.size > 30) this.cache.delete(this.cache.keys().next().value);
        return plan;
    }

    async loadIndex() {
        const sequence = ++this.sequence;
        const epoch = this.round;
        try {
            const response = epoch ? await fetch("/api/plans?epoch=" + epoch) : null;
            const index = response?.ok ? await response.json() : [];
            if (sequence !== this.sequence) return;
            this.index = index;
            if (!index.length) {
                this.plan = null;
                this.map.setPlan(null);
                this.map.setTask(this.pendingData?.round_epoch === epoch ? this.pendingData : {
                    map_package: this.state?.mission?.round_epoch === epoch ? this.state.mission.map_package : "", tasks: [],
                });
                $("plan-label").textContent = epoch ? "轮次 " + epoch + " · 等待分配结果" : "等待任务与分配结果";
                $("allocation-title").textContent = "分配结果";
                $("allocation-summary").textContent = "尚未接收到本轮计划";
                $("routes").replaceChildren();
                $("plan-revisions").replaceChildren();
                $("plan-json").hidden = true;
                $("plan-notice").textContent = "";
                return;
            }
            const latest = index.at(-1).plan_revision;
            if (this.mode === "live" || !index.some((p) => p.plan_revision === this.revision)) this.revision = latest;
            this.renderRevisions();
            await this.show(this.revision);
        } catch (error) {
            if (sequence === this.sequence) $("plan-notice").textContent = error.message;
        }
    }

    renderRevisions() {
        $("plan-revisions").replaceChildren(...this.index.map((entry) => {
            const item = button(allocationName(entry.plan_revision) + " · R" + entry.plan_revision, () => {
                this.mode = "history";
                this.revision = entry.plan_revision;
                this.renderRounds();
                this.renderRevisions();
                this.show(this.revision);
            });
            item.className = "revision-button" + (entry.plan_revision === this.revision ? " selected" : "");
            item.setAttribute("aria-pressed", String(entry.plan_revision === this.revision));
            return item;
        }));
        const latest = this.index.at(-1).plan_revision;
        $("plan-notice").textContent = this.mode === "history" ?
            (latest > this.revision ? "最新 R" + latest + " · 正在回看 R" + this.revision : "历史查看") : "实时跟随";
    }

    async show(revision) {
        const sequence = ++this.sequence;
        const epoch = this.round;
        try {
            const [plan, previous] = await Promise.all([
                this.fetchPlan(epoch, revision),
                this.index.some((p) => p.plan_revision === revision - 1) ? this.fetchPlan(epoch, revision - 1) : null,
            ]);
            if (sequence !== this.sequence) return;
            this.plan = plan;
            const info = this.index.find((p) => p.plan_revision === revision);
            $("plan-label").textContent = "任务 " + (plan.mission_id || epoch) + " · 已接收 " + this.index.length + " 个修订";
            $("allocation-title").textContent = allocationName(revision) + " · R" + revision;
            $("allocation-summary").textContent = info.robot_count + " 台设备 · 本次分配 " + info.assigned_count + " 点 · 首次接收 " + clock(info.received_at);
            $("plan-json").hidden = false;
            $("plan-json").onclick = () => inspect("分配结果 R" + revision, plan);
            this.renderRoutes(plan, previous);
            const points = new Map();
            for (const route of planRoutes(plan)) for (const checkpoint of route.checkpoints || []) {
                for (const id of checkpoint.task_ids || []) points.set(id, { id, location: checkpoint.location });
            }
            this.map.setPlan(plan);
            await this.map.setTask({ round_epoch: epoch, map_package: plan.map_package, tasks: [...points.values()] });
        } catch (error) {
            if (sequence === this.sequence) $("plan-notice").textContent = error.message;
        }
    }

    renderRoutes(plan, previous) {
        const old = new Map(planRoutes(previous).map((r) => [r.robot_id, r]));
        const routes = planRoutes(plan);
        const cards = routes.map((route) => {
            const card = node("article", null, "route-card");
            const title = node("div", null, "section-title");
            const color = routeColor(route.robot_id);
            const name = node("strong", route.robot_id, "route-robot");
            name.style.borderLeftColor = color;
            title.append(name, node("span", (route.task_ids?.length || 0) + " 点", "badge neutral"));
            card.append(title);
            const ids = route.task_ids || [];
            const path = node("div", null, "task-sequence");
            for (const id of ids) path.append(node("span", id, "task-chip"));
            card.append(ids.length ? path : node("p", "本次无需执行", "muted"));
            card.append(node("small", [String(route.checkpoints?.length || 0) + " 个检查点",
            route.dependencies?.length ? "前置设备 " + list(route.dependencies) : ""].filter(Boolean).join(" · ")));
            if (previous) {
                const before = old.get(route.robot_id)?.task_ids || [];
                const added = ids.filter((id) => !before.includes(id));
                const removed = before.filter((id) => !ids.includes(id));
                if (added.length) card.append(node("p", "纳入 " + list(added), "route-change added"));
                if (removed.length) card.append(node("p", "移出 " + list(removed), "route-change removed"));
                if (!added.length && !removed.length && JSON.stringify(before) !== JSON.stringify(ids)) card.append(node("p", "执行顺序调整", "route-change"));
            }
            const toggle = button("在地图中单独查看", () => {
                const active = this.map.robot === route.robot_id;
                this.map.setRobot(active ? null : route.robot_id);
                $("routes").querySelectorAll("button").forEach((b) => { b.textContent = "在地图中单独查看"; });
                toggle.textContent = active ? "在地图中单独查看" : "显示全部设备";
            });
            card.append(toggle);
            return card;
        });
        if (previous) {
            const removed = [...old.keys()].filter((robot) => !routes.some((r) => r.robot_id === robot));
            if (removed.length) cards.push(node("p", "本次计划不再包含：" + list(removed), "muted"));
        }
        $("routes").replaceChildren(...(cards.length ? cards : [node("p", "本次没有执行路线", "empty")]));
    }
}
