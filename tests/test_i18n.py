import ast
import json
from pathlib import Path
import string
import tempfile
import unittest
from unittest.mock import patch

from rhyft_i18n import (LANGUAGES, LanguagePreference, catalog, detect_language,
                        language_order, normalize_locale, set_language, tr,
                        FIRST_HOLD_MS, ROTATION_HOLD_MS)

ROOT = Path(__file__).resolve().parents[1]


class LocalizationTests(unittest.TestCase):
    def test_regional_and_script_variants(self):
        for source, expected in {'pt_PT.UTF-8': 'pt-BR', 'en_US': 'en', 'es-MX': 'es',
                'zh-Hant-HK': 'zh-TW', 'zh_HK': 'zh-TW', 'zh-SG': 'zh-CN',
                'zh-Hans-CN': 'zh-CN', 'ja-JP': 'ja', 'ko_KR': 'ko'}.items():
            self.assertEqual(normalize_locale(source), expected)
        self.assertIsNone(normalize_locale('ru-RU'))

    def test_device_locale_wins(self):
        self.assertEqual(detect_language(['ja-JP', 'en-US']), 'ja')
        self.assertEqual(detect_language(['ru-RU', 'fr-CA']), 'fr')
        with patch('rhyft_i18n.sys.platform', 'linux'), patch.dict('os.environ', {}, clear=True), patch('locale.getlocale', return_value=(None, None)):
            self.assertEqual(detect_language(), 'en')

    def test_detected_language_first_and_longer(self):
        for code in LANGUAGES:
            self.assertEqual(language_order(code)[0], code)
            self.assertEqual(set(language_order(code)), set(LANGUAGES))
        self.assertGreater(FIRST_HOLD_MS, ROTATION_HOLD_MS)

    def test_complete_catalogs_and_placeholders(self):
        base = catalog('en')
        self.assertGreater(len(base), 60)
        formatter = string.Formatter()
        for code in LANGUAGES:
            self.assertEqual(set(catalog(code)), set(base), code)
            for key, value in catalog(code).items():
                self.assertTrue(value.strip(), (code, key))
                fields = lambda s: {f for _, f, _, _ in formatter.parse(s) if f}
                self.assertEqual(fields(value), fields(base[key]), (code, key))

    def test_preferences_survive_restart_and_preserve_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = Path(folder) / 'settings.json'
            settings.write_text('{"auto_update": false}', encoding='utf-8')
            preference = LanguagePreference(folder)
            self.assertIsNone(preference.read())
            preference.save('zh-TW')
            self.assertEqual(LanguagePreference(folder).read(), 'zh-TW')
            self.assertEqual(json.loads(settings.read_text())['auto_update'], False)
            with self.assertRaises(ValueError):
                preference.save('not-a-language')
            self.assertEqual(preference.read(), 'zh-TW')
            self.assertFalse(list(Path(folder).glob('*.tmp')))

    def test_corrupt_preferences_show_chooser_again(self):
        with tempfile.TemporaryDirectory() as folder:
            preference = LanguagePreference(folder)
            for value in ('broken', '[]', '{"language":"xx"}', 'null'):
                preference.path.write_text(value, encoding='utf-8')
                self.assertIsNone(preference.read())

    def test_unknown_key_and_saved_language(self):
        set_language('ja')
        self.assertEqual(tr('Continuar'), '続ける')
        self.assertEqual(tr('provider diagnostic'), 'provider diagnostic')
        set_language('en')

    def test_shared_bundle_matches(self):
        for path in ['rhyft_i18n.py', 'nucleo.py'] + [f'locales/{code}.json' for code in LANGUAGES]:
            self.assertEqual((ROOT / path).read_bytes(), (ROOT / 'celular/src' / path).read_bytes(), path)

    def test_versions_and_syntax(self):
        import tomllib
        project = tomllib.loads((ROOT / 'celular/pyproject.toml').read_text(encoding='utf-8'))
        self.assertEqual(project['project']['version'], '1.5.2')
        tree = ast.parse((ROOT / 'nucleo.py').read_text(encoding='utf-8'))
        values = {n.targets[0].id: n.value.value for n in tree.body if isinstance(n, ast.Assign)
                  and isinstance(n.targets[0], ast.Name) and isinstance(n.value, ast.Constant)}
        self.assertEqual(values['APP_VERSION'], project['project']['version'])
        self.assertEqual(values['GITHUB_REPO'], 'deadbynetsu/RHYFT')
        for file in ROOT.rglob('*.py'):
            if '.venv' not in file.parts:
                ast.parse(file.read_text(encoding='utf-8'), filename=str(file))


if __name__ == '__main__':
    unittest.main()
