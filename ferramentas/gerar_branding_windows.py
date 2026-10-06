# -*- coding: utf-8 -*-
"""Gera PNG e ICO oficiais do RHYFT a partir dos SVGs versionados."""
from pathlib import Path

from cairosvg import svg2png
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
OUT = ASSETS / "generated"
OUT.mkdir(parents=True, exist_ok=True)

APP_SVG = ASSETS / "rhyft_app_icon.svg"
MARK_SVG = ASSETS / "rhyft_mark.svg"

for arquivo in (APP_SVG, MARK_SVG):
    if not arquivo.exists():
        raise SystemExit(f"Asset ausente: {arquivo}")

app_png = OUT / "rhyft_icon.png"
mark_png = OUT / "rhyft_mark.png"
ico_path = OUT / "rhyft_icon.ico"

svg2png(url=str(APP_SVG), write_to=str(app_png), output_width=1024, output_height=1024)
svg2png(url=str(MARK_SVG), write_to=str(mark_png), output_width=512, output_height=512)

img = Image.open(app_png).convert("RGBA")
img.save(
    ico_path,
    format="ICO",
    sizes=[
        (16, 16), (20, 20), (24, 24), (32, 32), (40, 40),
        (48, 48), (64, 64), (128, 128), (256, 256),
    ],
)

if not ico_path.exists() or ico_path.stat().st_size < 1024:
    raise SystemExit("Falha ao gerar o ícone .ico do RHYFT.")

print(f"Branding Windows gerado em {OUT}")
