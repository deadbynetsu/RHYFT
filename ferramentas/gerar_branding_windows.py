# -*- coding: utf-8 -*-
"""Gera os assets Windows do RHYFT usando somente Pillow.

O runner Windows do GitHub Actions não inclui a DLL nativa do Cairo. Manter a
renderização em Pillow deixa o build portátil e ainda gera um ICO multi-size
adequado para Explorer, taskbar, atalhos e janelas do Tk/CustomTkinter.
"""
from pathlib import Path
from math import comb

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "generated"
OUT.mkdir(parents=True, exist_ok=True)

APP_PNG = OUT / "rhyft_icon.png"
MARK_PNG = OUT / "rhyft_mark.png"
ICO_PATH = OUT / "rhyft_icon.ico"

S = 1024


def _gradiente_horizontal(size, stops):
    """RGBA horizontal interpolando uma lista de (posição, RGBA)."""
    w, h = size
    img = Image.new("RGBA", size)
    px = img.load()
    stops = sorted(stops)
    for x in range(w):
        t = x / max(w - 1, 1)
        a, b = stops[0], stops[-1]
        for i in range(len(stops) - 1):
            if stops[i][0] <= t <= stops[i + 1][0]:
                a, b = stops[i], stops[i + 1]
                break
        span = max(b[0] - a[0], 1e-9)
        u = min(1.0, max(0.0, (t - a[0]) / span))
        cor = tuple(round(a[1][k] + (b[1][k] - a[1][k]) * u) for k in range(4))
        for y in range(h):
            px[x, y] = cor
    return img


def _gradiente_vertical(size, top, bottom):
    w, h = size
    img = Image.new("RGBA", size)
    px = img.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        cor = tuple(round(top[k] + (bottom[k] - top[k]) * t) for k in range(4))
        for x in range(w):
            px[x, y] = cor
    return img


def _bezier(p0, p1, p2, p3, passos=80):
    pontos = []
    for i in range(passos + 1):
        t = i / passos
        u = 1 - t
        x = u**3*p0[0] + 3*u*u*t*p1[0] + 3*u*t*t*p2[0] + t**3*p3[0]
        y = u**3*p0[1] + 3*u*u*t*p1[1] + 3*u*t*t*p2[1] + t**3*p3[1]
        pontos.append((round(x), round(y)))
    return pontos


def _colar_com_mascara(destino, preenchimento, mascara, glow=None, blur=22):
    if glow:
        halo = Image.new("RGBA", destino.size, (0, 0, 0, 0))
        glow_layer = Image.new("RGBA", destino.size, glow)
        halo.paste(glow_layer, (0, 0), mascara.filter(ImageFilter.GaussianBlur(blur)))
        destino.alpha_composite(halo)
    destino.paste(preenchimento, (0, 0), mascara)


def criar_marca(size=S, glow=True):
    base = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # Seta superior (ciano)
    topo = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(topo)
    pts = _bezier((150, 405), (315, 438), (372, 185), (748, 226))
    d.line(pts, fill=255, width=116, joint="curve")
    r = 58
    for p in (pts[0], pts[-1]):
        d.ellipse((p[0]-r, p[1]-r, p[0]+r, p[1]+r), fill=255)
    d.polygon([(704, 116), (904, 255), (704, 397)], fill=255)
    cyan = _gradiente_horizontal((S, S), [
        (0.0, (0, 141, 144, 255)),
        (0.52, (28, 224, 204, 255)),
        (1.0, (104, 247, 238, 255)),
    ])
    _colar_com_mascara(base, cyan, topo, (27, 235, 218, 120) if glow else None, 24)

    # Equalizador central.
    barras = [
        (345, 444, 400, 572, (76, 255, 232, 255), (0, 196, 177, 255)),
        (430, 386, 492, 633, (71, 255, 229, 255), (0, 197, 180, 255)),
        (522, 330, 590, 690, (235, 255, 251, 255), (207, 119, 199, 255)),
        (617, 395, 679, 628, (255, 181, 170, 255), (255, 57, 124, 255)),
        (706, 451, 760, 574, (255, 143, 150, 255), (255, 44, 125, 255)),
    ]
    for x0, y0, x1, y1, c1, c2 in barras:
        m = Image.new("L", (S, S), 0)
        ImageDraw.Draw(m).rounded_rectangle((x0, y0, x1, y1), radius=(x1-x0)//2, fill=255)
        g = _gradiente_vertical((S, S), c1, c2)
        if glow:
            halo = Image.new("RGBA", (S, S), (0, 0, 0, 0))
            cor_halo = (31, 236, 218, 85) if x0 < 600 else (255, 61, 119, 85)
            halo.paste(Image.new("RGBA", (S, S), cor_halo), (0, 0), m.filter(ImageFilter.GaussianBlur(12)))
            base.alpha_composite(halo)
        base.paste(g, (0, 0), m)

    # Seta inferior (rosa/coral)
    baixo = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(baixo)
    pts = _bezier((856, 618), (716, 735), (565, 781), (231, 630))
    d.line(pts, fill=255, width=118, joint="curve")
    r = 59
    for p in (pts[0], pts[-1]):
        d.ellipse((p[0]-r, p[1]-r, p[0]+r, p[1]+r), fill=255)
    d.polygon([(244, 524), (56, 647), (244, 777)], fill=255)
    pink = _gradiente_horizontal((S, S), [
        (0.0, (255, 151, 127, 255)),
        (0.48, (255, 83, 111, 255)),
        (1.0, (255, 42, 144, 255)),
    ])
    _colar_com_mascara(base, pink, baixo, (255, 48, 113, 120) if glow else None, 24)

    # Pequeno brilho interno para conservar definição em 16/32 px.
    if glow:
        high = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        hd = ImageDraw.Draw(high)
        hd.line(_bezier((166, 382), (318, 414), (385, 200), (711, 216)), fill=(225, 255, 253, 130), width=7)
        hd.line(_bezier((831, 598), (705, 701), (566, 741), (254, 610)), fill=(255, 238, 236, 110), width=7)
        base.alpha_composite(high)

    if size != S:
        base = base.resize((size, size), Image.Resampling.LANCZOS)
    return base


def criar_icone_app():
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # Halo externo da moldura.
    halo_mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(halo_mask).rounded_rectangle((64, 64, 960, 960), radius=196, fill=255)
    halo = Image.new("RGBA", (S, S), (22, 220, 215, 0))
    halo.putalpha(halo_mask.filter(ImageFilter.GaussianBlur(24)))
    canvas.alpha_composite(halo)

    # Fundo escuro premium.
    bg = Image.new("RGBA", (S, S), (7, 10, 16, 255))
    bg_draw = ImageDraw.Draw(bg)
    for i in range(880, 0, -1):
        # brilho muito sutil no centro
        if i % 16 == 0:
            pass
    bg_mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(bg_mask).rounded_rectangle((68, 68, 956, 956), radius=190, fill=255)
    canvas.paste(bg, (0, 0), bg_mask)

    # Borda em gradiente cyan -> rosa.
    outer = Image.new("L", (S, S), 0)
    inner = Image.new("L", (S, S), 0)
    ImageDraw.Draw(outer).rounded_rectangle((65, 65, 959, 959), radius=194, fill=255)
    ImageDraw.Draw(inner).rounded_rectangle((82, 82, 942, 942), radius=181, fill=255)
    rim = Image.eval(outer, lambda p: p)
    # subtrai o miolo da máscara da borda
    rim_px, inner_px = rim.load(), inner.load()
    for y in range(S):
        for x in range(S):
            if inner_px[x, y]:
                rim_px[x, y] = 0
    rim_grad = _gradiente_horizontal((S, S), [
        (0.0, (22, 240, 224, 255)),
        (0.50, (108, 208, 255, 255)),
        (0.62, (202, 119, 255, 255)),
        (1.0, (255, 51, 119, 255)),
    ])
    canvas.paste(rim_grad, (0, 0), rim)

    mark = criar_marca(820, glow=True)
    canvas.alpha_composite(mark, ((S - 820)//2, (S - 820)//2))
    return canvas


# PNG transparente para o cabeçalho interno.
marca = criar_marca(512, glow=True)
marca.save(MARK_PNG, optimize=True)

# PNG e ICO para Explorer/taskbar/janelas.
icone = criar_icone_app()
icone.save(APP_PNG, optimize=True)
icone.save(
    ICO_PATH,
    format="ICO",
    sizes=[
        (16, 16), (20, 20), (24, 24), (32, 32), (40, 40),
        (48, 48), (64, 64), (128, 128), (256, 256),
    ],
)

if not ICO_PATH.exists() or ICO_PATH.stat().st_size < 1024:
    raise SystemExit("Falha ao gerar o ícone .ico do RHYFT.")

print(f"Branding Windows gerado em {OUT}")
