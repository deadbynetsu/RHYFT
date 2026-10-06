"""Shared, offline localization and first-run preferences for RHYFT."""
import ctypes
import json
import locale
import os
from pathlib import Path
import sys
import tempfile

LANGUAGES = {
    'pt-BR': 'Português (Brasil)', 'en': 'English', 'es': 'Español',
    'fr': 'Français', 'de': 'Deutsch', 'it': 'Italiano', 'ja': '日本語',
    'ko': '한국어', 'zh-CN': '简体中文', 'zh-TW': '繁體中文',
}
FIRST_HOLD_MS = 4200
ROTATION_HOLD_MS = 1800
FADE_MS = 250
_language = 'en'
_catalogs = {}


def normalize_locale(value):
    value = str(value or '').split('.')[0].split('@')[0].replace('_', '-').lower()
    parts = value.split('-')
    if parts[0] == 'zh':
        return 'zh-TW' if any(p in ('hant', 'tw', 'hk', 'mo') for p in parts) else 'zh-CN'
    if parts[0] == 'pt':
        return 'pt-BR'
    return parts[0] if parts[0] in LANGUAGES else None


def detect_language(preferred=None):
    """Prefer device locales (Flet), then Windows UI language, then process locale."""
    candidates = list(preferred or [])
    if sys.platform == 'win32':
        try:
            buffer = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, len(buffer)):
                candidates.append(buffer.value)
        except (AttributeError, OSError):
            pass
    candidates.extend(os.environ.get(k) for k in ('LANGUAGE', 'LC_ALL', 'LC_MESSAGES', 'LANG'))
    try:
        candidates.append(locale.getlocale()[0])
    except (ValueError, TypeError):
        pass
    for candidate in candidates:
        for item in str(candidate or '').split(':'):
            code = normalize_locale(item)
            if code:
                return code
    return 'en'


def language_order(detected):
    code = normalize_locale(detected) or 'en'
    return [code] + [other for other in LANGUAGES if other != code]


def catalog(code):
    code = normalize_locale(code) or 'en'
    if code not in _catalogs:
        path = Path(__file__).resolve().parent / 'locales' / (code + '.json')
        try:
            _catalogs[code] = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            _catalogs[code] = {}
    return _catalogs[code]


def set_language(code):
    global _language
    _language = normalize_locale(code) or 'en'


def get_language():
    return _language


def tr(key, *, language=None, **values):
    code = language or _language
    text = catalog(code).get(key, catalog('en').get(key, key))
    return text.format(**values) if values else text


class LanguagePreference:
    """Separate atomic file: cannot overwrite credentials or update preferences."""
    def __init__(self, directory):
        self.path = Path(directory) / 'language.json'

    def read(self):
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
            code = value.get('language') if isinstance(value, dict) else None
            return code if code in LANGUAGES else None
        except (OSError, ValueError):
            return None

    def save(self, code):
        if code not in LANGUAGES:
            raise ValueError('Unsupported language')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='language-', suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump({'language': code}, stream, ensure_ascii=False)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
