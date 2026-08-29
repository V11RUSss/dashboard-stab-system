"""Storage, formatting and isolated execution of user Python scripts."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time

logger = logging.getLogger(__name__)


class ScriptEngine:
    RUN_TIMEOUT_S = 5.0
    OUTPUT_LIMIT_BYTES = 100_000

    def __init__(self, scripts_dir: str, shared_state=None, storage_data=None) -> None:
        # shared_state/storage_data are retained in the constructor for API
        # compatibility, but plain scripts are deliberately not coupled to
        # telemetry or backend internals.
        self.scripts_dir = os.path.abspath(scripts_dir)
        os.makedirs(self.scripts_dir, exist_ok=True)

    def _sanitize_path(self, raw_filename: str | None) -> str:
        if not raw_filename:
            return "default_unnamed.py"
        safe_name = os.path.basename(raw_filename)
        if not safe_name.endswith(".py"):
            safe_name += ".py"
        return safe_name

    async def _format_code(self, filepath: str) -> str:
        """Run Ruff import sorting and formatting without executing the file."""
        logs = []
        commands = (
            ("Линтер Ruff", "ruff", "check", "--select", "I", "--fix", filepath),
            ("Форматирование Ruff", "ruff", "format", filepath),
        )
        for title, *command in commands:
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
                text = (stdout + stderr).decode("utf-8", errors="replace").strip()
                logs.append(f"[{title}] exit={process.returncode}\n{text or 'OK'}")
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                logs.append(f"[{title}] превышен таймаут")
            except OSError as exc:
                logs.append(f"[{title}] не удалось запустить: {exc}")
        return "\n\n".join(logs)

    async def get_list(self) -> list[str]:
        def _get() -> list[str]:
            return sorted(f for f in os.listdir(self.scripts_dir) if f.endswith(".py"))

        return await asyncio.to_thread(_get)

    async def read_file(self, raw_filename: str | None) -> str:
        filepath = os.path.join(self.scripts_dir, self._sanitize_path(raw_filename))

        def _read() -> str:
            try:
                with open(filepath, "r", encoding="utf-8") as file:
                    return file.read()
            except FileNotFoundError:
                return ""

        return await asyncio.to_thread(_read)

    async def remove_file(self, raw_filename: str | None) -> None:
        path = os.path.join(self.scripts_dir, self._sanitize_path(raw_filename))

        def _remove() -> None:
            if os.path.isfile(path):
                os.remove(path)

        await asyncio.to_thread(_remove)

    async def save_and_format(
        self, raw_filename: str | None, content: str | None
    ) -> tuple[str, str]:
        safe_name = self._sanitize_path(raw_filename)
        filepath = os.path.join(self.scripts_dir, safe_name)
        source = content or ""

        def _write() -> None:
            with open(filepath, "w", encoding="utf-8") as file:
                file.write(source)

        await asyncio.to_thread(_write)
        formatter_logs = await self._format_code(filepath)
        final_code = await self.read_file(safe_name)
        return final_code, formatter_logs

    async def _read_limited(self, stream: asyncio.StreamReader) -> tuple[bytes, bool]:
        chunks = []
        retained = 0
        truncated = False
        while True:
            chunk = await stream.read(8192)
            if not chunk:
                break
            remaining = self.OUTPUT_LIMIT_BYTES - retained
            if remaining > 0:
                chunks.append(chunk[:remaining])
                retained += min(len(chunk), remaining)
            if len(chunk) > remaining:
                truncated = True
        return b"".join(chunks), truncated

    async def run_code(self, code: str | None) -> dict:
        """Execute plain Python in a child process and capture its result."""
        if not isinstance(code, str) or not code.strip():
            return {
                "stdout": "",
                "stderr": "Код пуст",
                "exit_code": None,
                "timed_out": False,
                "duration_ms": 0,
            }

        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-c",
            code,
            cwd=self.scripts_dir,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_task = asyncio.create_task(self._read_limited(process.stdout))
        stderr_task = asyncio.create_task(self._read_limited(process.stderr))
        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=self.RUN_TIMEOUT_S)
        except asyncio.TimeoutError:
            timed_out = True
            process.kill()
            await process.wait()

        (stdout, stdout_truncated), (stderr, stderr_truncated) = await asyncio.gather(
            stdout_task, stderr_task
        )
        duration_ms = round((time.monotonic() - started) * 1000)

        def _decode(value: bytes, truncated: bool) -> str:
            result = value.decode("utf-8", errors="replace")
            if truncated:
                result += "\n[вывод обрезан до 100000 байт]"
            return result

        return {
            "stdout": _decode(stdout, stdout_truncated),
            "stderr": _decode(stderr, stderr_truncated),
            "exit_code": process.returncode,
            "timed_out": timed_out,
            "duration_ms": duration_ms,
        }
