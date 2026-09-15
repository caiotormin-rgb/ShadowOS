"""Offline checks for routed versus combined household prompt generation."""
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    "household_render", Path(__file__).resolve().parents[1] / "render.py"
)
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)


class RenderTests(unittest.TestCase):
    def test_routed_base_teaches_standalone_words_and_compatible_slashes(self):
        base = renderer.render("base")
        self.assertIn("one word by itself: lista or groceries", base)
        self.assertIn("médico, medico or doctor", base)
        self.assertIn("existing /lista, /groceries, /medico and /doctor", base)
        self.assertIn("Words inside a sentence do not automatically switch", base)
        self.assertIn("/remover and /ok remain", base)

    def test_combined_prompt_replaces_routing_guidance(self):
        combined = renderer.render("combined")
        self.assertIn("Explicit mode routing is not enabled", combined)
        self.assertIn("Do not offer mode-switch commands", combined)
        self.assertNotIn("To switch modes", combined)
        self.assertNotIn("MODE_ROUTING_", combined)
        self.assertIn("No access to private owner memory", combined)

    def test_domain_renders_stay_separate(self):
        for mode in ("groceries", "doctor"):
            with self.subTest(mode=mode):
                prompt = renderer.render(mode)
                self.assertTrue(prompt.strip())
                self.assertNotIn("MODE_ROUTING_", prompt)
                self.assertNotIn("To switch modes", prompt)


if __name__ == "__main__":
    unittest.main()
