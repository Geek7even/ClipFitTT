# SPDX-License-Identifier: GPL-2.0-only
"""Test workflow metadata and staged-install rollback without real installs."""
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
    """Exercise generated service paths and managed-file replacement."""

    def test_beta_workflow_retains_full_version_with_numeric_apple_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'VERSION').write_text('0.9.1-beta.1\n')
            with mock.patch.object(build_service, 'ROOT', root):
                workflow = build_service.build(root / 'generated', root / 'app', sys.executable)
            with (workflow / 'Contents/Info.plist').open('rb') as handle:
                info = plistlib.load(handle)
            self.assertEqual(info['CFBundleShortVersionString'], '0.9.1')
            self.assertEqual(info['ClipFitTTVersion'], '0.9.1-beta.1')
            self.assertEqual(info['ClipFitTTDisplayVersion'], 'ClipFitTT Beta 0.9.1')

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
        """Return a fake package and workflow with distinguishable contents."""
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
                # Fail after earlier files have changed to exercise rollback.
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
                # Fail the new service move, not restoration of the old one.
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

    def test_native_bridge_replacement_rolls_back_with_service_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage, workflow = self.make_stage(root)
            library = install.app_logging.BRIDGE_NAME
            (stage / 'bin' / library).write_bytes(b'new bridge')
            app, services = root / 'app', root / 'services'
            (app / 'bin').mkdir(parents=True)
            (app / 'bin' / library).write_bytes(b'old bridge')
            replace = install.os.replace

            def fail_workflow(source, target):
                if Path(target) == services / workflow.name:
                    raise OSError('service install failed')
                return replace(source, target)

            with mock.patch.object(install.os, 'replace', side_effect=fail_workflow):
                with self.assertRaises(OSError):
                    install.commit_install(stage, workflow, app, services)
            self.assertEqual((app / 'bin' / library).read_bytes(), b'old bridge')


if __name__ == '__main__':
    unittest.main()
