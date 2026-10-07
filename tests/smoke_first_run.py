"""Regression smoke: finish first-run onboarding, then open the real desktop UI."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

directory = Path(os.environ['MIGRADOR_DATA_DIR'])
directory.mkdir(parents=True, exist_ok=True)
(directory / 'settings.json').write_text(json.dumps({'auto_update': False}), encoding='utf-8')

from desktop_language import prepare_language_before_app


def auto_continue(dialog):
    language = os.environ.get('RHYFT_TEST_LANGUAGE', 'en')
    dialog.value.set(language)
    dialog.select()
    dialog.after(120, dialog.save)


selected = prepare_language_before_app(directory, dialog_setup=auto_continue)
assert selected == os.environ.get('RHYFT_TEST_LANGUAGE', 'en')
assert (directory / 'language.json').is_file()

from app_branded import RHYFTApp

window = RHYFTApp()
try:
    window.update_idletasks()
    window.update()
    assert window.winfo_exists()
    assert window.state() != 'withdrawn'
    assert window.btn_iniciar.winfo_exists()
    print('FIRST_RUN_OK')
finally:
    window.destroy()
