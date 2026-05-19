"""
Движок выполнения логических узлов Python
"""

import io
import contextlib
import os
import asyncio
import logging
import traceback
from typing import List, Callable, Dict, Any, Tuple

logger = logging.getLogger(__name__)


class ScriptEngine:
    def __init__(self, scripts_dir: str, shared_state=None, storage_data=None) -> None:
        self.scripts_dir = scripts_dir
        os.makedirs(self.scripts_dir, exist_ok=True)
        self.active_script_name: str = ""
        self.active_func: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]] = (
            lambda data, ctx: data
        )

        self.shared_context: Dict[str, Any] = {
            "shared_state": shared_state if shared_state is not None else {},
            "history": storage_data if storage_data is not None else [],
            "os": os,
            "asyncio": asyncio,
        }

    def _sanitize_path(self, raw_filename: str) -> str:
        if not raw_filename:
            return "default_unnamed.py"
        safe_name = os.path.basename(raw_filename)
        if not safe_name.endswith(".py"):
            safe_name += ".py"
        return safe_name

    async def _format_code(self, filepath: str) -> str:
        """линтинг и форматирование через Ruff."""
        try:
            # Линтер
            proc_chk = await asyncio.create_subprocess_exec(
                "ruff",
                "check",
                "--select",
                "I",
                "--fix",
                filepath,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            chk_stdout, chk_stderr = await proc_chk.communicate()

            # Форматтер
            proc_fmt = await asyncio.create_subprocess_exec(
                "ruff",
                "format",
                filepath,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            fmt_stdout, fmt_stderr = await proc_fmt.communicate()

            logs = (
                f"[Линтер Ruff]\n{chk_stdout.decode('utf-8')}{chk_stderr.decode('utf-8')}\n"
                f"[Форматирование Ruff]\n{fmt_stdout.decode('utf-8')}{fmt_stderr.decode('utf-8')}"
            )
            return logs.strip()
        except Exception as e:
            return (
                f"Критическая ошибка запуска инструмента Ruff (Установлен ли он?): {e}"
            )

    async def get_list(self) -> List[str]:
        def _get():
            return sorted(
                [f for f in os.listdir(self.scripts_dir) if f.endswith(".py")]
            )

        return await asyncio.to_thread(_get)

    async def read_file(self, raw_filename: str) -> str:
        filepath = os.path.join(self.scripts_dir, self._sanitize_path(raw_filename))

        def _read():
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    return f.read()
            except FileNotFoundError:
                return ""

        return await asyncio.to_thread(_read)

    async def remove_file(self, raw_filename: str) -> None:
        safe_name = self._sanitize_path(raw_filename)
        path = os.path.join(self.scripts_dir, safe_name)
        try:
            await asyncio.to_thread(lambda: os.path.isfile(path) and os.remove(path))
        except Exception as e:
            logger.error("Не удалось удалить: %s", e)

    async def save_and_format(self, raw_filename: str, content: str) -> Tuple[str, str]:
        safe_name = self._sanitize_path(raw_filename)
        filepath = os.path.join(self.scripts_dir, safe_name)
        await asyncio.to_thread(
            lambda: open(filepath, "w", encoding="utf-8").write(content)
        )
        ruff_logs = await self._format_code(filepath)
        success, compilation_logs = await self.set_active_script(safe_name)
        final_code = await self.read_file(safe_name)

        total_logs = (
            f"{ruff_logs}\n\n[РЕЗУЛЬТАТ КОМПИЛЯЦИИ СЕТАПА]:\n{compilation_logs}"
        )
        return final_code, total_logs

    async def set_active_script(self, raw_filename: str) -> Tuple[bool, str]:
        safe_name = self._sanitize_path(raw_filename)
        code = await self.read_file(safe_name)
        if not code:
            return False, "Файл пуст"

        def _compile_and_set():
            namespace: dict = {}
            exec_globals = {"__builtins__": __builtins__}
            try:
                exec(code, {}, namespace)
                if "process" in namespace:
                    self.active_func = namespace["process"]
                    self.active_script_name = safe_name
                    logger.info("Активирован обработчик пакетов: %s", safe_name)
                    return True, "Успешно: функция process() подхвачена ядром шлюза!"
                return (
                    False,
                    "ОШИБКА: в скрипте отсутствует основная функция process(data, context).",
                )
            except Exception:
                return False, traceback.format_exc()

        try:
            return await asyncio.to_thread(_compile_and_set)
        except Exception as e:
            logger.error(
                "Синтаксическая ошибка при компиляции скрипта %s: %s", safe_name, e
            )
            return False, "Синтаксическая ошибка при компиляции скрипта"

    async def execute(self, data: dict) -> dict:
        """Применяет активный скрипт."""

        def _exec_wrapper():
            try:
                return self.active_func(data, self.shared_context)
            except Exception as e:
                logger.error("Ошибка при обработке пакета скриптом: %s", e)
                return data

        return await asyncio.to_thread(_exec_wrapper)

    async def dry_run(self, code: str, test_input: Dict[str, Any]):
        """тестовый запуск скрипта с перехватом консоли"""

        def _run_test():
            output_buffer = io.StringIO()
            exec_globals = {"__builtins__": __builtins__}
            local_env = {"data": test_input, "result": {}}
            try:
                with contextlib.redirect_stdout(
                    output_buffer
                ), contextlib.redirect_stderr(output_buffer):
                    exec(code, exec_globals, local_env)

                if "process" in local_env and callable(local_env["process"]):
                    result = local_env["process"](test_input, self.shared_context)
                else:
                    result = local_env.get("result", {})

                return result, output_buffer.getvalue() or "Нет вывода"
            except Exception as e:
                return (
                    {},
                    output_buffer.getvalue() + f"\nКритическая ошибка выполнения: {e}",
                )

        return await asyncio.to_thread(_run_test)
