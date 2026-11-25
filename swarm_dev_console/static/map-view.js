"use strict";

// World coordinates -> original raster pixels; ROS image rows run top to bottom.
function mapPixel(map, location) {
    if (location?.frame_id !== map.frame_id || !Array.isArray(location.pos)) return null;
    const [x, y] = location.pos;
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    const dx = x - map.origin[0], dy = y - map.origin[1], yaw = map.origin[2];
    return [(Math.cos(yaw) * dx + Math.sin(yaw) * dy) / map.resolution,
    map.height - (-Math.sin(yaw) * dx + Math.cos(yaw) * dy) / map.resolution];
}

function routeColor(id) {
    let hash = 0;
    for (const ch of id) hash = (hash * 31 + ch.charCodeAt(0)) | 0;
    const palette = ["#2463c4", "#c43735", "#087c68", "#8650b5", "#c16d0c", "#bd3980", "#247eac", "#77731b"];
    return palette[(hash >>> 0) % palette.length];
}

class SwarmMap {
    constructor() {
        this.canvas = document.getElementById("map-canvas");
        this.frame = document.getElementById("map-frame");
        this.status = document.getElementById("map-status");
        this.legend = document.getElementById("map-legend");
        this.sequence = 0;
        this.task = null;
        this.plan = null;
        this.maps = [];
        this.scale = 1;
        this.offset = [0, 0];
        this.frame.addEventListener("change", () => this.loadImage());
        document.getElementById("map-fit").addEventListener("click", () => this.fit());
        this.canvas.addEventListener("wheel", (event) => {
            if (!this.image) return;
            event.preventDefault();
            const point = this.cursor(event);
            const next = Math.min(40, Math.max(.01, this.scale * Math.exp(-event.deltaY * .001)));
            this.offset = point.map((p, i) => p - (p - this.offset[i]) * next / this.scale);
            this.scale = next;
            this.draw();
        }, { passive: false });
        this.canvas.addEventListener("pointerdown", (event) => {
            if (event.button !== 0) return;
            this.drag = this.cursor(event);
            this.canvas.setPointerCapture(event.pointerId);
        });
        this.canvas.addEventListener("pointermove", (event) => {
            const point = this.cursor(event);
            if (this.drag) {
                this.offset = this.offset.map((p, i) => p + point[i] - this.drag[i]);
                this.drag = point;
                this.draw();
            }
        });
        this.canvas.addEventListener("lostpointercapture", () => { this.drag = null; });
        this.canvas.addEventListener("pointerup", () => { this.drag = null; });
        this.canvas.addEventListener("keydown", (event) => {
            if (event.key === "Home") { event.preventDefault(); this.fit(); }
        });
        new ResizeObserver(() => this.image ? this.fit() : this.draw()).observe(this.canvas);
    }

    cursor(event) {
        const rect = this.canvas.getBoundingClientRect();
        return [event.clientX - rect.left, event.clientY - rect.top];
    }

    async setTask(task) {
        this.task = task;
        const packagePath = task?.map_package;
        if (this.packagePath === packagePath) { this.draw(); return; }
        this.packagePath = packagePath;
        this.image = null;
        this.maps = [];
        this.frame.replaceChildren();
        const sequence = ++this.sequence;
        this.draw();
        if (!packagePath) { this.status.textContent = "请导入包含 map_package 的任务 JSON。"; return; }
        this.status.textContent = "正在读取本机地图…";
        try {
            const response = await fetch("/api/maps?package=" + encodeURIComponent(packagePath));
            const result = await response.json();
            if (sequence !== this.sequence) return;
            if (!response.ok) throw new Error(result.error || "地图读取失败");
            this.maps = result.maps;
            this.frame.replaceChildren(...this.maps.map((map) => new Option(map.frame_id, map.file)));
            const firstFrame = this.task?.tasks?.[0]?.location?.frame_id;
            const first = this.maps.find((map) => map.frame_id === firstFrame);
            if (first) this.frame.value = first.file;
            await this.loadImage();
        } catch (error) {
            if (sequence === this.sequence) this.status.textContent = error.message;
        }
    }

    async loadImage() {
        const sequence = ++this.sequence;
        const map = this.maps.find((item) => item.file === this.frame.value);
        this.image = null;
        this.draw();
        if (!map) return;
        this.status.textContent = "正在读取本机底图…";
        const image = new Image();
        image.src = "/api/maps/image?package=" + encodeURIComponent(this.packagePath) + "&file=" + encodeURIComponent(map.file);
        try {
            await image.decode();
            if (sequence !== this.sequence) return;
            this.image = image;
            this.map = map;
            this.fit();
        } catch {
            if (sequence === this.sequence) this.status.textContent = "底图加载失败，请检查本机地图图片。";
        }
    }

    setPlan(plan) { this.plan = plan; this.robot = null; this.draw(); }

    setRobot(robot) { this.robot = robot; this.draw(); }

    fit() {
        if (!this.image) return;
        const { width, height } = this.canvas.getBoundingClientRect();
        this.scale = Math.max(.01, Math.min((width - 32) / this.map.width, (height - 32) / this.map.height));
        this.offset = [(width - this.map.width * this.scale) / 2, (height - this.map.height * this.scale) / 2];
        this.draw();
    }

    draw() {
        const { width, height } = this.canvas.getBoundingClientRect();
        if (!width || !height) return;
        const ratio = window.devicePixelRatio || 1;
        this.canvas.width = Math.round(width * ratio);
        this.canvas.height = Math.round(height * ratio);
        const ctx = this.canvas.getContext("2d");
        ctx.scale(ratio, ratio);
        ctx.fillStyle = "#edf2f6";
        ctx.fillRect(0, 0, width, height);
        this.legend.replaceChildren();
        if (!this.image) return;
        const map = this.map;
        ctx.drawImage(this.image, ...this.offset, map.width * this.scale, map.height * this.scale);
        const screen = (location) => {
            const point = mapPixel(map, location);
            return point?.map((p, i) => this.offset[i] + p * this.scale);
        };
        // Only a plan for this exact imported round is eligible for an overlay.
        const plan = this.plan?.round_epoch === this.task?.round_epoch ? this.plan : null;
        const routes = Array.isArray(plan?.routes) ? plan.routes : Object.values(plan?.routes || {});
        const owners = new Map();
        for (const route of routes) {
            const color = routeColor(route.robot_id);
            for (const id of route.task_ids || []) owners.set(id, color);
            const entry = document.createElement("span");
            entry.textContent = route.robot_id + " · " + (route.task_ids?.length || 0) + " 点";
            entry.style.borderLeftColor = color;
            this.legend.append(entry);
            if (this.robot && route.robot_id !== this.robot) continue;
            ctx.strokeStyle = color;
            ctx.fillStyle = color;
            ctx.lineWidth = 2;
            ctx.setLineDash([6, 4]);
            let previous = null;
            for (const checkpoint of route.checkpoints || []) {
                const point = screen(checkpoint.location);
                if (!point) { previous = null; continue; }
                if (previous) {
                    ctx.beginPath(); ctx.moveTo(...previous); ctx.lineTo(...point); ctx.stroke();
                }
                ctx.beginPath(); ctx.arc(...point, 3, 0, Math.PI * 2); ctx.fill();
                previous = point;
            }
        }
        ctx.setLineDash([]);
        let count = 0, outside = 0;
        const tasks = Array.isArray(this.task?.tasks) ? this.task.tasks.filter((task) => task && typeof task === "object") : [];
        for (const task of tasks) {
            const point = screen(task.location);
            if (!point) continue;
            count++;
            const pixel = mapPixel(map, task.location);
            if (pixel[0] < 0 || pixel[1] < 0 || pixel[0] > map.width || pixel[1] > map.height) outside++;
            const selected = !this.robot || routes.find((r) => r.robot_id === this.robot)?.task_ids?.includes(task.id);
            ctx.globalAlpha = selected ? 1 : .2;
            ctx.fillStyle = owners.get(task.id) || "#233a53";
            ctx.strokeStyle = "white";
            ctx.lineWidth = 2;
            ctx.beginPath(); ctx.arc(...point, 5, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
            ctx.font = "12px system-ui";
            const label = String(task.id);
            const textWidth = ctx.measureText(label).width;
            ctx.fillStyle = "#ffffffed";
            ctx.fillRect(point[0] + 7, point[1] - 17, textWidth + 8, 18);
            ctx.fillStyle = "#192b42";
            ctx.fillText(label, point[0] + 11, point[1] - 4);
            ctx.globalAlpha = 1;
        }
        const unknown = tasks.filter((task) => !this.maps.some((m) => m.frame_id === task.location?.frame_id)).length;
        this.status.textContent = [this.packagePath?.split("/").filter(Boolean).pop(), map.frame_id,
        `${count} 个任务点`, `${map.resolution} m/像素`,
        plan ? "规划检查点 · R" + plan.plan_revision : "任务点预览",
        outside ? outside + " 个点超出底图范围" : "",
        unknown ? unknown + " 个任务点未找到对应子地图" : ""].filter(Boolean).join(" · ");
    }
}
