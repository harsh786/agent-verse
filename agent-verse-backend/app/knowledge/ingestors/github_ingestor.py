"""GitHub repository ingestor — crawls code/docs via GitHub REST API."""
from __future__ import annotations
import os
from typing import Any
import httpx
from app.observability.logging import get_logger

logger = get_logger(__name__)

_SKIP_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
    ".bin", ".zip", ".tar", ".gz", ".pdf", ".woff", ".ttf",
    ".mp4", ".mp3", ".avi", ".mov", ".exe", ".dll", ".so", ".dylib",
})
_SKIP_DIRS = frozenset({
    ".git", ".github", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", "coverage", ".cache", "vendor",
})
_TEXT_EXTENSIONS = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".md", ".mdx", ".txt", ".rst", ".adoc",
    ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".env.example",
    ".sh", ".bash", ".zsh", ".fish", ".go", ".rs", ".java", ".rb",
    ".php", ".cs", ".cpp", ".c", ".h", ".hpp", ".kt", ".swift",
    ".html", ".htm", ".css", ".scss", ".less", ".sql", ".graphql",
})
_CHUNK_SIZE = 1500
_CHUNK_OVERLAP = 100


class GitHubIngestor:
    def __init__(self, token: str | None = None) -> None:
        self._token = token or os.getenv("GITHUB_TOKEN", "")

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    async def _get_tree(self, owner: str, repo: str) -> list[dict]:
        url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/HEAD?recursive=1"
        async with httpx.AsyncClient(timeout=30, headers=self._headers()) as c:
            r = await c.get(url)
            r.raise_for_status()
            data = r.json()
            if data.get("truncated"):
                logger.warning("github_tree_truncated", owner=owner, repo=repo)
            return data.get("tree", [])

    async def _fetch_file_content(self, owner: str, repo: str, path: str) -> str:
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/{path}"
        async with httpx.AsyncClient(timeout=15, headers=self._headers()) as c:
            r = await c.get(url)
            r.raise_for_status()
            return r.text[:100_000]  # max 100KB per file

    def _should_ingest(self, path: str) -> bool:
        parts = path.split("/")
        if any(p in _SKIP_DIRS for p in parts):
            return False
        if "." in path:
            ext = "." + path.rsplit(".", 1)[-1].lower()
            if ext in _SKIP_EXTENSIONS:
                return False
            if ext not in _TEXT_EXTENSIONS:
                return False
        return True

    async def ingest_repo(
        self, owner: str, repo: str, *,
        branch: str = "HEAD", max_files: int = 300,
        file_patterns: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        tree = await self._get_tree(owner, repo)
        chunks: list[dict[str, Any]] = []
        file_count = 0

        for item in tree:
            if file_count >= max_files:
                break
            if item.get("type") != "blob":
                continue
            path = item.get("path", "")
            if not self._should_ingest(path):
                continue

            try:
                content = await self._fetch_file_content(owner, repo, path)
                content = content.strip()
                if len(content) < 50:
                    continue

                source_url = f"https://github.com/{owner}/{repo}/blob/{branch}/{path}"
                source_doc_id = f"{owner}/{repo}/{path}"

                # Sliding window chunks
                start = 0
                while start < len(content):
                    chunk = content[start:start + _CHUNK_SIZE]
                    if len(chunk.strip()) >= 50:
                        chunks.append({
                            "content": chunk,
                            "source_url": source_url,
                            "source_type": "github",
                            "source_doc_id": source_doc_id,
                            "page_number": None,
                            "metadata": {
                                "owner": owner, "repo": repo,
                                "path": path, "branch": branch,
                                "size": item.get("size", 0),
                            },
                        })
                    start += _CHUNK_SIZE - _CHUNK_OVERLAP
                file_count += 1
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    continue  # File deleted between tree fetch and content fetch
                logger.warning("github_file_fetch_error", path=path, status=e.response.status_code)
            except Exception as exc:
                logger.warning("github_file_fetch_failed", path=path, error=str(exc))

        logger.info("github_repo_ingested", owner=owner, repo=repo, files=file_count, chunks=len(chunks))
        return chunks
