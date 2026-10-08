"""Exercise desktop history actions without Tk windows or provider requests."""
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import app


class Field:
    def __init__(self, text=''):
        self.text = text
        self.options = {}

    def get(self):
        return self.text

    def delete(self, *_):
        self.text = ''

    def insert(self, _index, text):
        self.text = text

    def configure(self, **options):
        self.options.update(options)

    def set(self, value):
        self.text = value


def screen():
    view = SimpleNamespace(
        _controle=None, modo='sp_yt',
        _rotulos_modo={'Spotify  ➔  YouTube Music': 'sp_yt', 'YouTube Music  ➔  Spotify': 'yt_sp'},
        seg_modo=Field(), entry_spotify=Field('unrelated-source'), entry_yt=Field('unrelated-destination'),
        lbl_campo_origem=Field(), lbl_campo_destino=Field(),
        btn_iniciar=Field(), btn_pausar=Field(), btn_cancelar=Field(),
        focus_set=Mock(), _ui_progresso_reset=Mock(), _limpar_log=Mock(),
        processo_migracao=Mock(), processo_migracao_reversa=Mock(),
        iniciar_thread=Mock(),
    )
    for name in ('_ao_trocar_modo', '_ler_registro_historico', '_retomar_historico'):
        setattr(view, name, MethodType(getattr(app.MigradorApp, name), view))
    return view


def history(**changes):
    record = {'arquivo': '/tmp/progresso_saved.json', 'direcao': 'sp_yt',
              'origem_input': 'https://open.spotify.com/playlist/source', 'origem_id': 'source',
              'nome_playlist': 'Original playlist', 'status': 'concluida', 'adicionadas': 19,
              'legado': False, 'valido': True}
    return dict(record, **changes)


class DesktopSyncUITests(unittest.TestCase):
    def setUp(self):
        self.records = []
        self.addCleanup(patch.stopall)
        patch.object(app, 'listar_historico_migracoes', lambda: self.records, create=True).start()
        self.messages = patch.object(app, 'messagebox').start()

    def test_resume_prefills_saved_direction_and_source_before_start(self):
        for direction, source in [('sp_yt', 'https://open.spotify.com/playlist/source'),
                                  ('yt_sp', 'https://music.youtube.com/playlist?list=source')]:
            with self.subTest(direction=direction):
                view, dialog = screen(), Mock()
                record = history(direcao=direction, origem_input=source)
                self.records[:] = [record]
                view._retomar_historico(record, dialog)
                self.assertEqual(view.modo, direction)
                self.assertEqual(view.entry_spotify.get(), source)
                self.assertEqual(view.entry_yt.get(), 'Original playlist')
                self.assertEqual(view._rotulos_modo[view.seg_modo.get()], direction)
                dialog.destroy.assert_called_once_with()
                view.iniciar_thread.assert_called_once_with(history_record=record)

    def test_running_migration_cannot_be_replaced_by_history_action(self):
        view, dialog = screen(), Mock()
        view._controle = object()
        view._retomar_historico(history(), dialog)
        self.assertEqual(view.entry_spotify.get(), 'unrelated-source')
        self.assertEqual(view.entry_yt.get(), 'unrelated-destination')
        dialog.destroy.assert_not_called()
        view.iniciar_thread.assert_not_called()

    def test_removed_or_corrupt_checkpoint_never_starts_new_destination(self):
        for current in ([], [history(valido=False)]):
            with self.subTest(current=current):
                self.records[:] = current
                view, dialog = screen(), Mock()
                view._retomar_historico(history(), dialog)
                dialog.destroy.assert_not_called()
                view.iniciar_thread.assert_not_called()
                self.assertEqual(view.entry_spotify.get(), 'unrelated-source')
        self.assertEqual(self.messages.showwarning.call_count, 2)

    def test_legacy_history_requires_original_source_and_preserves_checkpoint(self):
        view, dialog = screen(), Mock()
        record = history(legado=True, origem_input=None, origem_id=None,
                         arquivo='/tmp/progresso_legacy_Original_playlist.json')
        self.records[:] = [record]
        source_dialog = Mock()
        source_dialog.get_input.return_value = '  https://open.spotify.com/playlist/original  '
        with patch.object(app.ctk, 'CTkInputDialog', return_value=source_dialog) as ask:
            view._retomar_historico(record, dialog)
        ask.assert_called_once()
        self.assertEqual(view.entry_spotify.get(), 'https://open.spotify.com/playlist/original')
        self.assertEqual(view.entry_yt.get(), 'Original playlist')
        view.iniciar_thread.assert_called_once_with(history_record=record)

    def test_cancelled_legacy_prompt_leaves_fields_and_history_unchanged(self):
        view, dialog = screen(), Mock()
        record = history(legado=True, origem_input=None)
        self.records[:] = [record]
        with patch.object(app.ctk, 'CTkInputDialog') as ask:
            ask.return_value.get_input.return_value = None
            view._retomar_historico(record, dialog)
        dialog.destroy.assert_not_called()
        view.iniciar_thread.assert_not_called()
        self.assertEqual(view.entry_spotify.get(), 'unrelated-source')

    def test_modern_record_without_origin_is_not_treated_as_legacy(self):
        view, dialog = screen(), Mock()
        record = history(origem_input=None)
        self.records[:] = [record]
        with patch.object(app.ctk, 'CTkInputDialog') as ask:
            view._retomar_historico(record, dialog)
        ask.assert_not_called()
        view.iniciar_thread.assert_not_called()
        self.messages.showwarning.assert_called_once()

    def test_start_guard_precedes_authentication_or_field_reads(self):
        view = screen()
        view._controle = object()
        view.entry_spotify = Mock()
        with patch.object(app, 'spotify_vinculado') as linked:
            app.MigradorApp.iniciar_thread(view, history_record=history())
        view.entry_spotify.get.assert_not_called()
        linked.assert_not_called()
        self.messages.showwarning.assert_not_called()

    def test_checkpoint_is_rechecked_before_thread_launch(self):
        view = screen()
        with patch.object(app, 'spotify_vinculado') as linked, patch.object(app.threading, 'Thread') as thread:
            app.MigradorApp.iniciar_thread(view, history_record=history())
        linked.assert_not_called()
        thread.assert_not_called()
        self.messages.showwarning.assert_called_once()

    def test_resume_worker_receives_exact_checkpoint_and_fresh_source(self):
        for direction in ('sp_yt', 'yt_sp'):
            with self.subTest(direction=direction):
                view = screen()
                view.modo = direction
                view.entry_spotify.text = 'current-source'
                view.entry_yt.text = 'saved-destination'
                record = history(direcao=direction)
                self.records[:] = [record]
                control = object()
                with patch.object(app, 'spotify_vinculado', return_value=True), \
                        patch.object(app.os.path, 'exists', return_value=True), \
                        patch.object(app, 'carregar_auth_spotify') as auth, \
                        patch.object(app, 'ControleMigracao', return_value=control), \
                        patch.object(app, 'definir_ativo'), \
                        patch.object(app.threading, 'Thread') as thread:
                    auth.return_value.tem_escrita.return_value = True
                    app.MigradorApp.iniciar_thread(view, history_record=record)
                worker = view.processo_migracao_reversa if direction == 'yt_sp' else view.processo_migracao
                thread.assert_called_once_with(target=worker,
                                               args=('current-source', 'saved-destination', control, record['arquivo']),
                                               daemon=True)
                thread.return_value.start.assert_called_once_with()
                self.assertIs(view._controle, control)

    def test_normal_start_keeps_existing_worker_call_contract(self):
        view = screen()
        control = object()
        with patch.object(app, 'spotify_vinculado', return_value=True), \
                patch.object(app.os.path, 'exists', return_value=True), \
                patch.object(app, 'ControleMigracao', return_value=control), \
                patch.object(app, 'definir_ativo'), \
                patch.object(app.threading, 'Thread') as thread:
            app.MigradorApp.iniciar_thread(view)
        thread.assert_called_once_with(target=view.processo_migracao,
                                       args=('unrelated-source', 'unrelated-destination', control), daemon=True)


if __name__ == '__main__':
    unittest.main()
