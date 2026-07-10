import type { Agent, AgentSnapshot, Connector, ConnectorSpec, ConnectorTestResult, ConsentRecord, CostMetrics, CreateScheduleRequest, EvalScorecard, GdprExportJob, Goal, GoalEvent, GoalMetrics, GoalTimeline, GoldenTask, Memory, RolloutGateResult, Schedule, SearchResult, SimulationResult, SubmitGoalOptions, ToolReliabilityStats, UpdateAgentRequest } from './types.js';
export declare class AgentVerseClient {
    private readonly baseUrl;
    private readonly apiKey;
    constructor(apiKey: string, baseUrl?: string);
    private get headers();
    private request;
    submitGoal(goal: string, options?: SubmitGoalOptions): Promise<Goal>;
    getGoal(goalId: string): Promise<Goal>;
    listGoals(): Promise<Goal[]>;
    cancelGoal(goalId: string): Promise<void>;
    waitForGoal(goalId: string, options?: {
        timeout?: number;
        pollInterval?: number;
    }): Promise<Goal>;
    streamGoal(goalId: string): AsyncGenerator<GoalEvent>;
    createAgent(name: string, goalTemplate?: string, extra?: Record<string, unknown>): Promise<Agent>;
    getAgent(agentId: string): Promise<Agent>;
    updateAgent(agentId: string, data: UpdateAgentRequest): Promise<Agent>;
    runAgent(agentId: string, goal: string, options?: {
        dryRun?: boolean;
        autonomyMode?: string;
    }): Promise<Goal>;
    listAgents(): Promise<Agent[]>;
    deleteAgent(agentId: string): Promise<void>;
    snapshotAgent(agentId: string): Promise<AgentSnapshot>;
    listAgentVersions(agentId: string): Promise<AgentSnapshot[]>;
    rollbackAgent(agentId: string, snapshotId: string): Promise<Agent>;
    listConnectors(): Promise<Connector[]>;
    registerConnector(name: string, url: string, authType?: string, authConfig?: Record<string, string>): Promise<Connector>;
    deleteConnector(serverId: string): Promise<void>;
    testConnector(serverId: string): Promise<ConnectorTestResult>;
    getConnectorCatalog(): Promise<ConnectorSpec[]>;
    listSchedules(): Promise<Schedule[]>;
    createSchedule(data: CreateScheduleRequest): Promise<Schedule>;
    deleteSchedule(scheduleId: string): Promise<void>;
    createScheduleNl(command: string): Promise<Schedule>;
    recallMemory(query: string, limit?: number): Promise<Memory[]>;
    storeMemory(content: string, tags?: string[]): Promise<Memory>;
    searchKnowledge(collectionId: string, query: string, limit?: number): Promise<SearchResult[]>;
    getGoalMetrics(days?: number): Promise<GoalMetrics>;
    getCostMetrics(days?: number): Promise<CostMetrics>;
    getToolReliability(): Promise<ToolReliabilityStats[]>;
    checkRolloutGate(agentId: string, evalSuiteId?: string): Promise<RolloutGateResult>;
    recordConsent(purpose: string, legalBasis?: string): Promise<ConsentRecord>;
    startGdprExport(): Promise<GdprExportJob>;
    getGdprExportStatus(jobId: string): Promise<GdprExportJob>;
    listGoldenTasks(evalSuiteId: string): Promise<GoldenTask[]>;
    getGoalEvaluation(goalId: string): Promise<EvalScorecard>;
    simulate(goal: string, agentId?: string): Promise<SimulationResult>;
    replayGoal(goalId: string): Promise<GoalTimeline>;
    getPendingApprovals(): Promise<any[]>;
    approveRequest(requestId: string, note?: string): Promise<void>;
    rejectRequest(requestId: string, reason?: string): Promise<void>;
    emergencyStop(): Promise<{
        cancelled_goals: number;
        status: string;
    }>;
}
