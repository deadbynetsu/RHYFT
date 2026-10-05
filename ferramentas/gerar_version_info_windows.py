# -*- coding: utf-8 -*-
"""Gera metadados de versão do executável Windows usado pelo PyInstaller."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
NUCLEO = ROOT / "nucleo.py"
SAIDA = ROOT / "build_meta" / "version_info.txt"

texto = NUCLEO.read_text(encoding="utf-8")
match = re.search(r"APP_VERSION\s*=\s*['\"]([^'\"]+)", texto)
if not match:
    raise SystemExit("APP_VERSION não encontrado em nucleo.py")

versao = match.group(1)
partes = []
for pedaco in versao.split("."):
    numero = re.match(r"\d+", pedaco)
    partes.append(int(numero.group(0)) if numero else 0)
while len(partes) < 4:
    partes.append(0)
versao_tuple = tuple(partes[:4])

conteudo = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={versao_tuple},
    prodvers={versao_tuple},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          u'041604B0',
          [
            StringStruct(u'CompanyName', u'deadbynetsu'),
            StringStruct(u'FileDescription', u'RHYFT - Spotify e YouTube Music'),
            StringStruct(u'FileVersion', u'{versao}'),
            StringStruct(u'InternalName', u'RHYFT'),
            StringStruct(u'LegalCopyright', u'Copyright (c) deadbynetsu'),
            StringStruct(u'OriginalFilename', u'RHYFT.exe'),
            StringStruct(u'ProductName', u'RHYFT'),
            StringStruct(u'ProductVersion', u'{versao}')
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct(u'Translation', [1046, 1200])])
  ]
)
"""

SAIDA.parent.mkdir(parents=True, exist_ok=True)
SAIDA.write_text(conteudo, encoding="utf-8")
print(f"Metadados Windows gerados para a versão {versao}: {SAIDA}")
