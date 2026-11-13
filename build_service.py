#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Generate a user-specific Finder service at installation time."""
from pathlib import Path
import plistlib
import shlex
import sys
import uuid

ROOT = Path(__file__).resolve().parent
NAME = '压缩视频至100MB（H.264）'
DEFAULT_APP_DIR = Path.home() / 'Library/Application Support/VideoCompressor'


def build(output_dir, app_dir=DEFAULT_APP_DIR, python_executable=None):
    python = str(Path(python_executable or sys.executable).resolve())
    app_dir = Path(app_dir).expanduser().absolute()
    workflow = Path(output_dir) / (NAME + '.workflow')
    contents = workflow / 'Contents'
    (contents / 'Resources').mkdir(parents=True, exist_ok=True)
    script = 'exec ' + shlex.join([python, str(app_dir / 'compress.py'), '--launch']) + ' "$@"\n'
    action = {
        'ActionBundlePath': '/System/Library/Automator/Run Shell Script.action',
        'ActionName': 'Run Shell Script',
        'ActionParameters': {'COMMAND_STRING': script, 'CheckedForUserDefaultShell': True,
                             'inputMethod': 1, 'shell': '/bin/zsh', 'source': ''},
        'AMAccepts': {'Container': 'List', 'Optional': True, 'Types': ['com.apple.cocoa.string']},
        'AMProvides': {'Container': 'List', 'Types': ['com.apple.cocoa.string']},
        'AMActionVersion': '2.0.3', 'AMParameterProperties': {}, 'AMRequiredResources': [],
        'Application': ['Automator'], 'BundleIdentifier': 'com.apple.RunShellScript',
        'CFBundleVersion': '2.0.3', 'Class Name': 'RunShellScriptAction',
        'CanShowSelectedItemsWhenRun': False, 'CanShowWhenRun': True,
        'Category': ['AMCategoryUtilities'], 'ShowWhenRun': False,
        'InputUUID': str(uuid.uuid4()), 'OutputUUID': str(uuid.uuid4()),
        'UUID': str(uuid.uuid4()), 'isViewVisible': True,
    }
    info = {
        'CFBundleIdentifier': 'local.videocompressor.finder100mb',
        'CFBundleName': NAME,
        'CFBundleShortVersionString': (ROOT / 'VERSION').read_text().strip(),
        'NSServices': [{'NSMenuItem': {'default': NAME}, 'NSMessage': 'runWorkflowAsService',
                        'NSRequiredContext': {'NSApplicationIdentifier': 'com.apple.finder'},
                        'NSSendFileTypes': ['public.mpeg-4', 'com.apple.quicktime-movie']}],
    }
    document = {
        'AMApplicationBuild': '523', 'AMApplicationVersion': '2.10', 'AMDocumentVersion': '2',
        'actions': [{'action': action, 'isViewVisible': True}], 'connectors': {},
        'workflowMetaData': {
            'serviceApplicationBundleID': 'com.apple.finder',
            'serviceApplicationPath': '/System/Library/CoreServices/Finder.app',
            'serviceInputTypeIdentifier': 'com.apple.Automator.fileSystemObject.movie',
            'serviceOutputTypeIdentifier': 'com.apple.Automator.nothing',
            'serviceProcessesInput': 0,
            'workflowTypeIdentifier': 'com.apple.Automator.servicesMenu',
            'useAutomaticInputType': False,
        },
    }
    for path, data in ((contents / 'Info.plist', info),
                       (contents / 'Resources/document.wflow', document)):
        with path.open('wb') as handle:
            plistlib.dump(data, handle)
    return workflow


if __name__ == '__main__':
    print(build(ROOT / '.build'))
