import sys
import tempfile
import unittest
import os
from pathlib import Path
import subprocess
try:
    import customtkinter as ctk
except ImportError:
    ctk = None
from rhyft_i18n import LanguagePreference


@unittest.skipUnless(sys.platform == 'win32' and ctk, 'Desktop UI checks run on Windows CI')
class DesktopLanguageTests(unittest.TestCase):
    def test_main_screen_opens_in_every_saved_language(self):
        from rhyft_i18n import LANGUAGES
        root = Path(__file__).resolve().parents[1]
        for language in LANGUAGES:
            with self.subTest(language=language), tempfile.TemporaryDirectory() as directory:
                env = dict(os.environ, MIGRADOR_DATA_DIR=directory, RHYFT_TEST_LANGUAGE=language, PYTHONUTF8='1')
                subprocess.run([sys.executable, str(root / 'tests/smoke_desktop.py')],
                               cwd=root, env=env, check=True, timeout=20, capture_output=True)

    def test_select_save_and_cancel_timers(self):
        from desktop_language import LanguageDialog
        with tempfile.TemporaryDirectory() as directory:
            root = ctk.CTk()
            root.withdraw()
            result = []
            try:
                dialog = LanguageDialog(root, LanguagePreference(directory), 'en', result.append, first_run=True)
                root.update()
                dialog.value.set('ja')
                dialog.select()
                self.assertEqual(dialog.heading.cget('text'), '言語を選択')
                self.assertIsNone(dialog.timer)
                dialog.save()
                self.assertEqual(result, ['ja'])
                self.assertEqual(LanguagePreference(directory).read(), 'ja')
            finally:
                root.destroy()
