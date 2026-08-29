import tempfile
import unittest

from core.script_engine import ScriptEngine


class ScriptEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.engine = ScriptEngine(self.temp_dir.name)

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_plain_python_stdout_and_stderr(self):
        result = await self.engine.run_code(
            "import sys\nprint(10 + 20)\nprint('warning', file=sys.stderr)"
        )
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout"].strip(), "30")
        self.assertEqual(result["stderr"].strip(), "warning")
        self.assertFalse(result["timed_out"])

    async def test_timeout_kills_child_without_blocking_backend(self):
        self.engine.RUN_TIMEOUT_S = 0.1
        result = await self.engine.run_code("while True:\n    pass")
        self.assertTrue(result["timed_out"])
        self.assertNotEqual(result["exit_code"], 0)


if __name__ == "__main__":
    unittest.main()
