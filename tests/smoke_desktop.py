"""Exercise the actual desktop entrypoint in isolated data without network login."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
directory = Path(os.environ['MIGRADOR_DATA_DIR'])
language = os.environ['RHYFT_TEST_LANGUAGE']
(directory / 'settings.json').write_text(json.dumps({'auto_update': False}), encoding='utf-8')
from rhyft_i18n import LanguagePreference, tr
LanguagePreference(directory).save(language)
from app_branded import RHYFTApp
window = RHYFTApp()
try:
    window.withdraw()
    window.update()
    assert window.btn_iniciar.cget('text') == tr('Iniciar migração')
    assert window.lbl_prog_titulo.cget('text') == tr('Pronto para começar')
    window._ao_trocar_modo('YouTube Music  ➔  Spotify')
    assert window.lbl_campo_origem.cget('text') == tr('Link ou ID da playlist do YouTube Music')
finally:
    window.destroy()
