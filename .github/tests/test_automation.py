import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


updater = load('update-sources')
selector = load('select-packages')
RULE = {'repository': 'gAlleb/omtalk', 'tag_prefix': 'v', 'cargo_manifest': 'crates/tui/Cargo.toml'}
DISTFILES = 'distfiles="https://github.com/gAlleb/omtalk/archive/refs/tags/v${version}.tar.gz"\n' 


class Tags(unittest.TestCase):
    def tags(self, *names):
        return '\n'.join('a' * 40 + '\trefs/tags/' + name for name in names)

    def test_numeric_stable_and_annotated_tags(self):
        self.assertEqual(updater.latest_tag(self.tags('v0.1.9', 'v0.1.11', 'v0.1.11^{}', 'v0.2.0-rc1', 'bad')), '0.1.11')
        with self.assertRaises(ValueError):
            updater.latest_tag(self.tags('v2.0.0-beta1'))

    def test_update_and_same_version_checksum_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / 'omtalk' / 'template'
            template.parent.mkdir()
            original = 'version=0.1.9\nrevision=3\nchecksum=' + 'a' * 64 + '\nhostmakedepends="cmake"\n'
            original += DISTFILES
            template.write_text(original)
            with patch.object(updater, 'source_checksum', return_value='b' * 64) as checksum:
                self.assertTrue(updater.update(template, self.tags('v0.1.11'), RULE))
                updated = template.read_text()
                self.assertIn('version=0.1.11\nrevision=1\n', updated)
                self.assertIn('checksum=' + 'b' * 64, updated)
                self.assertIn('hostmakedepends="cmake"', updated)
                self.assertFalse(updater.update(template, self.tags('v0.1.11'), RULE))
                self.assertFalse(updater.update(template, self.tags('v0.1.8'), RULE))
                self.assertEqual(template.read_text(), updated)
                checksum.assert_called_once_with('0.1.11', 'omtalk', RULE)

    def test_failed_download_does_not_modify_template(self):
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / 'omtalk' / 'template'
            template.parent.mkdir()
            original = 'version=0.1.9\nrevision=1\nchecksum=' + 'a' * 64 + '\n'
            original += DISTFILES
            template.write_text(original)
            with patch.object(updater, 'source_checksum', side_effect=ValueError('invalid source')):
                with self.assertRaises(ValueError):
                    updater.update(template, self.tags('v0.1.11'), RULE)
            self.assertEqual(template.read_text(), original)

    def archive(self, version):
        result = io.BytesIO()
        with tarfile.open(fileobj=result, mode='w:gz') as tar:
            files = {
                'Cargo.toml': '[workspace.package]\nversion="' + version + '"\n',
                'crates/tui/Cargo.toml': '[package]\nname="omtalk"\nversion.workspace=true\n',
                'Cargo.lock': '[[package]]\nname="omtalk"\nversion="' + version + '"\n',
            }
            for name, content in files.items():
                member = tarfile.TarInfo('omtalk-0.2.0/' + name)
                data = content.encode()
                member.size = len(data)
                tar.addfile(member, io.BytesIO(data))
        result.seek(0)
        return result

    def test_source_version_matches_tag(self):
        updater.validate_archive(self.archive('0.2.0'), '0.2.0', 'omtalk', RULE)
        with self.assertRaises(ValueError):
            updater.validate_archive(self.archive('0.1.9'), '0.2.0', 'omtalk', RULE)


class Selection(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.old = Path.cwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, self.old)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.org')
        for name in ('omtalk', 'Waybar', 'wal-telegram'):
            path = Path('srcpkgs') / name / 'template'
            path.parent.mkdir(parents=True)
            path.write_text('version=1\n')
        Path('srcpkgs/omtalk/update.json').write_text('{}')
        self.commit()
        self.base = self.git('rev-parse', 'HEAD').strip()
        self.available = selector.available_packages()

    def git(self, *args):
        return subprocess.check_output(['git', *args], text=True, stderr=subprocess.DEVNULL)

    def commit(self):
        self.git('add', '-A')
        self.git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'test')

    def test_entire_push_not_only_last_commit(self):
        Path('srcpkgs/Waybar/template').write_text('version=2\n')
        self.commit()
        Path('srcpkgs/wal-telegram/files').mkdir()
        Path('srcpkgs/wal-telegram/files/run').write_text('example')
        self.commit()
        self.assertEqual(selector.changed_packages(self.base, self.available), ['Waybar', 'wal-telegram'])

    def test_removed_patch_rebuilds_package(self):
        path = Path('srcpkgs/Waybar/patch')
        path.write_text('patch')
        self.commit()
        before = self.git('rev-parse', 'HEAD').strip()
        path.unlink()
        self.commit()
        self.assertEqual(selector.changed_packages(before, self.available), ['Waybar'])

    def test_ci_change_smoke_build_and_new_branch(self):
        Path('.github').mkdir()
        Path('.github/script.py').write_text('pass')
        self.commit()
        self.assertEqual(selector.changed_packages(self.base, self.available), ['omtalk', 'wal-telegram'])
        self.assertEqual(selector.changed_packages('0' * 40, self.available), sorted(self.available))

    def test_manual_selection_validated(self):
        self.assertEqual(selector.select('workflow_dispatch', {}, 'all', self.available), sorted(self.available))
        self.assertEqual(selector.select('workflow_dispatch', {}, 'Waybar', self.available, ['omtalk']), ['Waybar', 'omtalk'])
        for invalid in ('', '../bad', 'missing', 'omtalk;echo'):
            with self.assertRaises(ValueError):
                selector.select('workflow_dispatch', {}, invalid, self.available)

    def test_schedule_skips_published_and_retries_missing_package(self):
        with patch.dict(os.environ, GITHUB_REPOSITORY='gAlleb/omvoid-repo'):
            with patch.object(selector, 'is_published', return_value=True):
                self.assertEqual(selector.select('schedule', {}, '', self.available), [])
            with patch.object(selector, 'is_published', return_value=False):
                self.assertEqual(selector.select('schedule', {}, '', self.available), ['omtalk'])
