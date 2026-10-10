import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ema_tts import app, theme

ROOT = Path(__file__).resolve().parent.parent


class OfflineTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)

    def cache_model(self, hub: Path, files=app.MODEL_FILES):
        model = hub / "models--canberkkkkkk--ema-lightning"
        snapshot = model / "snapshots" / "7a6ba1ad"
        snapshot.mkdir(parents=True)
        (model / "refs").mkdir()
        (model / "refs" / "main").write_text("7a6ba1ad", encoding="utf-8")
        for name in files:
            (snapshot / name).write_bytes(b"weights")

    def test_cached_model_turns_the_hub_off(self):
        self.cache_model(self.home / "hub")
        env = {"HF_HUB_CACHE": str(self.home / "hub")}
        self.assertTrue(app.offline_if_cached(env))
        self.assertEqual(env["HF_HUB_OFFLINE"], "1")

    def test_hf_home_and_default_locations(self):
        self.cache_model(self.home / "hf" / "hub")
        env = {"HF_HOME": str(self.home / "hf")}
        self.assertTrue(app.offline_if_cached(env))
        self.cache_model(self.home / "xdg" / "huggingface" / "hub")
        self.assertTrue(app.offline_if_cached({"XDG_CACHE_HOME": str(self.home / "xdg")}))
        self.assertEqual(app.hub_cache({}), Path.home() / ".cache" / "huggingface" / "hub")
        self.assertEqual(app.hub_cache({"HF_HOME": "x", "HF_HUB_CACHE": "y"}), Path("y"))  # HF_HUB_CACHE wins

    def test_first_start_stays_online(self):
        hub = self.home / "hub"
        env = {"HF_HUB_CACHE": str(hub)}
        self.assertFalse(app.offline_if_cached(env))  # nothing downloaded yet
        self.cache_model(hub, files=["ema.pt"])  # interrupted download
        self.assertFalse(app.offline_if_cached(env))
        self.assertNotIn("HF_HUB_OFFLINE", env)

    def test_user_choice_is_left_alone(self):
        self.cache_model(self.home / "hub")
        for extra in ({"EMA_TTS_ONLINE": "1"}, {"HF_HUB_OFFLINE": "0"}, {"HF_HUB_OFFLINE": ""}):
            with self.subTest(extra=extra):
                env = {"HF_HUB_CACHE": str(self.home / "hub"), **extra}
                self.assertFalse(app.offline_if_cached(env))
                self.assertEqual(env.get("HF_HUB_OFFLINE"), extra.get("HF_HUB_OFFLINE"))
        env = {"HF_HUB_CACHE": str(self.home / "hub"), "EMA_TTS_ONLINE": "0"}
        self.assertTrue(app.offline_if_cached(env))

    def test_app_does_not_import_the_hub_before_main(self):
        # huggingface_hub reads HF_HUB_OFFLINE once, when it is imported; main() must get there first
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import ema_tts.app; "
                "print(sorted(m for m in sys.modules if m.split('.')[0] in ('huggingface_hub', 'ema_lightning')))")
        result = subprocess.run([sys.executable, "-I", "-c", code, str(ROOT)], capture_output=True, text=True,
                                timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "[]")


class IconFontTest(unittest.TestCase):
    def test_windows_10_falls_back_to_mdl2(self):
        self.assertEqual(theme.icon_font({"Segoe UI", "Segoe MDL2 Assets"}), "Segoe MDL2 Assets")
        self.assertEqual(theme.icon_font({"Segoe Fluent Icons", "Segoe MDL2 Assets"}), "Segoe Fluent Icons")
        self.assertEqual(theme.icon_font({"Segoe UI"}), "Segoe Fluent Icons")  # nothing better to offer


class IdentityTest(unittest.TestCase):
    def test_app_id(self):
        self.assertEqual(app.APP_ID, "EmreOzhan.TalkingBoard")


if __name__ == "__main__":
    unittest.main()
