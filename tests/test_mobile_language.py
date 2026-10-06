import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'celular/src'))
try:
    import flet as ft
    import mobile_language as mobile
except ImportError:
    ft = None
from rhyft_i18n import LanguagePreference, get_language


class FakePage:
    def __init__(self):
        self.controls = []
        self.dialogs = []

    def add(self, control):
        self.controls.append(control)

    def update(self):
        pass

    def clean(self):
        self.controls.clear()

    def show_dialog(self, dialog):
        self.dialogs.append(dialog)

    def pop_dialog(self):
        self.dialogs.pop()

    async def get_device_info(self):
        return SimpleNamespace(locales=[SimpleNamespace(language_code='zh', script_code='Hant', country_code='HK')])


@unittest.skipIf(ft is None, 'Flet integration runs in CI with pinned 1.0.3')
class MobileLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_device_locale(self):
        self.assertEqual(await mobile.device_language(FakePage()), 'zh-TW')

    async def test_first_run_select_persist_and_skip_next_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            page = FakePage()
            task = asyncio.create_task(mobile.ensure_language(page, directory))
            for _ in range(10):
                await asyncio.sleep(0)
                if page.controls:
                    break
            layout = page.controls[0].content.content
            choice = layout.controls[3]
            self.assertEqual(choice.value, 'zh-TW')
            self.assertEqual(choice.content.controls[0].value, 'zh-TW')
            choice.value = 'de'
            choice.on_change(None)
            layout.controls[-1].on_click(None)
            await asyncio.wait_for(task, 2)
            self.assertEqual(LanguagePreference(directory).read(), 'de')
            self.assertEqual(get_language(), 'de')
            self.assertEqual(page.controls, [])
            await mobile.ensure_language(page, directory)
            self.assertEqual(page.controls, [])

    async def test_cancel_settings_does_not_change_preference(self):
        with tempfile.TemporaryDirectory() as directory:
            preference = LanguagePreference(directory)
            preference.save('en')
            page = FakePage()
            task = asyncio.create_task(mobile.choose_language(page, preference, 'en', 'ja', settings=True))
            await asyncio.sleep(0)
            dialog = page.dialogs[-1]
            dialog.actions[0].on_click(None)
            self.assertIsNone(await asyncio.wait_for(task, 2))
            self.assertEqual(preference.read(), 'en')

    async def test_failed_save_keeps_screen_open(self):
        with tempfile.TemporaryDirectory() as directory:
            preference = LanguagePreference(directory)
            page = FakePage()
            task = asyncio.create_task(mobile.choose_language(page, preference, 'en', 'en', settings=True))
            await asyncio.sleep(0)
            dialog = page.dialogs[-1]
            with patch.object(preference, 'save', side_effect=OSError):
                dialog.content.content.controls[-1].on_click(None)
            self.assertFalse(task.done())
            self.assertTrue(dialog.content.content.controls[-2].value)
            dialog.actions[0].on_click(None)
            await task
