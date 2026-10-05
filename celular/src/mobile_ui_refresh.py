# -*- coding: utf-8 -*-
"""Redesign visual da interface Android.

Este módulo não altera a lógica de migração. Ele reaproveita os controles e
callbacks criados por ``main.Tela`` e troca somente a composição visual depois
do patch OAuth de ``mobile_app`` ter sido aplicado.
"""
import flet as ft

BG = '#080A0F'
SURFACE = '#10131A'
SURFACE_2 = '#151922'
FIELD = '#0C0F15'
BORDER = '#242A36'
BORDER_SOFT = '#1B202B'
TEXT = '#F5F7FA'
MUTED = '#8E97A8'
MUTED_2 = '#697386'
SPOTIFY = '#1ED760'
YOUTUBE = '#FF4B55'
ACCENT = '#8BB8FF'
WHITE = '#F7F8FB'

SOCIAL_LINKS = [
    ('GitHub', 'https://github.com/deadbynetsu', '#E6EDF3', ft.Icons.CODE),
    ('Instagram', 'https://www.instagram.com/deadbynetsu.dev/', '#F472B6', ft.Icons.PHOTO_CAMERA),
    ('TikTok', 'https://www.tiktok.com/@deadbynetsu', '#5EEAD4', ft.Icons.MUSIC_NOTE),
    ('Discord', 'https://discord.gg/s9b7R5F6Uh', '#818CF8', ft.Icons.GROUP),
    ('YouTube', 'https://www.youtube.com/@DeadbyNeTsU', '#F87171', ft.Icons.PLAY_CIRCLE),
]


def _style_button(button, *, bg=SURFACE_2, fg=TEXT, border=BORDER, radius=14, padding=13):
    """Aplica um estilo simples, estável e consistente aos botões Flet 1.x."""
    try:
        button.bgcolor = bg
        button.color = fg
        button.style = ft.ButtonStyle(
            elevation=0,
            padding=padding,
            side=ft.BorderSide(1, border) if border else None,
            shape=ft.RoundedRectangleBorder(radius=radius),
        )
    except Exception:
        # Aparência não pode impedir o app de abrir em builds Flet diferentes.
        pass
    return button


def _card(content, *, padding=16, radius=18, bgcolor=SURFACE, border=BORDER_SOFT):
    return ft.Container(
        content=content,
        padding=padding,
        border_radius=radius,
        bgcolor=bgcolor,
        border=ft.Border.all(1, color=border),
    )


def _mini_icon(icon, color):
    return ft.Container(
        width=42,
        height=42,
        border_radius=13,
        bgcolor=SURFACE_2,
        border=ft.Border.all(1, color=BORDER),
        content=ft.Icon(icon, color=color, size=21),
        alignment=ft.Alignment.CENTER,
    )


def _section_label(text):
    return ft.Text(text, size=10, color=MUTED_2, weight=ft.FontWeight.BOLD)


def apply_mobile_ui(target):
    """Aplica o redesign à ``Tela`` encontrada nos globals da função main."""
    g = target.__globals__
    Tela = g.get('Tela')
    nuc = g.get('nuc')
    if Tela is None or nuc is None or getattr(Tela, '_mobile_ui_refresh_applied', False):
        return

    montar_base = Tela.montar
    add_log_base = Tela.add_log
    limpar_log_base = Tela.limpar_log

    def add_log_novo(self, mensagem, tipo='normal'):
        add_log_base(self, mensagem, tipo)
        painel = getattr(self, '_mobile_log_panel', None)
        if painel is not None:
            painel.visible = True

    def limpar_log_novo(self):
        limpar_log_base(self)
        painel = getattr(self, '_mobile_log_panel', None)
        if painel is not None:
            painel.visible = False

    def montar_novo(self):
        # Primeiro deixa o app original criar todos os controles/callbacks.
        montar_base(self)
        p = self.page

        # Base visual.
        p.bgcolor = BG
        p.padding = 0

        # Controles existentes usados pela lógica do app.
        self.lbl_sp.size = 11
        self.lbl_yt.size = 11
        self.lbl_sp.weight = ft.FontWeight.W_500
        self.lbl_yt.weight = ft.FontWeight.W_500

        # O texto fica fora do Switch no layout novo para não estourar em telas estreitas.
        self.sw_update.label = ''
        self.sw_update.active_color = ACCENT
        self.btn_update.content = 'Verificar agora'
        self.btn_update.expand = True
        _style_button(self.btn_update, bg=SURFACE_2, fg=ACCENT, padding=11)

        self.seg.show_selected_icon = False
        self.seg.padding = 0
        try:
            self.seg.style = ft.ButtonStyle(
                elevation=0,
                padding=10,
                side=ft.BorderSide(1, BORDER),
                shape=ft.RoundedRectangleBorder(radius=13),
            )
        except Exception:
            pass
        self.seg.segments = [
            ft.Segment(value='sp_yt', label=ft.Text('Spotify  →  YT Music', size=12, weight=ft.FontWeight.W_600)),
            ft.Segment(value='yt_sp', label=ft.Text('YT Music  →  Spotify', size=12, weight=ft.FontWeight.W_600)),
        ]

        for campo in (self.campo_origem, self.campo_destino):
            campo.bgcolor = FIELD
            campo.border_color = BORDER
            campo.focused_border_color = ACCENT
            campo.border_radius = 14
            campo.text_size = 14
            campo.cursor_color = ACCENT
            try:
                campo.content_padding = 15
            except Exception:
                pass

        # Expand só funciona no eixo principal do pai. O botão principal entra em
        # uma Row dedicada para ocupar a largura sem crescer verticalmente.
        self.btn_iniciar.expand = True
        self.btn_pausar.expand = True
        self.btn_cancelar.expand = True
        _style_button(self.btn_iniciar, bg=WHITE, fg='#090B10', border=WHITE, padding=14)
        _style_button(self.btn_pausar, bg=SURFACE_2, fg=TEXT, padding=14)
        _style_button(self.btn_cancelar, bg=SURFACE_2, fg=YOUTUBE, padding=13)

        self.lbl_prog_titulo.size = 14
        self.lbl_prog_titulo.color = TEXT
        self.lbl_prog_detalhe.size = 11
        self.lbl_prog_detalhe.color = MUTED
        self.barra.color = SPOTIFY
        self.barra.bgcolor = BORDER

        # Cabeçalho: compacto e com identidade visual própria.
        logo = ft.Container(
            width=44,
            height=44,
            border_radius=14,
            bgcolor=SURFACE_2,
            border=ft.Border.all(1, color=BORDER),
            alignment=ft.Alignment.CENTER,
            content=ft.Column(
                controls=[
                    ft.Container(width=19, height=3, border_radius=4, bgcolor=SPOTIFY),
                    ft.Container(width=19, height=3, border_radius=4, bgcolor=YOUTUBE),
                ],
                spacing=5,
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        versao = ft.Container(
            content=ft.Text(f'v{nuc.APP_VERSION}', size=10, color=MUTED),
            padding=ft.Padding.symmetric(horizontal=10, vertical=6),
            border_radius=99,
            bgcolor=SURFACE_2,
            border=ft.Border.all(1, color=BORDER),
        )
        cabecalho = ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        logo,
                        ft.Column(
                            controls=[
                                ft.Text('Migrador de Playlists', size=20, weight=ft.FontWeight.BOLD, color=TEXT),
                                ft.Text('Spotify  ⇄  YouTube Music', size=11, color=MUTED),
                            ],
                            spacing=1,
                            expand=True,
                        ),
                        versao,
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Text(
                    'Leve suas playlists de uma plataforma para a outra sem complicação.',
                    size=12,
                    color=MUTED,
                ),
            ],
            spacing=12,
        )

        # Contas conectadas: substitui os dois cartões altos por linhas compactas.
        btn_sp = _style_button(
            ft.Button(content='Gerenciar', on_click=self.abrir_spotify),
            bg=SURFACE_2, fg=TEXT, padding=10,
        )
        btn_yt = _style_button(
            ft.Button(content='Gerenciar', on_click=self.abrir_yt),
            bg=SURFACE_2, fg=TEXT, padding=10,
        )

        def conta(icon, cor, nome, status, botao):
            return ft.Container(
                content=ft.Row(
                    controls=[
                        _mini_icon(icon, cor),
                        ft.Column(
                            controls=[
                                ft.Text(nome, size=13, weight=ft.FontWeight.BOLD, color=TEXT),
                                status,
                            ],
                            spacing=2,
                            expand=True,
                        ),
                        botao,
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=12,
                border_radius=16,
                bgcolor=SURFACE,
                border=ft.Border.all(1, color=BORDER_SOFT),
            )

        contas = ft.Column(
            controls=[
                _section_label('CONTAS'),
                conta(ft.Icons.MUSIC_NOTE, SPOTIFY, 'Spotify', self.lbl_sp, btn_sp),
                conta(ft.Icons.PLAY_CIRCLE, YOUTUBE, 'YouTube Music', self.lbl_yt, btn_yt),
            ],
            spacing=9,
        )

        # Área principal de migração.
        historico = ft.TextButton(
            content='Histórico',
            icon=ft.Icons.HISTORY,
            on_click=self.abrir_historico,
        )
        try:
            historico.style = ft.ButtonStyle(color=ACCENT, padding=8)
        except Exception:
            pass

        migracao = _card(
            ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Column(
                                controls=[
                                    _section_label('NOVA MIGRAÇÃO'),
                                    ft.Text('Escolha o sentido e cole sua playlist', size=14,
                                            weight=ft.FontWeight.BOLD, color=TEXT),
                                ],
                                spacing=2,
                                expand=True,
                            ),
                            historico,
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    self.seg,
                    self.campo_origem,
                    self.campo_destino,
                    ft.Row(controls=[self.btn_iniciar]),
                    ft.Row(
                        controls=[self.btn_pausar, self.btn_cancelar],
                        spacing=10,
                    ),
                ],
                spacing=12,
            ),
            padding=16,
            radius=20,
            bgcolor=SURFACE,
            border=BORDER,
        )

        progresso = _card(
            ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Container(
                                width=8,
                                height=8,
                                border_radius=99,
                                bgcolor=SPOTIFY,
                            ),
                            self.lbl_prog_titulo,
                        ],
                        spacing=8,
                    ),
                    self.barra,
                    self.lbl_prog_detalhe,
                ],
                spacing=10,
            ),
            padding=14,
            radius=17,
            bgcolor='#0D1118',
            border=BORDER_SOFT,
        )

        # Log some da tela quando vazio; aparece automaticamente na primeira mensagem.
        self._mobile_log_panel = _card(
            ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.TERMINAL, color=MUTED, size=17),
                            ft.Text('Atividade', size=12, weight=ft.FontWeight.BOLD, color=TEXT),
                        ],
                        spacing=7,
                    ),
                    ft.Container(
                        content=self.lista_log,
                        height=190,
                        padding=10,
                        border_radius=13,
                        bgcolor='#07090E',
                        border=ft.Border.all(1, color=BORDER_SOFT),
                    ),
                ],
                spacing=10,
            ),
            padding=13,
            radius=17,
            bgcolor=SURFACE,
            border=BORDER_SOFT,
        )
        self._mobile_log_panel.visible = bool(self.lista_log.controls)

        # Links iguais aos da versão de PC, mas organizados para toque no celular.
        def abrir_link(url):
            async def acao(e):
                await self.launcher.launch_url(url)
            return acao

        def social_button(nome, url, cor, icon):
            botao = ft.Button(
                content=nome,
                icon=icon,
                on_click=abrir_link(url),
                expand=True,
            )
            try:
                botao.icon_color = cor
            except Exception:
                pass
            return _style_button(botao, bg=SURFACE_2, fg=TEXT, border=BORDER, padding=11)

        sociais = [social_button(nome, url, cor, icon) for nome, url, cor, icon in SOCIAL_LINKS]
        comunidade = _card(
            ft.Column(
                controls=[
                    _section_label('LINKS & COMUNIDADE'),
                    ft.Text('Acompanhe o projeto', size=13, weight=ft.FontWeight.BOLD, color=TEXT),
                    ft.Text('Os mesmos links da versão para PC, agora direto no app.', size=10, color=MUTED),
                    ft.Row(controls=[sociais[0], sociais[1]], spacing=9),
                    ft.Row(controls=[sociais[2], sociais[3]], spacing=9),
                    ft.Row(controls=[sociais[4]]),
                ],
                spacing=9,
            ),
            padding=14,
            radius=17,
            bgcolor=SURFACE,
            border=BORDER_SOFT,
        )

        # Atualizações ficam no fim: acessíveis, mas sem ocupar o topo da experiência.
        atualizacoes = _card(
            ft.Column(
                controls=[
                    _section_label('APLICATIVO'),
                    ft.Row(
                        controls=[
                            ft.Column(
                                controls=[
                                    ft.Text('Atualizações automáticas', size=13, weight=ft.FontWeight.BOLD, color=TEXT),
                                    ft.Text('Busca uma versão nova quando o app abre.', size=10, color=MUTED),
                                ],
                                spacing=1,
                                expand=True,
                            ),
                            self.sw_update,
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row(controls=[self.btn_update]),
                ],
                spacing=10,
            ),
            padding=14,
            radius=17,
            bgcolor=SURFACE,
            border=BORDER_SOFT,
        )

        rodape = ft.Column(
            controls=[
                ft.Text('Feito por deadbynetsu', size=10, color=MUTED_2,
                        text_align=ft.TextAlign.CENTER),
                ft.Text('Open source • Spotify ⇄ YouTube Music', size=9, color='#4F5869',
                        text_align=ft.TextAlign.CENTER),
            ],
            spacing=2,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

        conteudo = ft.Column(
            controls=[
                cabecalho,
                contas,
                migracao,
                progresso,
                self._mobile_log_panel,
                comunidade,
                atualizacoes,
                rodape,
            ],
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
        )

        # Remove a árvore antiga e insere a nova sem recriar a lógica.
        p.controls.clear()
        p.add(
            ft.SafeArea(
                expand=True,
                content=ft.Container(
                    content=conteudo,
                    padding=ft.Padding.only(left=16, top=14, right=16, bottom=22),
                    expand=True,
                ),
            )
        )
        self.atualizar_status()

    Tela.add_log = add_log_novo
    Tela.limpar_log = limpar_log_novo
    Tela.montar = montar_novo
    Tela._mobile_ui_refresh_applied = True
