"""Typed exceptions for the org domain."""
from __future__ import annotations


class OrgNotFoundError(Exception):
    def __init__(self, org_id: str) -> None:
        self.org_id = org_id
        super().__init__(f"Organization {org_id!r} not found")


class OrgDepartmentNotFoundError(Exception):
    def __init__(self, dept_id: str) -> None:
        self.dept_id = dept_id
        super().__init__(f"Department {dept_id!r} not found")


class OrgMissionNotFoundError(Exception):
    def __init__(self, mission_id: str) -> None:
        self.mission_id = mission_id
        super().__init__(f"Mission {mission_id!r} not found")


class OrgTaskNotFoundError(Exception):
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"Task {task_id!r} not found")


class OrgTaskDepthExceededError(Exception):
    """Raised when task depth exceeds anti-runaway limit."""
    def __init__(self, depth: int, max_depth: int) -> None:
        super().__init__(f"Task depth {depth} exceeds maximum {max_depth}")


class OrgTaskLimitExceededError(Exception):
    """Raised when per-mission task count exceeds limit."""
    def __init__(self, count: int, limit: int) -> None:
        super().__init__(f"Mission has {count} tasks, limit is {limit}")


class OrgInvalidStatusError(Exception):
    """Raised when an invalid status transition is attempted."""
    def __init__(self, resource: str, status: str) -> None:
        super().__init__(f"Invalid status {status!r} for {resource}")
