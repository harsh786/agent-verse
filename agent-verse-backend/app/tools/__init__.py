"""Native tool implementations for AgentVerse.

These tools are built-in and do not require MCP server registrations.
Each tool is tenant-scoped for isolation.
"""

from app.tools.code_interpreter import CodeInterpreter, CodeResult, get_interpreter
from app.tools.document_parser import DocumentParserTool, ParsedDocument
from app.tools.email_tool import EmailTool, IMAPConfig, SMTPConfig
from app.tools.file_ops import FileOps
from app.tools.http_tool import HttpRequestTool
from app.tools.shell_tool import ShellTool
from app.tools.web_search import WebSearchTool

__all__ = [
    # code interpreter
    "CodeInterpreter",
    "CodeResult",
    "get_interpreter",
    # document parser
    "DocumentParserTool",
    "ParsedDocument",
    # email
    "EmailTool",
    "IMAPConfig",
    "SMTPConfig",
    # file ops
    "FileOps",
    # http
    "HttpRequestTool",
    # shell
    "ShellTool",
    # web search
    "WebSearchTool",
]
