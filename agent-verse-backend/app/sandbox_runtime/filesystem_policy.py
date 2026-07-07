from __future__ import annotations
import enum


class FilesystemMode(str, enum.Enum):
    READ_ONLY = "read_only"
    WORKSPACE = "workspace"
    EPHEMERAL = "ephemeral"


class FilesystemPolicy:
    def __init__(
        self,
        mode: FilesystemMode = FilesystemMode.READ_ONLY,
        workspace: str = "",
    ) -> None:
        self._mode = mode
        self._workspace = workspace

    def can_read(self, path: str) -> bool:
        return True

    def can_write(self, path: str) -> bool:
        if self._mode == FilesystemMode.READ_ONLY:
            return False
        if self._mode == FilesystemMode.EPHEMERAL and self._workspace:
            return path.startswith(self._workspace)
        return True
