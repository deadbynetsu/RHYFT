import hashlib
from pathlib import Path
import tempfile
import unittest
import yaml
from ferramentas.publish_release import BINARIES, validate_assets, validate_version

ROOT = Path(__file__).resolve().parents[1]


class ReleaseSafetyTests(unittest.TestCase):
    def test_old_releases_rejected(self):
        for version in ('1.4.0', '1.3.1', '1.4.999', '1.5.0-rc1', 'latest'):
            with self.assertRaises(ValueError):
                validate_version(version)
        validate_version('1.5.0')

    def test_both_platforms_and_matching_hashes_required(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in BINARIES:
                data = name.encode()
                (root / name).write_bytes(data)
                (root / (name + '.sha256')).write_text(hashlib.sha256(data).hexdigest())
            self.assertEqual(len(validate_assets(root)), 6)
            (root / 'RHYFT-android.apk').write_bytes(b'changed')
            with self.assertRaises(ValueError):
                validate_assets(root)

    def test_publisher_needs_all_successful_builds(self):
        # BaseLoader preserves YAML 1.2's "on" key (PyYAML defaults to 1.1).
        workflow = yaml.load((ROOT / '.github/workflows/release.yml').read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
        self.assertEqual(set(workflow['jobs']['publish']['needs']), {'tests', 'windows', 'android'})
        self.assertNotIn('always()', workflow['jobs']['publish']['if'])
        for name in ('build.yml', 'build-android.yml'):
            text = (ROOT / '.github/workflows' / name).read_text(encoding='utf-8')
            self.assertNotIn('gh release', text)
            self.assertNotIn('--clobber', text)
            self.assertEqual(yaml.load(text, Loader=yaml.BaseLoader)['permissions']['contents'], 'read')
