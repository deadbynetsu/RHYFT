# -*- coding: utf-8 -*-
"""Rebranding único do projeto para RHYFT.

Troca a marca visível, nomes de binários/documentação e mantém compatibilidade
com dados locais e identificadores externos já usados em produção.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
TEXT_EXTS = {'.py', '.md', '.html', '.js', '.toml', '.bat', '.txt'}
SKIP = {
    Path('ferramentas/rebrand_rhyft.py'),
}


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def write(rel, text):
    (ROOT / rel).write_text(text, encoding='utf-8')


# Marca visível e nomes de binários/artefatos fora dos workflows.
for path in ROOT.rglob('*'):
    if not path.is_file() or path.suffix.lower() not in TEXT_EXTS:
        continue
    rel = path.relative_to(ROOT)
    if '.git' in rel.parts or rel in SKIP or '.github' in rel.parts:
        continue
    text = path.read_text(encoding='utf-8')
    original = text
    text = text.replace('Migrador de Playlists', 'RHYFT')
    text = text.replace('MigradorPlaylists', 'RHYFT')
    text = text.replace('Migrador Spotify e YouTube Music', 'RHYFT — Spotify e YouTube Music')
    text = re.sub(r'\bMigrador\b', 'RHYFT', text)
    for old, new in (
        ('o migrador de playlists', 'o RHYFT'),
        ('do migrador de playlists', 'do RHYFT'),
        ('pelo migrador de playlists', 'pelo RHYFT'),
        ('no migrador de playlists', 'no RHYFT'),
        ('o migrador', 'o RHYFT'),
        ('do migrador', 'do RHYFT'),
        ('pelo migrador', 'pelo RHYFT'),
        ('no migrador', 'no RHYFT'),
    ):
        text = text.replace(old, new)
    if text != original:
        path.write_text(text, encoding='utf-8')

# O nome técnico do pacote Android fica estável para o APK continuar atualizando
# por cima da instalação atual.
text = read('celular/pyproject.toml')
text = text.replace('name = "rhyft"', 'name = "migrador-playlists"')
write('celular/pyproject.toml', text)

# Migra dados antigos do desktop para %APPDATA%/RHYFT sem perder tokens/config.
for rel in ('nucleo.py', 'celular/src/nucleo.py'):
    text = read(rel)
    if "LEGACY_APP_NAME = 'MigradorPlaylists'" not in text:
        text = text.replace(
            "APP_NAME = 'RHYFT'\nAPP_VERSION =",
            "APP_NAME = 'RHYFT'\nLEGACY_APP_NAME = 'MigradorPlaylists'\nAPP_VERSION =",
            1,
        )
    old = """    else:\n        base = os.environ.get('APPDATA') or os.path.join(os.path.expanduser('~'), '.config')\n        pasta = os.path.join(base, APP_NAME)\n    try:\n        os.makedirs(pasta, exist_ok=True)\n        return pasta\n"""
    new = """    else:\n        base = os.environ.get('APPDATA') or os.path.join(os.path.expanduser('~'), '.config')\n        pasta = os.path.join(base, APP_NAME)\n        antiga = os.path.join(base, LEGACY_APP_NAME)\n        if not os.path.exists(pasta) and os.path.isdir(antiga):\n            try:\n                shutil.copytree(antiga, pasta)\n            except OSError:\n                pass\n    try:\n        os.makedirs(pasta, exist_ok=True)\n        return pasta\n"""
    if old in text:
        text = text.replace(old, new, 1)
    write(rel, text)

# Migra a pasta privada do Android para RHYFT sem deslogar quem já usa o app.
text = read('celular/src/main.py')
if 'import shutil\n' not in text:
    text = text.replace('import re\nimport threading\n', 'import re\nimport shutil\nimport threading\n', 1)
old_mobile = """# Pasta privada do app. Precisa ser definida ANTES de importar o núcleo.\nos.environ.setdefault('MIGRADOR_DATA_DIR', os.path.join(\n    os.environ.get('FLET_APP_STORAGE_DATA') or os.path.expanduser('~'), 'RHYFT'))\n"""
new_mobile = """# Pasta privada do app. Precisa ser definida ANTES de importar o núcleo.\n_storage_base = os.environ.get('FLET_APP_STORAGE_DATA') or os.path.expanduser('~')\n_data_nova = os.path.join(_storage_base, 'RHYFT')\n_data_antiga = os.path.join(_storage_base, 'MigradorPlaylists')\nif not os.path.exists(_data_nova) and os.path.isdir(_data_antiga):\n    try:\n        shutil.copytree(_data_antiga, _data_nova)\n    except OSError:\n        pass\nos.environ.setdefault('MIGRADOR_DATA_DIR', _data_nova)\n"""
if old_mobile in text:
    text = text.replace(old_mobile, new_mobile, 1)
write('celular/src/main.py', text)

# Site: prioriza os novos nomes de download, mas mantém fallback para releases antigas.
text = read('site/script.js')
old_windows = "const windows = assets.find(a => /windows.*\\.zip$/i.test(a.name)) || assets.find(a => /\\.zip$/i.test(a.name) && /migrador/i.test(a.name));"
new_windows = "const windows = assets.find(a => /^RHYFT-windows\\.zip$/i.test(a.name)) || assets.find(a => /windows.*\\.zip$/i.test(a.name)) || assets.find(a => /\\.zip$/i.test(a.name) && /(rhyft|migrador)/i.test(a.name));"
old_android = "const android = assets.find(a => /android.*\\.apk$/i.test(a.name)) || assets.find(a => /\\.apk$/i.test(a.name));"
new_android = "const android = assets.find(a => /^RHYFT-android\\.apk$/i.test(a.name)) || assets.find(a => /android.*\\.apk$/i.test(a.name)) || assets.find(a => /\\.apk$/i.test(a.name));"
text = text.replace(old_windows, new_windows).replace(old_android, new_android)
write('site/script.js', text)

# README com identidade de produto.
text = read('README.md')
text = text.replace('# 🎵 RHYFT', '# RHYFT')
if 'Your music. No borders.' not in text:
    text = text.replace('# RHYFT\n', '# RHYFT\n\n**Your music. No borders.**\n', 1)
write('README.md', text)

print('Rebranding RHYFT aplicado.')
