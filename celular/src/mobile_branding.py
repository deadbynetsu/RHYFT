# -*- coding: utf-8 -*-
"""Aplica a identidade visual oficial na interface Android."""
import flet as ft


def _filhos(controle):
    """Percorre a árvore Flet sem depender da estrutura exata da página."""
    vistos = set()

    content = getattr(controle, 'content', None)
    if content is not None:
        vistos.add(id(content))
        yield content

    controls = getattr(controle, 'controls', None)
    if controls:
        for filho in controls:
            if filho is not None and id(filho) not in vistos:
                vistos.add(id(filho))
                yield filho


def _eh_logo_provisoria(controle):
    """Reconhece o quadrado com as duas barras verde/vermelha do redesign."""
    if not isinstance(controle, ft.Container):
        return False
    if getattr(controle, 'width', None) != 44 or getattr(controle, 'height', None) != 44:
        return False

    coluna = getattr(controle, 'content', None)
    itens = getattr(coluna, 'controls', None) if coluna is not None else None
    if not itens or len(itens) != 2:
        return False

    try:
        return all(
            isinstance(item, ft.Container)
            and getattr(item, 'width', None) == 19
            and getattr(item, 'height', None) == 3
            for item in itens
        )
    except Exception:
        return False


def _trocar_logo(raiz):
    pilha = [raiz]
    visitados = set()

    while pilha:
        atual = pilha.pop()
        if atual is None or id(atual) in visitados:
            continue
        visitados.add(id(atual))

        if _eh_logo_provisoria(atual):
            # Mantemos o container de 44x44 e trocamos só o desenho interno.
            # Assim o alinhamento do cabeçalho permanece exatamente igual.
            atual.content = ft.Image(
                src='brand.svg',
                width=36,
                height=36,
                fit=ft.ImageFit.CONTAIN,
                anti_alias=True,
                semantics_label='Logo do Migrador de Playlists',
            )
            atual.alignment = ft.Alignment.CENTER
            return True

        pilha.extend(_filhos(atual))

    return False


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
            # Procura recursivamente em toda a página. A versão antiga dependia
            # de uma cadeia fixa de .content/.controls e falhava silenciosamente
            # quando outro wrapper adicionava um nível à árvore.
            for raiz in list(getattr(self.page, 'controls', []) or []):
                if _trocar_logo(raiz):
                    break
        except Exception:
            # Branding nunca deve impedir o app de iniciar.
            pass

    Tela.montar = montar_com_logo
    Tela._mobile_branding_applied = True
