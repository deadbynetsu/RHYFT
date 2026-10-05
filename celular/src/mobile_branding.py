# -*- coding: utf-8 -*-
"""Aplica a identidade visual oficial na interface Android."""
import flet as ft


def apply_mobile_branding(target):
    """Troca o marcador provisório do cabeçalho pela logo oficial do app."""
    g = target.__globals__
    Tela = g.get('Tela')
    if Tela is None or getattr(Tela, '_mobile_branding_applied', False):
        return

    montar_base = Tela.montar

    def montar_com_logo(self):
        montar_base(self)
        try:
            # Estrutura criada pelo mobile_ui_refresh:
            # SafeArea > Container > Column > cabeçalho > Row > logo.
            safe = self.page.controls[0]
            externo = safe.content
            conteudo = externo.content
            cabecalho = conteudo.controls[0]
            linha_topo = cabecalho.controls[0]
            linha_topo.controls[0] = ft.Image(
                src='brand.svg',
                width=44,
                height=44,
                fit=ft.ImageFit.CONTAIN,
                anti_alias=True,
                semantics_label='Logo do Migrador de Playlists',
            )
        except Exception:
            # A identidade visual nunca deve impedir o app de iniciar.
            pass

    Tela.montar = montar_com_logo
    Tela._mobile_branding_applied = True
