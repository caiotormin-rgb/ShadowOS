"""Research routing is independent of the household conversation model."""
import importlib
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import llm

class ResearchModelTests(unittest.TestCase):
    def tearDown(self):
        importlib.reload(llm)

    def call_with_env(self, env):
        with mock.patch.dict(os.environ, env, clear=True):
            importlib.reload(llm)
        result = SimpleNamespace(returncode=0, stdout=json.dumps({"outputs": [{"text": '{"ok": true}'}]}))
        with mock.patch.object(llm, "RUN", return_value=result) as run, mock.patch.object(llm.notify, "openclaw_bin", return_value="openclaw"):
            self.assertEqual(llm.ask_json("synthetic research test"), {"ok": True})
        return run.call_args.args[0]

    def test_default_is_pinned_even_without_environment(self):
        args = self.call_with_env({})
        self.assertEqual(args[args.index("--model") + 1], "openai/gpt-5.6-sol")
        self.assertEqual(args[args.index("--agent") + 1], "shared-tools")

    def test_empty_environment_does_not_restore_implicit_routing(self):
        args = self.call_with_env({"DOCTOR_LLM_MODEL": "  ", "DOCTOR_LLM_AGENT": ""})
        self.assertEqual(args[args.index("--model") + 1], "openai/gpt-5.6-sol")
        self.assertEqual(args[args.index("--agent") + 1], "shared-tools")

    def test_explicit_worker_profile_and_model_are_preserved(self):
        args = self.call_with_env({"DOCTOR_LLM_MODEL": "openai/gpt-5.6-terra", "DOCTOR_LLM_AGENT": "doctor-research"})
        self.assertEqual(args[args.index("--model") + 1], "openai/gpt-5.6-terra")
        self.assertEqual(args[args.index("--agent") + 1], "doctor-research")

if __name__ == "__main__":
    unittest.main()
