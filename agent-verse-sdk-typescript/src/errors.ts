export class AgentVerseError extends Error {
  constructor(message: string, public statusCode: number = 0) {
    super(message);
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
  constructor(public goalId: string, public reason: string) {
    super(`Goal ${goalId} failed: ${reason}`);
    this.name = 'GoalFailedError';
  }
}

export class NotFoundError extends AgentVerseError {
  constructor(resource: string) {
    super(`Not found: ${resource}`, 404);
    this.name = 'NotFoundError';
  }
}

export class GoalTimeoutError extends AgentVerseError {
  constructor(goalId: string, timeoutSeconds: number) {
    super(`Goal ${goalId} timed out after ${timeoutSeconds}s`);
    this.name = 'GoalTimeoutError';
  }
}
