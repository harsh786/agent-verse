import { AgentVerseError, AuthError, GoalFailedError, GoalTimeoutError, NotFoundError } from './errors.js';
export class AgentVerseClient {
    constructor(apiKey, baseUrl = 'http://localhost:8000') {
        this.apiKey = apiKey;
        this.baseUrl = baseUrl.replace(/\/$/, '');
    }
    get headers() {
        return {
            'X-API-Key': this.apiKey,
            'Content-Type': 'application/json',
        };
    }
    async request(method, path, body) {
        const url = `${this.baseUrl}${path}`;
        const res = await fetch(url, {
            method,
            headers: this.headers,
            body: body !== undefined ? JSON.stringify(body) : undefined,
        });
        if (res.status === 401)
            throw new AuthError();
        if (res.status === 404)
            throw new NotFoundError(path);
        if (!res.ok) {
            const text = await res.text().catch(() => res.statusText);
            throw new AgentVerseError(`API error ${res.status}: ${text.slice(0, 200)}`, res.status);
        }
        if (res.status === 204)
            return undefined;
        return res.json();
    }
    // ── Goals ────────────────────────────────────────────────────────────────
    async submitGoal(goal, options = {}) {
        return this.request('POST', '/goals', {
            goal,
            priority: options.priority ?? 'normal',
            dry_run: options.dry_run ?? false,
            agent_id: options.agent_id,
            persistence_mode: options.persistence_mode ?? false,
            workflow_mode: options.workflow_mode ?? 'single_agent',
        });
    }
    async getGoal(goalId) {
        return this.request('GET', `/goals/${goalId}`);
    }
    async listGoals() {
        const data = await this.request('GET', '/goals');
        return data.goals ?? [];
    }
    async cancelGoal(goalId) {
        await this.request('POST', `/goals/${goalId}/cancel`);
    }
    async waitForGoal(goalId, options) {
        const timeout = options?.timeout ?? 300;
        // Try SSE streaming first (more efficient)
        try {
            let finalGoal = null;
            const deadline = Date.now() + timeout * 1000;
            for await (const event of this.streamGoal(goalId)) {
                if (Date.now() > deadline)
                    break;
                const eventType = event.type ?? '';
                if (eventType === 'goal_complete' || eventType === 'goal_finished') {
                    finalGoal = await this.getGoal(goalId);
                    return finalGoal;
                }
                if (eventType === 'goal_failed' || eventType === 'goal_error') {
                    finalGoal = await this.getGoal(goalId);
                    const reason = event.payload?.reason ?? 'Goal failed';
                    throw new GoalFailedError(goalId, `Goal ${goalId} failed: ${reason}`);
                }
            }
            return await this.getGoal(goalId);
        }
        catch (err) {
            // SSE failed or timed out — fall back to polling
            if (err instanceof GoalFailedError) {
                throw err; // Re-throw actual goal failures
            }
            if (err instanceof GoalTimeoutError) {
                throw err;
            }
        }
        // Polling fallback
        const pollInterval = options?.pollInterval ?? 2000;
        const deadline = Date.now() + timeout * 1000;
        while (Date.now() < deadline) {
            const goal = await this.getGoal(goalId);
            const status = goal.status ?? '';
            if (['complete', 'completed', 'failed', 'error', 'cancelled'].includes(status)) {
                if (status === 'failed' || status === 'error') {
                    throw new GoalFailedError(goalId, `Goal ${goalId} failed: ${goal.error_message ?? 'unknown error'}`);
                }
                return goal;
            }
            await new Promise(resolve => setTimeout(resolve, pollInterval));
        }
        throw new GoalTimeoutError(goalId, timeout);
    }
    async *streamGoal(goalId) {
        const url = `${this.baseUrl}/goals/${goalId}/stream`;
        const res = await fetch(url, { headers: this.headers });
        if (!res.ok || !res.body)
            throw new AgentVerseError(`Stream failed: ${res.status}`);
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (true) {
            const { done, value } = await reader.read();
            if (done)
                break;
            buffer += decoder.decode(value, { stream: true });
            const frames = buffer.split('\n\n');
            buffer = frames.pop() ?? '';
            for (const frame of frames) {
                for (const line of frame.split('\n')) {
                    if (line.startsWith('data: ')) {
                        try {
                            yield JSON.parse(line.slice(6));
                        }
                        catch {
                            // skip malformed
                        }
                    }
                }
            }
        }
    }
    // ── Agents ───────────────────────────────────────────────────────────────
    async createAgent(name, goalTemplate = '', extra = {}) {
        return this.request('POST', '/agents', { name, goal_template: goalTemplate, ...extra });
    }
    async getAgent(agentId) {
        return this.request('GET', `/agents/${agentId}`);
    }
    async updateAgent(agentId, data) {
        return this.request('PUT', `/agents/${agentId}`, data);
    }
    async runAgent(agentId, goal, options) {
        return this.submitGoal(goal, {
            agent_id: agentId,
            dry_run: options?.dryRun,
        });
    }
    async listAgents() {
        const data = await this.request('GET', '/agents');
        return Array.isArray(data) ? data : data.agents ?? [];
    }
    async deleteAgent(agentId) {
        await this.request('DELETE', `/agents/${agentId}`);
    }
    async snapshotAgent(agentId) {
        return this.request('POST', `/agents/${agentId}/snapshot`);
    }
    async listAgentVersions(agentId) {
        return this.request('GET', `/agents/${agentId}/versions`);
    }
    async rollbackAgent(agentId, snapshotId) {
        return this.request('POST', `/agents/${agentId}/rollback/${snapshotId}`);
    }
    // ── Connectors ───────────────────────────────────────────────────────────
    async listConnectors() {
        const data = await this.request('GET', '/connectors');
        return Array.isArray(data) ? data : [];
    }
    async registerConnector(name, url, authType = 'bearer', authConfig = {}) {
        return this.request('POST', '/connectors', {
            name, url, auth_type: authType, auth_config: authConfig,
        });
    }
    async deleteConnector(serverId) {
        await this.request('DELETE', `/connectors/${serverId}`);
    }
    async testConnector(serverId) {
        return this.request('POST', `/connectors/${serverId}/test`);
    }
    async getConnectorCatalog() {
        return this.request('GET', '/connectors/catalog');
    }
    // ── Schedules ────────────────────────────────────────────────────────────
    async listSchedules() {
        return this.request('GET', '/schedules');
    }
    async createSchedule(data) {
        return this.request('POST', '/schedules', data);
    }
    async deleteSchedule(scheduleId) {
        await this.request('DELETE', `/schedules/${scheduleId}`);
    }
    async createScheduleNl(command) {
        return this.request('POST', '/schedules/nl', { command });
    }
    // ── Memory ───────────────────────────────────────────────────────────────
    async recallMemory(query, limit = 10) {
        return this.request('GET', `/memory/recall?q=${encodeURIComponent(query)}&limit=${limit}`);
    }
    async storeMemory(content, tags) {
        return this.request('POST', '/memory', { content, tags });
    }
    // ── Knowledge ────────────────────────────────────────────────────────────
    async searchKnowledge(collectionId, query, limit = 10) {
        return this.request('GET', `/knowledge/search?collection_id=${collectionId}&q=${encodeURIComponent(query)}&limit=${limit}`);
    }
    // ── Analytics ────────────────────────────────────────────────────────────
    async getGoalMetrics(days = 30) {
        return this.request('GET', `/analytics/goals?days=${days}`);
    }
    async getCostMetrics(days = 30) {
        return this.request('GET', `/analytics/cost?days=${days}`);
    }
    // ── Tool reliability ──────────────────────────────────────────────────────
    async getToolReliability() {
        return this.request('GET', '/memory/tool-reliability');
    }
    // ── Agent rollout gate ────────────────────────────────────────────────────
    async checkRolloutGate(agentId, evalSuiteId) {
        const params = evalSuiteId ? `?eval_suite_id=${evalSuiteId}` : '';
        return this.request('GET', `/agents/${agentId}/rollout-gate${params}`);
    }
    // ── Consent management ────────────────────────────────────────────────────
    async recordConsent(purpose, legalBasis) {
        return this.request('POST', '/compliance/consent', {
            purpose,
            legal_basis: legalBasis ?? 'legitimate_interest',
        });
    }
    // ── Async GDPR export ─────────────────────────────────────────────────────
    async startGdprExport() {
        return this.request('POST', '/compliance/export/start');
    }
    async getGdprExportStatus(jobId) {
        return this.request('GET', `/compliance/export/jobs/${jobId}`);
    }
    // ── Golden tasks ──────────────────────────────────────────────────────────
    async listGoldenTasks(evalSuiteId) {
        return this.request('GET', `/eval/golden-tasks?eval_suite_id=${evalSuiteId}`);
    }
    // ── Goal evaluation ───────────────────────────────────────────────────────
    async getGoalEvaluation(goalId) {
        return this.request('GET', `/goals/${goalId}/evaluation`);
    }
    // ── Simulation (Phase 20) ─────────────────────────────────────────────────
    async simulate(goal, agentId) {
        return this.request('POST', '/enterprise/simulate', {
            goal,
            agent_id: agentId,
            dry_run: true,
        });
    }
    // ── Replay (Phase 20) ─────────────────────────────────────────────────────
    async replayGoal(goalId) {
        return this.request('GET', `/goals/${goalId}/replay`);
    }
    // ── HITL (Phase 20) ───────────────────────────────────────────────────────
    async getPendingApprovals() {
        return this.request('GET', '/governance/hitl/pending');
    }
    async approveRequest(requestId, note) {
        await this.request('POST', `/governance/hitl/${requestId}/approve`, {
            note: note ?? '',
        });
    }
    async rejectRequest(requestId, reason) {
        await this.request('POST', `/governance/hitl/${requestId}/reject`, {
            reason: reason ?? '',
        });
    }
    // ── Emergency controls (Phase 20) ────────────────────────────────────────
    async emergencyStop() {
        return this.request('POST', '/governance/emergency-stop');
    }
}
