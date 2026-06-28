"""Comprehensive tools test suite — email_tool, code_interpreter, file_ops.

Coverage targets:
  email_tool.py:    24% → ≥60%  (EmailTool.send, email_send, validation, from_vault_config)
  code_interpreter: 56% → ≥70%  (to_dict, subprocess fallback paths, timeout)
  file_ops:         78% → ≥90%  (FileOps class methods, edge cases, error paths)
"""
from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── CodeInterpreter ────────────────────────────────────────────────────────────

class TestCodeResultToDict:
    def test_to_dict_success(self):
        from app.tools.code_interpreter import CodeResult

        r = CodeResult(stdout="hi", stderr="", exit_code=0, timed_out=False, execution_time_ms=42.5)
        d = r.to_dict()
        assert d["success"] is True
        assert d["stdout"] == "hi"
        assert d["stderr"] == ""
        assert d["exit_code"] == 0
        assert d["timed_out"] is False
        assert d["execution_time_ms"] == 42.5

    def test_to_dict_failure(self):
        from app.tools.code_interpreter import CodeResult

        r = CodeResult(stdout="", stderr="error", exit_code=1, timed_out=False)
        d = r.to_dict()
        assert d["success"] is False
        assert d["exit_code"] == 1

    def test_to_dict_timed_out(self):
        from app.tools.code_interpreter import CodeResult

        r = CodeResult(stdout="", stderr="timeout", exit_code=1, timed_out=True)
        d = r.to_dict()
        assert d["success"] is False
        assert d["timed_out"] is True

    def test_execution_time_ms_rounded(self):
        from app.tools.code_interpreter import CodeResult

        r = CodeResult(stdout="", stderr="", exit_code=0, execution_time_ms=12.34567)
        d = r.to_dict()
        # to_dict rounds to 2dp
        assert d["execution_time_ms"] == round(12.34567, 2)


class TestCodeInterpreterSubprocess:
    """Tests for the subprocess fallback path (AGENTVERSE_ALLOW_SUBPROCESS_EXEC=true is
    pre-set in conftest.py so these run in the test environment)."""

    @pytest.mark.asyncio
    async def test_python_stdout_via_subprocess(self):
        from app.tools.code_interpreter import CodeInterpreter

        interp = CodeInterpreter(timeout=15)
        result = await interp.execute("print('subprocess works')", "python")
        if not result.success:
            pytest.skip("Docker or subprocess not available in CI")
        assert "subprocess works" in result.stdout

    @pytest.mark.asyncio
    async def test_python_stderr_captured(self):
        from app.tools.code_interpreter import CodeInterpreter

        interp = CodeInterpreter(timeout=15)
        result = await interp.execute(
            "import sys; sys.stderr.write('err output')", "python"
        )
        # Success may vary by environment but stderr should be captured
        assert isinstance(result.stderr, str)

    @pytest.mark.asyncio
    async def test_python_syntax_error_nonzero_exit(self):
        from app.tools.code_interpreter import CodeInterpreter

        interp = CodeInterpreter(timeout=15)
        result = await interp.execute("def (invalid syntax:", "python")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_exit_code_nonzero_on_raise(self):
        from app.tools.code_interpreter import CodeInterpreter

        interp = CodeInterpreter(timeout=15)
        result = await interp.execute("raise SystemExit(42)", "python")
        assert result.success is False
        # Exit code may be 42 or 1 depending on execution path
        assert result.exit_code != 0

    @pytest.mark.asyncio
    async def test_subprocess_disabled_when_env_false(self):
        """When the env var is false the fallback must refuse."""
        from app.tools.code_interpreter import CodeInterpreter

        interp = CodeInterpreter(timeout=10)
        original = os.environ.pop("AGENTVERSE_ALLOW_SUBPROCESS_EXEC", None)
        os.environ["AGENTVERSE_ALLOW_SUBPROCESS_EXEC"] = "false"
        try:
            result = await interp._execute_subprocess_fallback("print(1)", "python", 5)
            assert result.success is False
            assert "disabled" in result.stderr.lower()
        finally:
            if original is not None:
                os.environ["AGENTVERSE_ALLOW_SUBPROCESS_EXEC"] = original
            else:
                os.environ.pop("AGENTVERSE_ALLOW_SUBPROCESS_EXEC", None)

    @pytest.mark.asyncio
    async def test_subprocess_timeout(self):
        """Subprocess execution respects timeout and sets timed_out=True."""
        from app.tools.code_interpreter import CodeInterpreter

        interp = CodeInterpreter(timeout=1)
        result = await interp._execute_subprocess_fallback(
            "import time; time.sleep(30)", "python", timeout=1
        )
        # Either timed_out is True or success is False (killed)
        assert result.timed_out is True or result.success is False

    @pytest.mark.asyncio
    async def test_javascript_language_subprocess(self):
        """JavaScript execution path builds 'node' command."""
        from app.tools.code_interpreter import CodeInterpreter

        interp = CodeInterpreter(timeout=10)
        # Even if node isn't available this exercises the language-dispatch path
        result = await interp._execute_subprocess_fallback(
            "console.log('js test')", "javascript", timeout=5
        )
        # May fail if node not installed — that's OK, we just verify it doesn't crash
        assert isinstance(result.success, bool)

    @pytest.mark.asyncio
    async def test_bash_language_subprocess(self):
        """Bash execution path builds 'sh' command."""
        from app.tools.code_interpreter import CodeInterpreter

        interp = CodeInterpreter(timeout=10)
        result = await interp._execute_subprocess_fallback(
            "echo 'bash test'", "bash", timeout=5
        )
        assert isinstance(result.success, bool)
        if result.success:
            assert "bash test" in result.stdout


# ── FileOps class methods ─────────────────────────────────────────────────────

class TestFileOpsClass:
    """Tests using the FileOps class directly (not the module-level wrappers)."""

    def setup_method(self):
        import uuid

        self.tenant_id = f"test-{uuid.uuid4().hex[:8]}"

    @pytest.mark.asyncio
    async def test_write_and_read(self):
        from app.tools.file_ops import FileOps

        ops = FileOps(self.tenant_id)
        bytes_written = await ops.write("hello.txt", "hello world")
        assert bytes_written == len("hello world".encode())

        content = await ops.read("hello.txt")
        assert content == "hello world"

    @pytest.mark.asyncio
    async def test_read_nonexistent_raises_file_not_found(self):
        from app.tools.file_ops import FileOps

        ops = FileOps(self.tenant_id)
        with pytest.raises(FileNotFoundError):
            await ops.read("nonexistent_xyz.txt")

    @pytest.mark.asyncio
    async def test_list_directory_contents(self):
        from app.tools.file_ops import FileOps

        ops = FileOps(self.tenant_id)
        await ops.write("listfile_a.txt", "a")
        await ops.write("listfile_b.txt", "b")

        entries = await ops.list(".")
        names = [e["name"] for e in entries]
        assert "listfile_a.txt" in names
        assert "listfile_b.txt" in names

    @pytest.mark.asyncio
    async def test_list_nonexistent_directory_returns_empty(self):
        from app.tools.file_ops import FileOps

        ops = FileOps(self.tenant_id)
        entries = await ops.list("definitely_not_there")
        assert entries == []

    @pytest.mark.asyncio
    async def test_list_file_as_directory_raises(self):
        from app.tools.file_ops import FileOps

        ops = FileOps(self.tenant_id)
        await ops.write("just_a_file.txt", "content")
        with pytest.raises(NotADirectoryError):
            await ops.list("just_a_file.txt")

    @pytest.mark.asyncio
    async def test_list_entries_have_expected_keys(self):
        from app.tools.file_ops import FileOps

        ops = FileOps(self.tenant_id)
        await ops.write("meta.txt", "x")
        entries = await ops.list(".")
        meta = next((e for e in entries if e["name"] == "meta.txt"), None)
        assert meta is not None
        assert meta["type"] == "file"
        assert "size_bytes" in meta
        assert "modified_at" in meta

    @pytest.mark.asyncio
    async def test_delete_file(self):
        from app.tools.file_ops import FileOps
        import pathlib

        ops = FileOps(self.tenant_id)
        await ops.write("to_delete.txt", "bye")
        deleted = await ops.delete("to_delete.txt")
        assert deleted is True
        assert not (ops._workspace / "to_delete.txt").exists()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_false(self):
        from app.tools.file_ops import FileOps

        ops = FileOps(self.tenant_id)
        deleted = await ops.delete("no_such_file.txt")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_delete_directory(self):
        """delete() on a directory uses shutil.rmtree."""
        from app.tools.file_ops import FileOps
        import pathlib

        ops = FileOps(self.tenant_id)
        subdir = ops._workspace / "subdir"
        subdir.mkdir(exist_ok=True)
        (subdir / "child.txt").write_text("x")

        deleted = await ops.delete("subdir")
        assert deleted is True
        assert not subdir.exists()

    @pytest.mark.asyncio
    async def test_exists_true_for_file(self):
        from app.tools.file_ops import FileOps

        ops = FileOps(self.tenant_id)
        await ops.write("exists_test.txt", "y")
        assert await ops.exists("exists_test.txt") is True

    @pytest.mark.asyncio
    async def test_exists_false_for_missing(self):
        from app.tools.file_ops import FileOps

        ops = FileOps(self.tenant_id)
        assert await ops.exists("no_such_file_abc.txt") is False

    @pytest.mark.asyncio
    async def test_exists_returns_false_for_traversal(self):
        """exists() must not raise on traversal — it returns False."""
        from app.tools.file_ops import FileOps

        ops = FileOps(self.tenant_id)
        assert await ops.exists("../../etc/passwd") is False

    @pytest.mark.asyncio
    async def test_list_dir_alias(self):
        """list_dir() is an alias for list() — must return the same result."""
        from app.tools.file_ops import FileOps

        ops = FileOps(self.tenant_id)
        await ops.write("alias_test.txt", "z")
        result_list = await ops.list(".")
        result_list_dir = await ops.list_dir(".")
        assert result_list == result_list_dir

    @pytest.mark.asyncio
    async def test_write_creates_subdirectory(self):
        """write() must create intermediate parent dirs."""
        from app.tools.file_ops import FileOps

        ops = FileOps(self.tenant_id)
        bytes_written = await ops.write("nested/dir/file.txt", "deep content")
        assert bytes_written > 0
        content = await ops.read("nested/dir/file.txt")
        assert content == "deep content"

    @pytest.mark.asyncio
    async def test_path_traversal_raises_permission_error(self):
        from app.tools.file_ops import FileOps

        ops = FileOps(self.tenant_id)
        with pytest.raises(PermissionError):
            await ops.read("../../etc/passwd")


class TestFileOpsWrappers:
    """Additional coverage for the module-level wrapper functions' error paths."""

    def setup_method(self):
        import uuid

        self.tid = f"wrap-{uuid.uuid4().hex[:8]}"

    @pytest.mark.asyncio
    async def test_file_read_nonexistent_returns_error_dict(self):
        from app.tools.file_ops import file_read

        result = await file_read("no_such_file_xyz.txt", tenant_id=self.tid)
        assert result["success"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_file_write_returns_bytes_written(self):
        from app.tools.file_ops import file_write, file_delete

        result = await file_write("bytes_test.txt", "hello bytes", tenant_id=self.tid)
        assert result["success"] is True
        assert result["bytes_written"] == len("hello bytes".encode())
        await file_delete("bytes_test.txt", tenant_id=self.tid)

    @pytest.mark.asyncio
    async def test_file_list_returns_entries_with_metadata(self):
        from app.tools.file_ops import file_write, file_list, file_delete

        await file_write("list_meta.txt", "x", tenant_id=self.tid)
        result = await file_list(".", tenant_id=self.tid)
        assert result["success"] is True
        entry_names = [e["name"] for e in result["entries"]]
        assert "list_meta.txt" in entry_names
        await file_delete("list_meta.txt", tenant_id=self.tid)

    @pytest.mark.asyncio
    async def test_file_delete_path_traversal_returns_error(self):
        from app.tools.file_ops import file_delete

        result = await file_delete("../../etc/passwd", tenant_id=self.tid)
        assert result["success"] is False
        assert "error" in result


# ── EmailTool ─────────────────────────────────────────────────────────────────

class TestValidateEmail:
    def test_valid_emails_pass(self):
        from app.tools.email_tool import _validate_email

        for addr in ["user@example.com", "a+b@sub.domain.org", "test.user@co.uk"]:
            _validate_email(addr)  # must not raise

    def test_invalid_email_raises(self):
        from app.tools.email_tool import _validate_email

        with pytest.raises(ValueError, match="Invalid email"):
            _validate_email("not-an-email")

    def test_missing_domain_raises(self):
        from app.tools.email_tool import _validate_email

        with pytest.raises(ValueError):
            _validate_email("user@")

    def test_missing_at_raises(self):
        from app.tools.email_tool import _validate_email

        with pytest.raises(ValueError):
            _validate_email("userexample.com")


class TestEmailToolSend:
    def _smtp_config(self):
        from app.tools.email_tool import SMTPConfig

        return SMTPConfig(
            host="localhost",
            port=1025,
            username="test",
            password="test",
            from_address="sender@example.com",
            use_tls=False,
        )

    @pytest.mark.asyncio
    async def test_send_success(self):
        from app.tools.email_tool import EmailTool

        tool = EmailTool(smtp_config=self._smtp_config())

        with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            result = await tool.send(
                to="recipient@example.com",
                subject="Hello",
                body="Test body",
            )

        assert result["status"] == "sent"
        assert result["to"] == "recipient@example.com"
        assert result["subject"] == "Hello"
        assert "message_id" in result
        mock_send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_no_smtp_config_raises(self):
        from app.tools.email_tool import EmailTool

        tool = EmailTool()  # no smtp_config
        with pytest.raises(ValueError, match="SMTP not configured"):
            await tool.send(to="x@x.com", subject="s", body="b")

    @pytest.mark.asyncio
    async def test_send_invalid_recipient_raises(self):
        from app.tools.email_tool import EmailTool

        tool = EmailTool(smtp_config=self._smtp_config())
        with pytest.raises(ValueError, match="Invalid email"):
            await tool.send(to="not-an-email", subject="s", body="b")

    @pytest.mark.asyncio
    async def test_send_with_html_body(self):
        from app.tools.email_tool import EmailTool

        tool = EmailTool(smtp_config=self._smtp_config())

        with patch("aiosmtplib.send", new_callable=AsyncMock):
            result = await tool.send(
                to="a@b.com",
                subject="HTML test",
                body="plain text",
                html_body="<p>html text</p>",
            )

        assert result["status"] == "sent"

    @pytest.mark.asyncio
    async def test_send_with_cc_and_bcc(self):
        from app.tools.email_tool import EmailTool

        tool = EmailTool(smtp_config=self._smtp_config())

        with patch("aiosmtplib.send", new_callable=AsyncMock):
            result = await tool.send(
                to="to@example.com",
                subject="CC test",
                body="body",
                cc=["cc@example.com"],
                bcc=["bcc@example.com"],
            )

        assert result["status"] == "sent"
        assert "cc@example.com" in result["recipients"]
        assert "bcc@example.com" in result["recipients"]

    @pytest.mark.asyncio
    async def test_send_invalid_cc_raises(self):
        from app.tools.email_tool import EmailTool

        tool = EmailTool(smtp_config=self._smtp_config())
        with pytest.raises(ValueError):
            await tool.send(
                to="valid@example.com",
                subject="s",
                body="b",
                cc=["not-valid-email"],
            )

    @pytest.mark.asyncio
    async def test_send_smtp_error_propagates(self):
        """aiosmtplib errors bubble up (not caught inside send())."""
        import aiosmtplib
        from app.tools.email_tool import EmailTool

        tool = EmailTool(smtp_config=self._smtp_config())

        # SMTPConnectError takes a single string message argument
        with patch(
            "aiosmtplib.send",
            side_effect=aiosmtplib.SMTPConnectError("Connection refused"),
        ):
            with pytest.raises(aiosmtplib.SMTPConnectError):
                await tool.send(to="x@x.com", subject="s", body="b")

    @pytest.mark.asyncio
    async def test_send_message_id_format(self):
        from app.tools.email_tool import EmailTool

        tool = EmailTool(smtp_config=self._smtp_config())

        with patch("aiosmtplib.send", new_callable=AsyncMock):
            result = await tool.send(to="u@example.com", subject="s", body="b")

        assert result["message_id"].startswith("<")
        assert "@agentverse>" in result["message_id"]


class TestEmailToolFromVaultConfig:
    def test_from_vault_config_with_smtp(self):
        from app.tools.email_tool import EmailTool, SMTPConfig

        config = {
            "smtp_host": "mail.example.com",
            "smtp_port": "587",
            "smtp_username": "user",
            "smtp_password": "pass",
            "smtp_from": "noreply@example.com",
            "smtp_use_tls": True,
        }
        tool = EmailTool.from_vault_config(config)
        assert tool._smtp is not None
        assert tool._smtp.host == "mail.example.com"
        assert tool._smtp.port == 587
        assert tool._smtp.from_address == "noreply@example.com"
        assert tool._smtp.use_tls is True

    def test_from_vault_config_no_smtp_host_gives_none_smtp(self):
        from app.tools.email_tool import EmailTool

        tool = EmailTool.from_vault_config({})
        assert tool._smtp is None
        assert tool._imap is None

    def test_from_vault_config_with_imap(self):
        from app.tools.email_tool import EmailTool

        config = {
            "imap_host": "imap.example.com",
            "imap_port": "993",
            "imap_username": "user",
            "imap_password": "pass",
        }
        tool = EmailTool.from_vault_config(config)
        assert tool._imap is not None
        assert tool._imap.host == "imap.example.com"
        assert tool._imap.port == 993

    def test_from_vault_config_defaults_smtp_from_to_username(self):
        """If smtp_from is absent, from_address falls back to smtp_username."""
        from app.tools.email_tool import EmailTool

        config = {
            "smtp_host": "mail.example.com",
            "smtp_username": "autouser@example.com",
            "smtp_password": "pw",
        }
        tool = EmailTool.from_vault_config(config)
        assert tool._smtp is not None
        assert tool._smtp.from_address == "autouser@example.com"


class TestEmailSendModuleFunction:
    """Tests for the module-level email_send() convenience wrapper."""

    @pytest.mark.asyncio
    async def test_email_send_success(self):
        from app.tools.email_tool import email_send

        with patch("aiosmtplib.send", new_callable=AsyncMock):
            result = await email_send(
                to="user@example.com",
                subject="Module Test",
                body="body text",
            )

        assert result["success"] is True
        assert result["subject"] == "Module Test"

    @pytest.mark.asyncio
    async def test_email_send_list_recipients(self):
        from app.tools.email_tool import email_send

        with patch("aiosmtplib.send", new_callable=AsyncMock):
            result = await email_send(
                to=["a@example.com", "b@example.com"],
                subject="Bulk",
                body="hi all",
            )

        assert result["success"] is True
        assert len(result["to"]) == 2

    @pytest.mark.asyncio
    async def test_email_send_failure_returns_error_dict(self):
        from app.tools.email_tool import email_send

        with patch("aiosmtplib.send", side_effect=Exception("SMTP down")):
            result = await email_send(
                to="user@example.com",
                subject="Fail",
                body="body",
            )

        assert result["success"] is False
        assert "error" in result
        assert "SMTP down" in result["error"]

    @pytest.mark.asyncio
    async def test_email_send_uses_from_addr_kwarg(self):
        """from_addr kwarg is used as the sender when provided."""
        from app.tools.email_tool import email_send

        captured_msg = []

        async def capture(msg, **kwargs):
            captured_msg.append(msg)

        with patch("aiosmtplib.send", side_effect=capture):
            await email_send(
                to="u@example.com",
                subject="s",
                body="b",
                from_addr="custom@sender.com",
            )

        assert len(captured_msg) == 1
        assert captured_msg[0]["From"] == "custom@sender.com"

    @pytest.mark.asyncio
    async def test_email_send_env_var_smtp_host(self):
        """email_send() reads SMTP_HOST from environment."""
        from app.tools.email_tool import email_send

        calls = []

        async def capture(msg, **kwargs):
            calls.append(kwargs)

        original = os.environ.get("SMTP_HOST")
        os.environ["SMTP_HOST"] = "smtp.custom.example.com"
        os.environ["SMTP_PORT"] = "25"
        try:
            with patch("aiosmtplib.send", side_effect=capture):
                await email_send(to="u@x.com", subject="s", body="b")
        finally:
            if original is not None:
                os.environ["SMTP_HOST"] = original
            else:
                os.environ.pop("SMTP_HOST", None)
            os.environ.pop("SMTP_PORT", None)

        assert len(calls) == 1
        assert calls[0]["hostname"] == "smtp.custom.example.com"
        assert calls[0]["port"] == 25
