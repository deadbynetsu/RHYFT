"""Exercise incremental desktop/Android migrations without provider accounts or UI."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


DIRECTIONS = ('sp_yt', 'yt_sp')
ARTIST = 'Artista'
SP_SOURCE = 'SRC0000000000000000001'
YT_SOURCE = 'PLSource000000000000000000000001'


def song(index, prefix='Faixa'):
    return {'index': index, 'name': f'{prefix} {index:02d}', 'spotify_id': f'Source{index:016d}',
            'youtube_id': f'Video{index:06d}', 'target_spotify_id': f'Target{index:016d}'}


def stock_youtube_account_info():
    # These are the three fields returned by ytmusicapi.get_account_info().
    return {'accountName': 'Conta de teste', 'channelHandle': '@conta-de-teste',
            'accountPhotoUrl': 'https://yt3.ggpht.com/test-account-photo'}


def youtube_account_menu(own_channel=None, other_channel=None):
    def section(channel):
        return {'multiPageMenuSectionRenderer': {'items': [{'compactLinkRenderer': {
            'icon': {'iconType': 'ACCOUNT_BOX'}, 'navigationEndpoint': {'browseEndpoint': {
                'browseId': channel, 'browseEndpointContextSupportedConfigs': {
                    'browseEndpointContextMusicConfig': {'pageType': 'MUSIC_PAGE_TYPE_USER_CHANNEL'}}}}}}]}}

    header = {'accountName': {'runs': [{'text': 'Conta de teste'}]},
              'channelHandle': {'runs': [{'text': '@conta-de-teste'}]},
              'accountPhoto': {'thumbnails': [{'url': 'https://yt3.ggpht.com/test-account-photo'}]}}
    active = {'header': {'activeAccountHeaderRenderer': header},
              'sections': [section(own_channel)] if own_channel else []}
    actions = [{'openPopupAction': {'popup': {'multiPageMenuRenderer': active}}}]
    if other_channel:
        switcher = {'header': {'accountSwitcherHeaderRenderer': {}}, 'sections': [section(other_channel)]}
        # A switch-account popup is not the active account's own-channel link.
        actions.append({'openPopupAction': {'popup': {'multiPageMenuRenderer': switcher}}})
    return {'actions': actions}


class Providers:
    """Lists deliberately preserve duplicate writes, as real playlist APIs do."""
    def __init__(self, songs):
        self.spotify_sources = {SP_SOURCE: list(songs)}
        self.youtube_sources = {YT_SOURCE: list(songs)}
        self.catalog = list(songs)
        self.destinations = {'sp_yt': {}, 'yt_sp': {}}
        self.creates = []
        self.searches = []
        self.writes = []
        self.source_reads = []
        self.destination_reads = []
        self.accounts = {'sp_yt': 'UCNativeYoutubeAccount', 'yt_sp': 'NativeSpotifyAccount'}
        self.liked_sources = {self.accounts['sp_yt']: list(songs)}
        self.liked_ids = {}
        self.account_error = None
        self.account_errors = {'sp_yt': None, 'yt_sp': None}
        self.youtube_account_info = None
        self.youtube_account_menu = {}
        self.account_menu_reads = []
        self.read_error = None
        self.drop_writes = False
        self.spotify = FakeSpotify(self)
        self.youtube = FakeYoutube(self)

    def create(self, direction, name):
        number = len(self.destinations[direction]) + 1
        destination = f'PL{number:032d}' if direction == 'sp_yt' else f'DST{number:019d}'
        self.destinations[direction][destination] = {'name': name, 'ids': [], 'account': self.accounts[direction]}
        self.creates.append((direction, destination, name))
        return destination

    def write(self, direction, destination, ids):
        self.writes.append((direction, destination, list(ids)))
        if not self.drop_writes:
            self.destinations[direction][destination]['ids'].extend(ids)

    def candidate(self, query):
        return next(item for item in self.catalog if item['name'] in query)

    def target_id(self, direction, item):
        return item['youtube_id'] if direction == 'sp_yt' else item['target_spotify_id']

    def written_ids(self, direction):
        return [item for found_direction, _, ids in self.writes if found_direction == direction for item in ids]


class FakeSpotify:
    def __init__(self, providers):
        self.providers = providers

    def me(self):
        if error := self.providers.account_error or self.providers.account_errors['yt_sp']:
            raise error
        return {'id': self.providers.accounts['yt_sp']}

    current_user = me

    def playlist_items(self, playlist_id, offset=0, **kwargs):
        self.providers.source_reads.append(('sp_yt', playlist_id, offset))
        items = self.providers.spotify_sources[playlist_id]
        # Pagination exercises a fresh full source read on every update.
        page = items[offset:offset + 8]
        return {'items': [{'track': {'id': item['spotify_id'], 'name': item['name'],
                'artists': [{'name': ARTIST}], 'duration_ms': 180000}} for item in page],
                'next': 'next-page' if offset + len(page) < len(items) else None, 'total': len(items)}

    def search(self, q, **kwargs):
        self.providers.searches.append(('yt_sp', q))
        item = self.providers.candidate(q)
        return {'tracks': {'items': [{'id': item['target_spotify_id'], 'name': item['name'],
                'artists': [{'name': ARTIST}], 'duration_ms': 180000, 'is_playable': True}]}}

    def _post(self, endpoint, payload):
        if endpoint == 'me/playlists':
            return {'id': self.providers.create('yt_sp', payload['name'])}
        parts = endpoint.split('/')
        if parts[0] != 'playlists' or parts[2] != 'items':
            raise AssertionError(f'Unexpected Spotify write: {endpoint}')
        ids = [uri.split(':')[-1] for uri in payload['uris']]
        self.providers.write('yt_sp', parts[1], ids)
        return {'snapshot_id': 'confirmed'}

    def _get(self, endpoint, limit=50, offset=0, **kwargs):
        if self.providers.read_error:
            raise self.providers.read_error
        parts = endpoint.split('/')
        if len(parts) == 2 and parts[0] == 'playlists':
            destination = self.providers.destinations['yt_sp'][parts[1]]
            self.providers.destination_reads.append(('yt_sp', parts[1], 'metadata', 0))
            return {'id': parts[1], 'name': destination['name'], 'owner': {'id': destination['account']}}
        if parts[0] != 'playlists' or parts[2] != 'items':
            raise AssertionError(f'Unexpected Spotify read: {endpoint}')
        destination = parts[1]
        self.providers.destination_reads.append(('yt_sp', destination, limit, offset))
        ids = self.providers.destinations['yt_sp'][destination]['ids']
        page = ids[offset:offset + limit]
        return {'total': len(ids), 'items': [{'item': {'id': item}} for item in page],
                'next': 'next-page' if offset + len(page) < len(ids) else None}


class FakeYoutube:
    def __init__(self, providers):
        self.providers = providers

    def get_account_info(self):
        if error := self.providers.account_error or self.providers.account_errors['sp_yt']:
            raise error
        if self.providers.youtube_account_info is not None:
            return dict(self.providers.youtube_account_info)
        return {'channelId': self.providers.accounts['sp_yt']}

    def _check_auth(self):
        pass  # Provider clients in this fixture represent an authenticated user.

    def _send_request(self, endpoint, body):
        if endpoint != 'account/account_menu' or body != {}:
            raise AssertionError(f'Unexpected authenticated YouTube operation: {endpoint}')
        self.providers.account_menu_reads.append(endpoint)
        return self.providers.youtube_account_menu

    def create_playlist(self, title, **kwargs):
        return self.providers.create('sp_yt', title)

    def add_playlist_items(self, playlist_id, ids):
        self.providers.write('sp_yt', playlist_id, ids)
        return {'status': 'STATUS_SUCCEEDED'}

    def get_liked_songs(self, limit=None):
        self.providers.source_reads.append(('yt_sp', 'LM', 0))
        items = self.providers.liked_sources[self.providers.accounts['sp_yt']]
        return {'id': self.providers.liked_ids.get(self.providers.accounts['sp_yt'], 'LM'),
                'title': 'Músicas curtidas', 'trackCount': len(items), 'tracks': [
            {'videoId': item['youtube_id'], 'title': item['name'], 'artists': [{'name': ARTIST}],
             'duration_seconds': 180, 'videoType': 'MUSIC_VIDEO_TYPE_ATV'} for item in items]}

    def get_playlist(self, playlist_id, limit=100, **kwargs):
        if playlist_id in self.providers.youtube_sources:
            self.providers.source_reads.append(('yt_sp', playlist_id, 0))
            items = self.providers.youtube_sources[playlist_id]
            return {'title': 'Origem nativa', 'trackCount': len(items), 'tracks': [
                {'videoId': item['youtube_id'], 'title': item['name'], 'artists': [{'name': ARTIST}],
                 'duration_seconds': 180, 'videoType': 'MUSIC_VIDEO_TYPE_ATV'} for item in items]}
        if self.providers.read_error:
            raise self.providers.read_error
        self.providers.destination_reads.append(('sp_yt', playlist_id, limit, 0))
        destination = self.providers.destinations['sp_yt'][playlist_id]
        ids = destination['ids'] if limit is None else destination['ids'][:limit]
        return {'title': destination['name'], 'trackCount': len(destination['ids']),
                'tracks': [{'videoId': item} for item in ids]}

    def search(self, query, **kwargs):
        self.providers.searches.append(('sp_yt', query))
        item = self.providers.candidate(query)
        return [{'videoId': item['youtube_id'], 'title': item['name'], 'artists': [{'name': ARTIST}],
                 'duration_seconds': 180, 'resultType': 'song'}]


class NativeSyncTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data_dir = Path(self.directory.name)
        root = Path(__file__).resolve().parents[1]
        with patch.dict(os.environ, {'MIGRADOR_DATA_DIR': str(self.data_dir)}):
            # Mobile language tests prepend celular/src to sys.path. Always exercise
            # the shared source that the release build copies into the APK.
            spec = importlib.util.spec_from_file_location('rhyft_native_sync_core', root / 'nucleo.py')
            self.core = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.core)
        self.providers = Providers([song(i) for i in range(19)])
        auth = type('AuthorizedSpotify', (), {'vinculado': lambda self: True, 'tem_escrita': lambda self: True})()
        patches = [
            patch.object(self.core, 'DATA_DIR', str(self.data_dir)),
            patch.object(self.core, 'LOG_ERROS_PATH', str(self.data_dir / 'errors.log')),
            patch.object(self.core, 'carregar_auth_spotify', return_value=auth),
            patch.object(self.core.spotipy, 'Spotify', return_value=self.providers.spotify),
            patch.object(self.core, 'YTMusic', return_value=self.providers.youtube),
            patch('requests.sessions.Session.request', side_effect=AssertionError('Native sync tests must never use the network')),
        ]
        for mock in patches:
            mock.start()
            self.addCleanup(mock.stop)

    def source_id(self, direction):
        return SP_SOURCE if direction == 'sp_yt' else YT_SOURCE

    def source_url(self, direction, source=None, share='initial'):
        source = source or self.source_id(direction)
        return (f'https://open.spotify.com/playlist/{source}?si={share}' if direction == 'sp_yt'
                else f'https://music.youtube.com/playlist?list={source}&si={share}')

    def run_migration(self, direction, name='Destino original', source=None, selected=None, success=True):
        class Motor(self.core.MotorMigracao):
            def __init__(self):
                self.logs, self.events = [], []

            def log(self, message, kind='normal'):
                self.logs.append((message, kind))

            def ui(self, function, *args):
                function(*args)

            def __getattr__(self, name):
                if name.startswith('_ui_'):
                    return lambda *args: self.events.append((name, args))
                raise AttributeError(name)

            def escolher_versao(self, *args, **kwargs):
                raise AssertionError('Exact matches must not require manual review')

        motor = Motor()  # A fresh engine represents reopening either native app.
        control = self.core.ControleMigracao()
        control.esperar = lambda *args, **kwargs: False
        method = motor.processo_migracao if direction == 'sp_yt' else motor.processo_migracao_reversa
        arguments = [source or self.source_url(direction), name, control]
        if selected is not None:
            arguments.append(str(selected))
        method(*arguments)
        text = '\n'.join(message for message, _ in motor.logs)
        if success:
            self.assertNotIn('Ocorreu um erro', text, text)
            self.assertIn('processo finalizado', text.casefold(), text)
        else:
            self.assertTrue(any(event == '_ui_progresso_status' and args[0] == 'Migração interrompida'
                                for event, args in motor.events), text)
        self.assertEqual(sum(event == '_ui_migracao_terminada' for event, _ in motor.events), 1)
        return motor

    def record(self, direction, source=None):
        records = [record for record in self.core.listar_historico_migracoes()
                   if record['direcao'] == direction and record['origem_id'] == (source or self.source_id(direction))]
        self.assertEqual(len(records), 1, records)
        summary = records[0]
        self.assertTrue(summary['valido'])
        self.assertFalse(summary['legado'])
        return summary, json.loads(Path(summary['arquivo']).read_text(encoding='utf-8'))

    def test_fresh_incremental_updates_preserve_destination_and_skip_confirmed_searches_and_writes(self):
        for direction in DIRECTIONS:
            with self.subTest(direction=direction):
                self.run_migration(direction)
                summary, saved = self.record(direction)
                destination = saved['playlist_id']
                self.assertEqual(summary['status'], 'completed')
                self.assertEqual(summary['adicionadas'], 19)
                before_searches = len(self.providers.searches)
                before_writes = len(self.providers.writes)
                for index in (19, 20):
                    added = song(index)
                    if direction == 'sp_yt':
                        self.providers.spotify_sources[SP_SOURCE].append(added)
                    else:
                        self.providers.youtube_sources[YT_SOURCE].append(added)
                    self.providers.catalog.append(added)
                    reads_before = len(self.providers.source_reads)
                    self.run_migration(direction, source=self.source_url(direction, share=f'updated-{index}'))
                    latest, checkpoint = self.record(direction)
                    self.assertEqual(latest['arquivo'], summary['arquivo'])
                    self.assertEqual(checkpoint['playlist_id'], destination)
                    self.assertEqual(latest['adicionadas'], index + 1)
                    self.assertEqual(latest['status'], 'completed')
                    self.assertGreater(len(self.providers.source_reads), reads_before)
                    self.assertEqual(len(self.providers.searches), before_searches + index - 18)
                    self.assertEqual(len(self.providers.writes), before_writes + index - 18)
                    self.assertEqual(self.providers.written_ids(direction).count(self.providers.target_id(direction, added)), 1)
                self.assertEqual(sum(item[0] == direction for item in self.providers.creates), 1)
                ids = self.providers.destinations[direction][destination]['ids']
                self.assertEqual(len(ids), 21)
                self.assertEqual(len(set(ids)), 21)
                counts = (len(self.providers.searches), len(self.providers.writes), len(self.providers.creates))
                self.run_migration(direction)
                self.assertEqual((len(self.providers.searches), len(self.providers.writes), len(self.providers.creates)), counts)
                self.assertEqual(self.record(direction)[0]['status'], 'completed')

    def test_changing_only_destination_name_keeps_origin_bound_playlist_and_its_actual_name(self):
        for direction in DIRECTIONS:
            with self.subTest(direction=direction):
                self.run_migration(direction)
                summary, saved = self.record(direction)
                counts = (len(self.providers.searches), len(self.providers.writes), len(self.providers.creates))
                self.run_migration(direction, name='Outro nome digitado', source=self.source_url(direction, share='different-share'))
                latest, checkpoint = self.record(direction)
                self.assertEqual(latest['arquivo'], summary['arquivo'])
                self.assertEqual(checkpoint['playlist_id'], saved['playlist_id'])
                self.assertEqual(latest['nome_playlist'], 'Destino original')
                self.assertEqual((len(self.providers.searches), len(self.providers.writes), len(self.providers.creates)), counts)

    def test_different_origins_with_the_same_destination_name_do_not_share_checkpoints_or_playlists(self):
        for direction in DIRECTIONS:
            with self.subTest(direction=direction):
                self.run_migration(direction)
                summary, original = self.record(direction)
                original_bytes = Path(summary['arquivo']).read_bytes()
                other_source = 'SRC0000000000000000002' if direction == 'sp_yt' else 'PLSource000000000000000000000002'
                other = song(70, 'Outra origem')
                mapping = self.providers.spotify_sources if direction == 'sp_yt' else self.providers.youtube_sources
                mapping[other_source] = [other]
                self.providers.catalog.append(other)
                self.run_migration(direction, source=self.source_url(direction, source=other_source))
                latest, checkpoint = self.record(direction, source=other_source)
                self.assertNotEqual(latest['arquivo'], summary['arquivo'])
                self.assertNotEqual(checkpoint['playlist_id'], original['playlist_id'])
                self.assertEqual(Path(summary['arquivo']).read_bytes(), original_bytes)
                self.assertEqual(len(self.providers.destinations[direction][original['playlist_id']]['ids']), 19)
                self.assertEqual(self.providers.destinations[direction][checkpoint['playlist_id']]['ids'], [self.providers.target_id(direction, other)])

    def test_equal_names_in_opposite_directions_remain_separate(self):
        self.run_migration('sp_yt')
        forward_summary, forward = self.record('sp_yt')
        self.run_migration('yt_sp')
        reverse_summary, reverse = self.record('yt_sp')
        self.assertNotEqual(forward_summary['arquivo'], reverse_summary['arquivo'])
        self.assertEqual(len(self.providers.destinations['sp_yt'][forward['playlist_id']]['ids']), 19)
        self.assertEqual(len(self.providers.destinations['yt_sp'][reverse['playlist_id']]['ids']), 19)
        self.assertEqual(len(self.core.listar_historico_migracoes()), 2)

    def test_liked_songs_from_different_google_accounts_use_separate_playlists_in_the_same_spotify_account(self):
        self.run_migration('yt_sp', source='LM')
        summary, original = self.record('yt_sp', source='LM')
        original_path = Path(summary['arquivo'])
        original_bytes = original_path.read_bytes()
        other = song(70, 'Curtida da outra conta')
        self.providers.accounts['sp_yt'] = 'UCAnotherGoogleSourceAccount'
        self.providers.liked_sources[self.providers.accounts['sp_yt']] = [other]
        self.providers.catalog.append(other)
        writes_before = len(self.providers.writes)
        self.run_migration('yt_sp', source='LM')
        records = [item for item in self.core.listar_historico_migracoes()
                   if item['direcao'] == 'yt_sp' and item['origem_id'] == 'LM']
        self.assertEqual(len(records), 2)
        new_summary = next(item for item in records if Path(item['arquivo']) != original_path)
        saved = json.loads(Path(new_summary['arquivo']).read_text(encoding='utf-8'))
        self.assertNotEqual(saved['playlist_id'], original['playlist_id'])
        self.assertEqual(saved['conta_origem'], 'UCAnotherGoogleSourceAccount')
        self.assertEqual(saved['conta_destino'], original['conta_destino'])
        self.assertEqual(saved['origem_input'], 'LM')
        self.assertEqual(new_summary['origem_input'], 'LM')
        self.assertEqual(original_path.read_bytes(), original_bytes)
        self.assertEqual(len(self.providers.destinations['yt_sp'][original['playlist_id']]['ids']), 19)
        self.assertEqual(self.providers.writes[writes_before:], [('yt_sp', saved['playlist_id'], [other['target_spotify_id']])])
        self.assertEqual(saved['status'], 'completed')

    def test_selected_liked_songs_history_from_another_google_account_preserves_its_checkpoint_without_mutations(self):
        self.run_migration('yt_sp', source='LM')
        summary, original = self.record('yt_sp', source='LM')
        path = Path(summary['arquivo'])
        before = path.read_bytes()
        counts = (len(self.providers.creates), len(self.providers.searches), len(self.providers.writes))
        self.providers.accounts['sp_yt'] = 'UCAnotherGoogleSourceAccount'
        self.providers.liked_sources[self.providers.accounts['sp_yt']] = list(self.providers.catalog)
        self.run_migration('yt_sp', source='LM', selected=path, success=False)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual((len(self.providers.creates), len(self.providers.searches), len(self.providers.writes)), counts)
        self.assertEqual(len(self.providers.destinations['yt_sp'][original['playlist_id']]['ids']), 19)
        self.assertEqual(self.record('yt_sp', source='LM')[0]['status'], 'completed')

    def test_same_google_liked_songs_account_appends_only_new_likes_to_the_existing_spotify_destination(self):
        self.run_migration('yt_sp', source='LM')
        summary, original = self.record('yt_sp', source='LM')
        self.assertEqual(original['conta_origem'], self.providers.accounts['sp_yt'])
        self.assertEqual(summary['origem_input'], 'LM')
        added = song(19, 'Nova curtida')
        self.providers.liked_sources[self.providers.accounts['sp_yt']].append(added)
        self.providers.catalog.append(added)
        searches_before = len(self.providers.searches)
        writes_before = len(self.providers.writes)
        self.run_migration('yt_sp', source='LM', name='Outro nome digitado')
        latest, checkpoint = self.record('yt_sp', source='LM')
        self.assertEqual(latest['arquivo'], summary['arquivo'])
        self.assertEqual(checkpoint['playlist_id'], original['playlist_id'])
        self.assertEqual(checkpoint['conta_origem'], original['conta_origem'])
        self.assertEqual(checkpoint['origem_input'], 'LM')
        self.assertEqual(checkpoint['mapeamento'][f'yt:{added["youtube_id"]}'], added['target_spotify_id'])
        self.assertEqual(latest['adicionadas'], 20)
        self.assertEqual(latest['status'], 'completed')
        self.assertEqual(len(self.providers.searches), searches_before + 1)
        self.assertIn(added['name'], self.providers.searches[-1][1])
        self.assertEqual(self.providers.writes[writes_before:], [('yt_sp', original['playlist_id'], [added['target_spotify_id']])])
        self.assertEqual(len(self.providers.creates), 1)
        self.assertEqual(self.providers.source_reads, [('yt_sp', 'LM', 0), ('yt_sp', 'LM', 0)])
        counts = (len(self.providers.creates), len(self.providers.searches), len(self.providers.writes))
        self.run_migration('yt_sp', source='LM')
        self.assertEqual((len(self.providers.creates), len(self.providers.searches), len(self.providers.writes)), counts)

    def test_android_real_liked_playlist_ids_remain_separate_without_channel_identity_and_keep_lm_history_inputs(self):
        account = self.providers.accounts['sp_yt']
        first_id = 'PLAndroidLikes000000000000000001'
        second_id = 'PLAndroidLikes000000000000000002'
        self.providers.liked_ids[account] = first_id
        self.providers.account_errors['sp_yt'] = RuntimeError('Canal não disponível neste cliente Android')
        self.run_migration('yt_sp', source='LM')
        summary, original = self.record('yt_sp', source=first_id)
        original_path = Path(summary['arquivo'])
        original_bytes = original_path.read_bytes()
        self.assertEqual(original['origem_id'], first_id)
        self.assertEqual(original['origem_input'], 'LM')
        self.assertEqual(summary['origem_input'], 'LM')
        other = song(70, 'Curtida da segunda origem')
        self.providers.liked_ids[account] = second_id
        self.providers.liked_sources[account] = [other]
        self.providers.catalog.append(other)
        self.run_migration('yt_sp', source='LM')
        latest, checkpoint = self.record('yt_sp', source=second_id)
        self.assertNotEqual(latest['arquivo'], summary['arquivo'])
        self.assertNotEqual(checkpoint['playlist_id'], original['playlist_id'])
        self.assertEqual(checkpoint['origem_input'], 'LM')
        self.assertEqual(latest['origem_input'], 'LM')
        self.assertEqual(original_path.read_bytes(), original_bytes)
        self.assertEqual(len(self.providers.destinations['yt_sp'][original['playlist_id']]['ids']), 19)
        self.assertEqual(self.providers.destinations['yt_sp'][checkpoint['playlist_id']]['ids'], [other['target_spotify_id']])
        self.assertEqual(len(self.providers.creates), 2)

    def test_stock_desktop_liked_songs_without_channel_id_export_and_explicitly_update_the_same_history_twice(self):
        self.providers.youtube_account_info = stock_youtube_account_info()
        self.providers.youtube_account_menu = youtube_account_menu()
        self.run_migration('yt_sp', source='LM')
        summary, original = self.record('yt_sp', source='LM')
        self.assertEqual(original['origem_id'], 'LM')
        self.assertIsNone(original.get('conta_origem'))
        self.assertTrue(original['origem_nao_verificada'])
        self.assertFalse(summary['legado'], 'an unbound modern history must remain selectable without legacy adoption')
        path = Path(summary['arquivo'])
        for index in (19, 20):
            added = song(index, 'Nova curtida desktop')
            self.providers.liked_sources[self.providers.accounts['sp_yt']].append(added)
            self.providers.catalog.append(added)
            before_searches = len(self.providers.searches)
            before_writes = len(self.providers.writes)
            self.run_migration('yt_sp', source='LM', selected=path)
            latest, checkpoint = self.record('yt_sp', source='LM')
            self.assertEqual(Path(latest['arquivo']), path)
            self.assertEqual(checkpoint['playlist_id'], original['playlist_id'])
            self.assertIsNone(checkpoint.get('conta_origem'))
            self.assertTrue(checkpoint['origem_nao_verificada'])
            self.assertEqual(latest['adicionadas'], index + 1)
            self.assertEqual(latest['status'], 'completed')
            self.assertFalse(latest['legado'])
            self.assertEqual(len(self.core.listar_historico_migracoes()), 1)
            self.assertEqual(len(self.providers.searches), before_searches + 1)
            self.assertEqual(self.providers.writes[before_writes:], [('yt_sp', original['playlist_id'], [added['target_spotify_id']])])
            self.assertIn(('yt_sp', original['playlist_id'], 'metadata', 0), self.providers.destination_reads)
        self.assertEqual(len(self.providers.creates), 1)
        self.assertEqual(len(self.providers.destinations['yt_sp'][original['playlist_id']]['ids']), 21)

    def test_only_the_active_account_own_channel_link_binds_likes_and_missing_known_identity_blocks_resume(self):
        own = 'UC' + 'A' * 22
        other = 'UC' + 'B' * 22
        self.providers.youtube_account_info = stock_youtube_account_info()
        self.providers.youtube_account_menu = youtube_account_menu(own_channel=own, other_channel=other)
        self.run_migration('yt_sp', source='LM')
        summary, checkpoint = self.record('yt_sp', source='LM')
        self.assertEqual(checkpoint['conta_origem'], own)
        self.assertTrue(self.providers.account_menu_reads)
        self.assertNotEqual(checkpoint['conta_origem'], other)
        path = Path(summary['arquivo'])
        before = path.read_bytes()
        counts = (len(self.providers.creates), len(self.providers.searches), len(self.providers.writes))
        # The user's display name/handle still match, and another account still
        # exposes a channel ID. Neither is proof of the active account's identity.
        self.providers.youtube_account_menu = youtube_account_menu(other_channel=other)
        self.run_migration('yt_sp', source='LM', success=False)
        self.run_migration('yt_sp', source='LM', selected=path, success=False)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual((len(self.providers.creates), len(self.providers.searches), len(self.providers.writes)), counts)
        self.assertEqual(self.record('yt_sp', source='LM')[0]['status'], 'completed')

    def test_unknown_stock_liked_source_is_never_automatically_merged_by_account_name_handle_or_destination_name(self):
        self.providers.youtube_account_info = stock_youtube_account_info()
        self.providers.youtube_account_menu = youtube_account_menu(other_channel='UC' + 'B' * 22)
        self.run_migration('yt_sp', source='LM')
        summary, original = self.record('yt_sp', source='LM')
        path = Path(summary['arquivo'])
        before = path.read_bytes()
        self.run_migration('yt_sp', source='LM')
        records = self.core.listar_historico_migracoes()
        self.assertEqual(len(records), 2)
        latest = next(item for item in records if Path(item['arquivo']) != path)
        checkpoint = json.loads(Path(latest['arquivo']).read_text(encoding='utf-8'))
        self.assertNotEqual(checkpoint['playlist_id'], original['playlist_id'])
        self.assertTrue(checkpoint['origem_nao_verificada'])
        self.assertIsNone(checkpoint.get('conta_origem'))
        self.assertFalse(latest['legado'])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(self.providers.creates), 2)
        self.assertEqual(len(self.providers.destinations['yt_sp'][original['playlist_id']]['ids']), 19)
        self.assertEqual(len(self.providers.destinations['yt_sp'][checkpoint['playlist_id']]['ids']), 19)

    def test_a_track_deleted_from_the_real_destination_is_searched_and_restored_once(self):
        for direction in DIRECTIONS:
            with self.subTest(direction=direction):
                self.run_migration(direction)
                summary, original = self.record(direction)
                missing = self.providers.catalog[4]
                missing_id = self.providers.target_id(direction, missing)
                ids = self.providers.destinations[direction][original['playlist_id']]['ids']
                ids.remove(missing_id)
                searches_before = len(self.providers.searches)
                writes_before = len(self.providers.writes)
                self.run_migration(direction)
                latest, checkpoint = self.record(direction)
                self.assertEqual(latest['arquivo'], summary['arquivo'])
                self.assertEqual(checkpoint['playlist_id'], original['playlist_id'])
                self.assertEqual(len(self.providers.searches), searches_before + 1)
                self.assertIn(missing['name'], self.providers.searches[-1][1])
                self.assertEqual(self.providers.writes[writes_before:], [(direction, original['playlist_id'], [missing_id])])
                self.assertEqual(len(ids), 19)
                self.assertEqual(ids.count(missing_id), 1)
                self.assertEqual(latest['adicionadas'], 19)
                self.assertEqual(latest['status'], 'completed')

    def test_selected_corrupt_or_invalid_checkpoint_is_never_overwritten_or_replaced(self):
        for direction in DIRECTIONS:
            for content in ('{broken json', json.dumps({'playlist_id': [], 'adicionadas': 'invalid'})):
                with self.subTest(direction=direction, content=content):
                    prefix = 'progresso_' if direction == 'sp_yt' else 'progresso_yt-sp_'
                    path = self.data_dir / f'{prefix}danificado.json'
                    path.write_text(content, encoding='utf-8')
                    before = path.read_bytes()
                    self.run_migration(direction, selected=path, success=False)
                    self.assertEqual(path.read_bytes(), before)
                    self.assertEqual(self.providers.creates, [])
                    self.assertEqual(self.providers.searches, [])
                    self.assertEqual(self.providers.writes, [])

    def test_selected_checkpoint_for_another_source_or_direction_is_protected(self):
        self.run_migration('sp_yt')
        summary, original = self.record('sp_yt')
        path = Path(summary['arquivo'])
        before = path.read_bytes()
        other_source = 'SRC0000000000000000002'
        self.providers.spotify_sources[other_source] = [song(80, 'Outra origem')]
        self.providers.catalog.append(self.providers.spotify_sources[other_source][0])
        counts = (len(self.providers.creates), len(self.providers.searches), len(self.providers.writes))
        self.run_migration('sp_yt', source=self.source_url('sp_yt', source=other_source), selected=path, success=False)
        self.run_migration('yt_sp', selected=path, success=False)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual((len(self.providers.creates), len(self.providers.searches), len(self.providers.writes)), counts)
        self.assertEqual(len(self.providers.destinations['sp_yt'][original['playlist_id']]['ids']), 19)

    def test_a_selected_checkpoint_bound_to_another_destination_account_cannot_create_or_write(self):
        for direction in DIRECTIONS:
            with self.subTest(direction=direction):
                self.run_migration(direction)
                summary, _ = self.record(direction)
                path = Path(summary['arquivo'])
                before = path.read_bytes()
                counts = (len(self.providers.creates), len(self.providers.searches), len(self.providers.writes))
                self.providers.accounts[direction] = 'AnotherDestinationAccount'
                self.run_migration(direction, selected=path, success=False)
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual((len(self.providers.creates), len(self.providers.searches), len(self.providers.writes)), counts)

    def test_failure_to_identify_the_account_keeps_its_completed_checkpoint_and_never_creates_another_destination(self):
        for direction in DIRECTIONS:
            with self.subTest(direction=direction):
                self.run_migration(direction)
                summary, _ = self.record(direction)
                path = Path(summary['arquivo'])
                before = path.read_bytes()
                counts = (len(self.providers.creates), len(self.providers.searches), len(self.providers.writes))
                self.providers.account_error = RuntimeError('Falha temporária ao identificar a conta')
                try:
                    self.run_migration(direction, success=False)
                finally:
                    self.providers.account_error = None
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual((len(self.providers.creates), len(self.providers.searches), len(self.providers.writes)), counts)
                self.assertEqual(self.record(direction)[0]['status'], 'completed')

    def test_failed_destination_reads_preserve_completed_progress_without_search_writes_or_replacement(self):
        for direction in DIRECTIONS:
            with self.subTest(direction=direction):
                self.run_migration(direction)
                summary, _ = self.record(direction)
                path = Path(summary['arquivo'])
                before = path.read_bytes()
                counts = (len(self.providers.creates), len(self.providers.searches), len(self.providers.writes))
                for message in ('Timeout ao ler a playlist', 'Playlist não encontrada ou inacessível'):
                    with self.subTest(message=message):
                        self.providers.read_error = RuntimeError(message)
                        try:
                            self.run_migration(direction, success=False)
                        finally:
                            self.providers.read_error = None
                        self.assertEqual(path.read_bytes(), before)
                        self.assertEqual((len(self.providers.creates), len(self.providers.searches), len(self.providers.writes)), counts)
                        self.assertEqual(self.record(direction)[0]['status'], 'completed')

    def test_normal_start_in_another_known_account_creates_an_independently_bound_playlist(self):
        for direction in DIRECTIONS:
            with self.subTest(direction=direction):
                self.run_migration(direction)
                summary, original = self.record(direction)
                old_path = Path(summary['arquivo'])
                old_bytes = old_path.read_bytes()
                self.providers.accounts[direction] = 'AnotherDestinationAccount'
                self.run_migration(direction)
                matching = [item for item in self.core.listar_historico_migracoes()
                            if item['direcao'] == direction and item['origem_id'] == self.source_id(direction)]
                self.assertEqual(len(matching), 2)
                new_record = next(item for item in matching if Path(item['arquivo']) != old_path)
                checkpoint = json.loads(Path(new_record['arquivo']).read_text(encoding='utf-8'))
                self.assertNotEqual(checkpoint['playlist_id'], original['playlist_id'])
                self.assertEqual(checkpoint['conta_destino'], 'AnotherDestinationAccount')
                self.assertEqual(checkpoint['status'], 'completed')
                self.assertEqual(old_path.read_bytes(), old_bytes)
                self.assertEqual(sum(found == direction for found, _, _ in self.providers.creates), 2)
                self.assertEqual(len(self.providers.destinations[direction][original['playlist_id']]['ids']), 19)
                self.assertEqual(len(self.providers.destinations[direction][checkpoint['playlist_id']]['ids']), 19)

    def alias_source(self, direction):
        first = self.providers.catalog[0]
        alias = dict(first, spotify_id=f'Source{99:016d}', youtube_id='Video000099')
        mapping = self.providers.spotify_sources if direction == 'sp_yt' else self.providers.youtube_sources
        mapping[self.source_id(direction)] = [first, alias]
        keys = [f'sp:{item["spotify_id"]}' if direction == 'sp_yt' else f'yt:{item["youtube_id"]}'
                for item in (first, alias)]
        return first, alias, keys

    def test_a_new_source_alias_of_a_confirmed_destination_track_maps_without_a_duplicate_post(self):
        for direction in DIRECTIONS:
            with self.subTest(direction=direction):
                first, alias, keys = self.alias_source(direction)
                mapping = self.providers.spotify_sources if direction == 'sp_yt' else self.providers.youtube_sources
                mapping[self.source_id(direction)] = [first]
                self.run_migration(direction)
                summary, original = self.record(direction)
                writes_before = len(self.providers.writes)
                mapping[self.source_id(direction)].append(alias)
                self.run_migration(direction)
                latest, checkpoint = self.record(direction)
                self.assertEqual(latest['arquivo'], summary['arquivo'])
                self.assertEqual(checkpoint['playlist_id'], original['playlist_id'])
                expected_id = self.providers.target_id(direction, first)
                self.assertEqual(checkpoint['mapeamento'], dict.fromkeys(keys, expected_id))
                self.assertEqual(latest['adicionadas'], 2)
                self.assertEqual(len(self.providers.writes), writes_before)
                self.assertEqual(self.providers.destinations[direction][original['playlist_id']]['ids'], [expected_id])

    def test_two_new_source_aliases_write_once_and_are_both_processed_only_after_destination_confirmation(self):
        for direction in DIRECTIONS:
            with self.subTest(direction=direction):
                first, _, keys = self.alias_source(direction)
                self.run_migration(direction)
                summary, checkpoint = self.record(direction)
                expected_id = self.providers.target_id(direction, first)
                self.assertEqual(checkpoint['mapeamento'], dict.fromkeys(keys, expected_id))
                self.assertEqual(summary['adicionadas'], 2)
                self.assertEqual(summary['status'], 'completed')
                self.assertEqual(self.providers.written_ids(direction), [expected_id])
                self.assertEqual(self.providers.destinations[direction][checkpoint['playlist_id']]['ids'], [expected_id])

    def test_unconfirmed_alias_writes_leave_both_source_keys_unprocessed_and_resumable(self):
        for direction in DIRECTIONS:
            with self.subTest(direction=direction):
                first, _, keys = self.alias_source(direction)
                self.providers.drop_writes = True
                self.run_migration(direction)
                summary, checkpoint = self.record(direction)
                self.assertEqual(checkpoint['mapeamento'], {})
                self.assertEqual(checkpoint['adicionadas'], [])
                self.assertEqual(summary['adicionadas'], 0)
                self.assertEqual(summary['status'], 'interrupted')
                self.assertEqual(self.providers.destinations[direction][checkpoint['playlist_id']]['ids'], [])
                self.providers.drop_writes = False
                self.run_migration(direction)
                latest, recovered = self.record(direction)
                expected_id = self.providers.target_id(direction, first)
                self.assertEqual(recovered['playlist_id'], checkpoint['playlist_id'])
                self.assertEqual(recovered['mapeamento'], dict.fromkeys(keys, expected_id))
                self.assertEqual(latest['status'], 'completed')
                self.assertEqual(self.providers.destinations[direction][checkpoint['playlist_id']]['ids'], [expected_id])

    def seed_legacy(self, direction):
        destination = self.providers.create(direction, 'Destino legado')
        first = self.providers.catalog[0]
        target = self.providers.target_id(direction, first)
        self.providers.destinations[direction][destination]['ids'].append(target)
        key = f'{first["name"]} - {ARTIST}' if direction == 'sp_yt' else f'yt:{first["youtube_id"]}'
        record = {'playlist_id': destination, 'adicionadas': [key]}
        record.update({'videos': [target]} if direction == 'sp_yt' else {'destino_ids': [target], 'puladas': []})
        prefix = 'progresso_' if direction == 'sp_yt' else 'progresso_yt-sp_'
        path = self.data_dir / f'{prefix}Destino_legado.json'
        path.write_text(json.dumps(record), encoding='utf-8')
        return path, record

    def test_legacy_checkpoint_requires_explicit_adoption_and_binds_the_typed_origin_without_repeating_old_tracks(self):
        for direction in DIRECTIONS:
            with self.subTest(direction=direction):
                path, original = self.seed_legacy(direction)
                original_bytes = path.read_bytes()
                self.run_migration(direction, name='Nome ignorado', selected=path)
                summary, checkpoint = self.record(direction)
                self.assertNotEqual(Path(summary['arquivo']), path)
                self.assertEqual(path.read_bytes(), original_bytes)
                self.assertEqual(checkpoint['playlist_id'], original['playlist_id'])
                self.assertEqual(checkpoint['origem_id'], self.source_id(direction))
                self.assertEqual(checkpoint['direcao'], direction)
                self.assertEqual(checkpoint['status'], 'completed')
                self.assertIn(self.providers.target_id(direction, self.providers.catalog[0]), checkpoint['mapeamento'].values())
                self.assertEqual(sum(item[0] == direction for item in self.providers.creates), 1)
                self.assertEqual(len([query for found, query in self.providers.searches if found == direction]), 18)
                old_target = self.providers.target_id(direction, self.providers.catalog[0])
                self.assertNotIn(old_target, self.providers.written_ids(direction))
                ids = self.providers.destinations[direction][original['playlist_id']]['ids']
                self.assertEqual(len(ids), 19)
                self.assertEqual(len(set(ids)), 19)
                counts = (len(self.providers.searches), len(self.providers.writes), len(self.providers.creates))
                self.run_migration(direction, name='Outro nome após vincular')
                self.assertEqual((len(self.providers.searches), len(self.providers.writes), len(self.providers.creates)), counts)

    def test_an_unbound_legacy_checkpoint_is_not_adopted_automatically_by_destination_name(self):
        for direction in DIRECTIONS:
            with self.subTest(direction=direction):
                path, original = self.seed_legacy(direction)
                before = path.read_bytes()
                self.run_migration(direction, name='Destino legado')
                summary, checkpoint = self.record(direction)
                self.assertNotEqual(Path(summary['arquivo']), path)
                self.assertNotEqual(checkpoint['playlist_id'], original['playlist_id'])
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(len(self.providers.destinations[direction][original['playlist_id']]['ids']), 1)
                self.assertEqual(len(self.providers.destinations[direction][checkpoint['playlist_id']]['ids']), 19)

    def test_legacy_without_a_usable_mapping_rechecks_existing_tracks_without_duplicate_writes(self):
        for direction in DIRECTIONS:
            with self.subTest(direction=direction):
                path, original = self.seed_legacy(direction)
                original['adicionadas'] = ['older-unmapped-source-key']
                original['videos' if direction == 'sp_yt' else 'destino_ids'] = []
                path.write_text(json.dumps(original), encoding='utf-8')
                before = path.read_bytes()
                self.run_migration(direction, selected=path)
                summary, checkpoint = self.record(direction)
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(checkpoint['playlist_id'], original['playlist_id'])
                self.assertEqual(summary['status'], 'completed')
                self.assertEqual(summary['adicionadas'], 19)
                self.assertEqual(len([query for found, query in self.providers.searches if found == direction]), 19)
                old_target = self.providers.target_id(direction, self.providers.catalog[0])
                self.assertNotIn(old_target, self.providers.written_ids(direction))
                ids = self.providers.destinations[direction][original['playlist_id']]['ids']
                self.assertEqual(len(ids), 19)
                self.assertEqual(len(set(ids)), 19)


if __name__ == '__main__':
    unittest.main()
