export class AgentVerseError extends Error {
    constructor(message, statusCode = 0) {
        super(message);
        this.statusCode = statusCode;
        this.name = 'AgentVerseError';
    }
}
export class AuthError extends AgentVerseError {
    constructor(message = 'Invalid API key') {
        super(message, 401);
        this.name = 'AuthError';
    }
}
export class GoalFailedError extends AgentVerseError {
    constructor(goalId, reason) {
        super(`Goal ${goalId} failed: ${reason}`);
        this.goalId = goalId;
        this.reason = reason;
        this.name = 'GoalFailedError';
    }
}
export class NotFoundError extends AgentVerseError {
    constructor(resource) {
        super(`Not found: ${resource}`, 404);
        this.name = 'NotFoundError';
    }
}
export class GoalTimeoutError extends AgentVerseError {
    constructor(goalId, timeoutSeconds) {
        super(`Goal ${goalId} timed out after ${timeoutSeconds}s`);
        this.name = 'GoalTimeoutError';
    }
}
