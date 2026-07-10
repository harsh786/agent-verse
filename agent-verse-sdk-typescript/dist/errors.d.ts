export declare class AgentVerseError extends Error {
    statusCode: number;
    constructor(message: string, statusCode?: number);
}
export declare class AuthError extends AgentVerseError {
    constructor(message?: string);
}
export declare class GoalFailedError extends AgentVerseError {
    goalId: string;
    reason: string;
    constructor(goalId: string, reason: string);
}
export declare class NotFoundError extends AgentVerseError {
    constructor(resource: string);
}
export declare class GoalTimeoutError extends AgentVerseError {
    constructor(goalId: string, timeoutSeconds: number);
}
