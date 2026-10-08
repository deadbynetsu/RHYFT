"""Android history actions without starting Flet or contacting either provider."""
from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MOBILE = ROOT / 'celular' / 'src'
try:
    import flet as ft
except ImportError:
    ft = None


class FakePage:
    def __init__(self):
        self.controls = []
        self.dialogs = []
        self.updates = 0

    def add(self, control):
        self.controls.append(control)

    def update(self):
        self.updates += 1

    def clean(self):
        self.controls.clear()

    def show_dialog(self, dialog):
        self.dialogs.append(dialog)

    def pop_dialog(self):
        return self.dialogs.pop()


def children(control):
    content = getattr(control, 'content', None)
    if content is not None and not isinstance(content, str):
        yield content
    yield from (getattr(control, 'controls', None) or [])


def walk(control):
    yield control
    for child in children(control):
        yield from walk(child)


@unittest.skipIf(ft is None, 'Requires the Android Flet 1.0.3 dependency')
class MobileSyncUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.storage = tempfile.TemporaryDirectory(prefix='rhyft-mobile-ui-')
        cls.addClassCleanup(cls.storage.cleanup)
        spec = importlib.util.spec_from_file_location('rhyft_android_sync_ui', MOBILE / 'main.py')
        cls.ui = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {'MIGRADOR_DATA_DIR': cls.storage.name,
                                     'FLET_APP_STORAGE_DATA': cls.storage.name}), \
                patch.object(sys, 'path', [str(MOBILE), *sys.path]), \
                patch.object(ft, 'run'):
            spec.loader.exec_module(cls.ui)

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        nuc = self.ui.nuc
        original_open = nuc.abrir_url_externa
        self.addCleanup(setattr, nuc, 'abrir_url_externa', original_open)
        auth = SimpleNamespace(vinculado=lambda: True, tem_escrita=lambda: True)
        self.stack.enter_context(patch.object(nuc, 'carregar_auth_spotify', return_value=auth))
        self.stack.enter_context(patch.object(nuc, 'spotify_vinculado', return_value=True))
        self.stack.enter_context(patch.object(nuc, 'checar_atualizacoes_habilitadas', return_value=False))
        self.stack.enter_context(patch.object(nuc, 'registrar_erro_em_arquivo'))
        real_exists = os.path.exists
        self.stack.enter_context(patch.object(self.ui.os.path, 'exists', side_effect=lambda path:
            True if str(path) == str(nuc.YT_AUTH_PATH) else real_exists(path)))
        self.thread = self.stack.enter_context(patch.object(self.ui.threading, 'Thread'))
        self.records = []
        self.history = self.stack.enter_context(patch.object(nuc, 'listar_historico_migracoes',
            side_effect=lambda: list(self.records), create=True))
        self.page = FakePage()
        self.tela = self.ui.Tela(self.page)
        self.tela.montar()

    def record(self, direction='sp_yt', *, source='source-playlist', valid=True):
        return {'arquivo': str(Path(self.storage.name) / f'progresso_{direction}.json'),
                'direcao': direction, 'origem_input': source, 'origem_id': source,
                'nome_playlist': 'Playlist salva', 'status': 'completed',
                'adicionadas': 12, 'legado': source is None, 'valido': valid}

    def history_buttons(self):
        self.tela.abrir_historico(None)
        listing = self.page.dialogs[-1].content.controls[1]
        return [item.content.controls[-1].controls for item in listing.controls
                if isinstance(item, ft.Container)]

    def test_history_starts_both_directions_with_exact_saved_file(self):
        for direction in ('sp_yt', 'yt_sp'):
            with self.subTest(direction=direction):
                self.tela.controle = None
                self.thread.reset_mock()
                self.records = [self.record(direction)]
                self.history_buttons()[0][0].on_click(None)
                args = self.thread.call_args.kwargs['args']
                self.assertEqual(args[:2], ('source-playlist', 'Playlist salva'))
                self.assertIs(args[2], self.tela.controle)
                self.assertEqual(args[3], self.records[0]['arquivo'])
                expected = (self.tela.motor.processo_migracao_reversa if direction == 'yt_sp'
                            else self.tela.motor.processo_migracao)
                self.assertEqual(self.thread.call_args.kwargs['target'], expected)
                self.assertEqual(self.tela.modo, direction)
                self.assertEqual(self.tela.seg.selected, [direction])
                self.assertIsNone(self.tela._historico_selecionado)
                self.assertEqual(self.page.dialogs, [])
                self.thread.return_value.start.assert_called_once()

    def test_normal_migration_keeps_three_arguments(self):
        self.tela.campo_origem.value = 'another-playlist'
        self.tela.campo_destino.value = 'Nova playlist'
        self.tela.iniciar(None)
        self.assertEqual(len(self.thread.call_args.kwargs['args']), 3)

    def test_history_uses_wrapped_start_and_clears_record_if_login_blocks(self):
        self.records = [self.record()]
        seen = []
        original_start = self.ui.Tela.iniciar

        def login_wrapper(instance, event):
            seen.append(instance._historico_selecionado['arquivo'])
            return original_start(instance, event)

        with patch.object(self.ui.Tela, 'iniciar', login_wrapper):
            self.history_buttons()[0][0].on_click(None)
        self.assertEqual(seen, [self.records[0]['arquivo']])
        self.assertEqual(len(self.thread.call_args.kwargs['args']), 4)
        self.tela.controle = None
        self.thread.reset_mock()
        with patch.object(self.tela, 'iniciar', return_value=None):
            self.history_buttons()[0][0].on_click(None)
        self.assertIsNone(self.tela._historico_selecionado)
        self.tela.campo_origem.value = 'different-source'
        self.tela.iniciar(None)
        self.assertEqual(len(self.thread.call_args.kwargs['args']), 3)

    def test_running_migration_prevents_resume_and_deletion_even_with_stale_buttons(self):
        self.records = [self.record('yt_sp')]
        buttons = self.history_buttons()[0]
        self.tela.campo_origem.value = 'current-source'
        self.tela.campo_destino.value = 'Current playlist'
        self.tela.controle = object()
        buttons[0].on_click(None)
        self.assertEqual(self.tela.campo_origem.value, 'current-source')
        self.assertEqual(self.tela.campo_destino.value, 'Current playlist')
        self.assertEqual(self.tela.modo, 'sp_yt')
        self.thread.assert_not_called()
        with patch.object(self.ui.os, 'remove') as remove:
            buttons[1].on_click(None)
        remove.assert_not_called()

    def test_invalid_and_removed_records_never_start_a_new_playlist(self):
        self.records = [self.record(valid=False)]
        buttons = self.history_buttons()[0]
        self.assertTrue(buttons[0].disabled)
        self.assertFalse(buttons[1].disabled)
        buttons[0].on_click(None)
        self.thread.assert_not_called()
        self.records = [self.record()]
        buttons = self.history_buttons()[0]
        self.records = []
        buttons[0].on_click(None)
        self.thread.assert_not_called()
        self.assertFalse(self.tela.campo_origem.value)
        self.assertIsNone(self.tela.controle)

    def test_legacy_history_requests_source_and_keeps_saved_destination(self):
        self.records = [self.record('yt_sp', source=None)]
        self.history_buttons()[0][0].on_click(None)
        self.thread.assert_not_called()
        dialog = self.page.dialogs[-1]
        field = dialog.content.controls[1]
        self.assertFalse(field.value)
        self.assertEqual(field.label, self.ui.tr('Link ou ID da playlist do YouTube Music'))
        dialog.actions[-1].on_click(None)
        self.assertTrue(field.error_text)
        self.assertEqual(self.page.dialogs, [dialog])
        self.thread.assert_not_called()
        field.value = 'https://music.youtube.com/playlist?list=source-id'
        dialog.actions[-1].on_click(None)
        args = self.thread.call_args.kwargs['args']
        self.assertEqual(args[0], field.value)
        self.assertEqual(args[3], self.records[0]['arquivo'])
        self.assertEqual(self.tela.modo, 'yt_sp')
        self.assertEqual(self.page.dialogs, [])

    def test_legacy_record_is_rechecked_after_source_prompt(self):
        self.records = [self.record(source=None)]
        self.history_buttons()[0][0].on_click(None)
        dialog = self.page.dialogs[-1]
        dialog.content.controls[1].value = 'source-id'
        self.records = []
        dialog.actions[-1].on_click(None)
        self.thread.assert_not_called()
        self.assertIsNone(self.tela.controle)

    def test_mobile_redesign_and_branding_preserve_history_callback(self):
        with patch.object(sys, 'path', [str(MOBILE), *sys.path]):
            from mobile_ui_refresh import apply_mobile_ui
            from mobile_branding import apply_mobile_branding
        with ExitStack() as wrappers:
            for name in ('montar', 'add_log', 'limpar_log'):
                wrappers.enter_context(patch.object(self.ui.Tela, name, getattr(self.ui.Tela, name)))
            for name in ('_mobile_ui_refresh_applied', '_mobile_branding_applied'):
                wrappers.enter_context(patch.object(self.ui.Tela, name, False, create=True))
            apply_mobile_ui(self.ui.main)
            apply_mobile_branding(self.ui.main)
            page = FakePage()
            tela = self.ui.Tela(page)
            tela.montar()
            controls = [control for root in page.controls for control in walk(root)]
            history = next(control for control in controls
                           if isinstance(control, ft.TextButton)
                           and control.content == self.ui.tr('Histórico'))
            self.assertEqual(history.on_click, tela.abrir_historico)
            self.records = [self.record()]
            history.on_click(None)
            self.assertEqual(page.dialogs[-1].title.value, self.ui.tr('Progresso salvo'))


if __name__ == '__main__':
    unittest.main()
