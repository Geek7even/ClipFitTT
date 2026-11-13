# SPDX-License-Identifier: GPL-2.0-only
import json
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_service
import install


class InstallerTests(unittest.TestCase):
    def test_workflow_uses_supplied_python_and_install_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / 'app with spaces'
            workflow = build_service.build(root / 'generated', app, sys.executable)
            with (workflow / 'Contents/Resources/document.wflow').open('rb') as handle:
                document = plistlib.load(handle)
            command = document['actions'][0]['action']['ActionParameters']['COMMAND_STRING']
            self.assertIn(str(Path(sys.executable).resolve()), command)
            self.assertIn(str(app / 'compress.py'), command)
            self.assertIn('"$@"', command)

    def make_stage(self, root):
        stage = root / 'stage'
        stage.mkdir()
        for name in install.MANAGED_FILES:
            (stage / name).write_text('new ' + name)
        (stage / 'runtime_config.json').write_text(json.dumps({}))
        (stage / 'bin').mkdir()
        (stage / 'bin/HandBrakeCLI').write_text('new CLI')
        workflow = build_service.build(stage / 'generated', root / 'app', sys.executable)
        return stage, workflow

    def test_managed_install_preserves_existing_logs_and_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage, workflow = self.make_stage(root)
            app, services = root / 'app', root / 'services'
            (app / 'work').mkdir(parents=True)
            (app / 'work/keep.mp4').write_bytes(b'recoverable')
            install.commit_install(stage, workflow, app, services)
            self.assertEqual((app / 'compress.py').read_text(), 'new compress.py')
            self.assertEqual((app / 'work/keep.mp4').read_bytes(), b'recoverable')
            self.assertTrue((services / workflow.name).is_dir())

    def test_partial_install_failure_restores_prior_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage, workflow = self.make_stage(root)
            app, services = root / 'app', root / 'services'
            app.mkdir()
            for name in install.MANAGED_FILES:
                (app / name).write_text('old ' + name)
            real_replace = install.os.replace
            def fail_on_preset(source, target):
                if Path(target) == app / 'preset.json':
                    raise OSError('simulated installation failure')
                return real_replace(source, target)
            with mock.patch.object(install.os, 'replace', side_effect=fail_on_preset):
                with self.assertRaises(OSError):
                    install.commit_install(stage, workflow, app, services)
            for name in install.MANAGED_FILES:
                self.assertEqual((app / name).read_text(), 'old ' + name)
            self.assertFalse((services / workflow.name).exists())

    def test_existing_newer_cli_is_selected_without_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'bin').mkdir()
            binary = root / 'bin/HandBrakeCLI'
            binary.write_bytes(b'local CLI')
            binary.chmod(0o755)
            with mock.patch.object(install.cli_update, 'binary_version', return_value='1.12.0'), mock.patch.object(install.cli_update, 'validate_candidate'):
                self.assertEqual(install.choose_cli(None, root), binary.resolve())

    def test_service_install_failure_restores_previous_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage, workflow = self.make_stage(root)
            app, services = root / 'app', root / 'services'
            app.mkdir()
            (app / 'compress.py').write_text('old code')
            old = services / workflow.name
            old.mkdir(parents=True)
            (old / 'keep.txt').write_text('old workflow')
            real_replace = install.os.replace
            def fail_on_service(source, target):
                if Path(target) == old and '.service-install-' in str(source):
                    raise OSError('service registration stage failure')
                return real_replace(source, target)
            with mock.patch.object(install.os, 'replace', side_effect=fail_on_service):
                with self.assertRaises(OSError):
                    install.commit_install(stage, workflow, app, services)
            self.assertEqual((app / 'compress.py').read_text(), 'old code')
            self.assertEqual((old / 'keep.txt').read_text(), 'old workflow')

    def test_explicit_invalid_cli_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):
                install.choose_cli(str(Path(directory) / 'missing'), Path(directory))


if __name__ == '__main__':
    unittest.main()
