/**
 * realtime.js - 单一实时轮询控制器
 * 统一管理 Token 用量追踪、统计页、时间段图表的轮询，避免重复 interval。
 */

class RealtimeController {
    constructor() {
        this.jobs = new Map();
    }

    start(name, callback, intervalMs, runImmediately = true) {
        this.stop(name);
        const safeInterval = Math.max(Number(intervalMs) || 1000, 1000);
        const wrapped = async () => {
            try {
                await callback();
            } catch (err) {
                console.error(`Realtime job failed: ${name}`, err);
            }
        };

        const timerId = window.setInterval(wrapped, safeInterval);
        this.jobs.set(name, { timerId, callback: wrapped, intervalMs: safeInterval });
        if (runImmediately) {
            wrapped();
        }
    }

    stop(name) {
        const existing = this.jobs.get(name);
        if (!existing) return;
        window.clearInterval(existing.timerId);
        this.jobs.delete(name);
    }

    restart(name, callback, intervalMs, runImmediately = true) {
        this.start(name, callback, intervalMs, runImmediately);
    }

    stopAll() {
        for (const name of Array.from(this.jobs.keys())) {
            this.stop(name);
        }
    }

    isRunning(name) {
        return this.jobs.has(name);
    }
}

const realtimeController = new RealtimeController();

window.addEventListener('beforeunload', () => {
    realtimeController.stopAll();
});
