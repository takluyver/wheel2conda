"""Convert pure-Python wheels to conda packages"""

import argparse
import base64
import configparser
import csv
from enum import Enum
import hashlib
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import posixpath
import tarfile

import win_cli_launchers

from .requirements import requires_dist_to_conda_requirements
from .wheel import WheelContents

__version__ = '0.2'

class Platform(Enum):
    linux = 1
    osx = 2
    win = 3

script_template = """\
#!{interpreter}
from {module} import {func}
if __name__ == '__main__':
    {func}()
"""

PREFIX = '/opt/anaconda1anaconda2anaconda3'

PYTHON_VERSIONS = ['3.6', '3.5', '3.4', '2.7']
PLATFORM_PAIRS = [
    (Platform.linux, '64'),
    (Platform.linux, '32'),
    (Platform.osx, '64'),
    (Platform.win, '64'),
    (Platform.win, '32'),
]

class CaseSensitiveContextParser(configparser.ConfigParser):
    optionxfrom = staticmethod(str)

def _add_to_tarball(tf, arcname, contents):
    ti = tarfile.TarInfo(arcname)
    ti.size = len(contents)
    tf.addfile(ti, BytesIO(contents))

_license_classifiers = {
    'License :: OSI Approved :: MIT License': 'MIT',
    'License :: OSI Approved :: BSD License': 'BSD',
    'License :: OSI Approved :: Apache Software License': 'Apache',
    'License :: OSI Approved :: GNU General Public License (GPL)': 'GPL',
    'License :: OSI Approved :: GNU General Public License v2 (GPLv2)': 'GPLv2',
    'License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)': 'GPLv2+',
    'License :: OSI Approved :: GNU General Public License v3 (GPLv3)': 'GPLv3',
    'License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)': 'GPLv3+',
    'License :: OSI Approved :: GNU Lesser General Public License v2 (LGPLv2)': 'LGPLv2',
    'License :: OSI Approved :: GNU Lesser General Public License v2 or later (LGPLv2+)': 'LGPLv2+',
    'License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)': 'LGPLGv3',
    'License :: OSI Approved :: GNU Lesser General Public License v3 or later (LGPLv3+)': 'LGPLv3+',
    'License :: OSI Approved :: GNU Library or Lesser General Public License (LGPL)': 'LGPL',
}

def identify_license(metadata):
    if ('License' in metadata) and (metadata['License'][0].lower() != 'unknown'):
        return metadata['License'][0]
    for clf in metadata.get('Classifier', []):
        if clf in _license_classifiers:
            return _license_classifiers[clf]

    return 'UNKNOWN'

class PackageBuilder:
    def __init__(self, wheel_contents, python_version, platform, bitness):
        self.wheel_contents = wheel_contents
        self.python_version = python_version
        self.platform = platform
        self.bitness = bitness
        self.files = []
        self.has_prefix_files = []
        self.py_record_extra = []
        self.build_no = 0

    def record_file(self, arcname, has_prefix=False):
        self.files.append(arcname)
        if has_prefix:
            self.has_prefix_files.append(arcname)

    def record_file_or_dir(self, arcname, src):
        # We're assuming that it will be either a directory or a regular file
        if src.is_dir():
            d = str(src)
            for dirpath, dirnames, filenames in os.walk(d):
                rel_dirpath = os.path.relpath(dirpath, start=d)
                if os.sep == '\\':
                    rel_dirpath = rel_dirpath.replace('\\', '/')
                for f in filenames:
                    p = posixpath.join(arcname, rel_dirpath, f)
                    self.record_file(posixpath.normpath(p))

        else:
            self.record_file(arcname)

    def site_packages_path(self):
        if self.platform is Platform.win:
            return 'Lib/site-packages/'
        else:
            return 'lib/python{}/site-packages/'.format(self.python_version)

    def scripts_path(self):
        if self.platform is Platform.win:
            return 'Scripts/'
        else:
            return 'bin/'

    def build(self, fileobj):
        with tarfile.open(fileobj=fileobj, mode='w:bz2') as tf:
            self.add_module(tf)
            self.create_scripts(tf)
            self.write_pep376_record(tf)
            self.write_index(tf)
            self.write_has_prefix_list(tf)
            self.write_files_list(tf)


    def add_module(self, tf):
        site_packages = self.site_packages_path()
        for src in self.wheel_contents.unpacked.iterdir():
            if src.name.endswith('.data'):
                self._add_data_dir(tf, src)
                continue

            dst = site_packages + src.name
            if src.name.endswith('.dist-info'):
                # Skip RECORD for now, we'll add it later, with rows for scripts
                def exclude_record(ti):
                    return None if ti.name.endswith('RECORD') else ti
                tf.add(str(src), arcname=dst, filter=exclude_record)
                self.record_file_or_dir(dst, src)
                continue

            # Actual module/package file/directory
            tf.add(str(src), arcname=dst)
            self.record_file_or_dir(dst, src)

    def _add_data_dir(self, tf, src):
        for d in src.iterdir():
            if d.name == 'data':
                for f in d.iterdir():
                    tf.add(str(f), arcname=f.name)
                    self.record_file_or_dir(f.name, f)

            else:
                raise NotImplementedError('%s under data dir' % d.name)

    def _py_record_file(self, relpath, contents):
        h = hashlib.sha256(contents)
        digest = base64.urlsafe_b64encode(h.digest()).decode('ascii').rstrip('=')
        self.py_record_extra.append((relpath, 'sha256='+digest, len(contents)))

    def write_pep376_record(self, tf):
        sio = StringIO()
        installed_record = csv.writer(sio)
        if self.platform is Platform.win:
            prefix_from_site_pkgs = '../..'
        else:
            prefix_from_site_pkgs = '../../..'

        with (self.wheel_contents.find_dist_info() / 'RECORD').open() as f:
            wheel_record = csv.reader(f)
            for row in wheel_record:
                path_parts = row[0].split('/')
                if len(path_parts) > 2 \
                        and path_parts[0].endswith('.data') \
                        and path_parts[1] == 'data':
                    row[0] = posixpath.join(prefix_from_site_pkgs, *path_parts[2:])
                installed_record.writerow(row)

        for row in self.py_record_extra:
            path = posixpath.join(prefix_from_site_pkgs, row[0])
            installed_record.writerow((path,) + row[1:])

        record_path = self.site_packages_path() \
                      + self.wheel_contents.find_dist_info().name + '/RECORD'
        _add_to_tarball(tf, record_path, sio.getvalue().encode('utf-8'))
        # The RECORD file was already recorded for conda's files list when the
        # rest of .dist-info was added.

    def _write_script_unix(self, tf, name, contents):
        path = self.scripts_path() + name
        contents = contents.encode('utf-8')
        _add_to_tarball(tf, path, contents)
        self.record_file(path, has_prefix=True)
        self._py_record_file(path, contents)

    def _write_script_windows(self, tf, name, contents):
        self._write_script_unix(tf, name+'-script.py', contents)
        arch = 'x64' if self.bitness == '64' else 'x86'
        src = win_cli_launchers.find_exe(arch)
        dst = self.scripts_path() + name + '.exe'
        tf.add(src, arcname=dst)
        self.record_file(dst)
        with open(src, 'rb') as f:
            self._py_record_file(dst, f.read())

    def create_scripts(self, tf):
        ep_file = self.wheel_contents.find_dist_info() / 'entry_points.txt'
        if not ep_file.is_file():
            return

        cp = CaseSensitiveContextParser()
        cp.read([str(ep_file)])
        for name, ep in cp['console_scripts'].items():
            if ep.count(':') != 1:
                raise ValueError("Bad entry point: %r" % ep)
            mod, func = ep.split(':')
            s = script_template.format(
                module=mod, func=func,
                # This is replaced when the package is installed:
                interpreter=PREFIX+'/bin/python',
            )
            if self.platform == Platform.win:
                self._write_script_windows(tf, name, s)
            else:
                self._write_script_unix(tf, name, s)

    def write_index(self, tf):
        py_version_nodot = self.python_version.replace('.', '')
        reqs = self.wheel_contents.metadata.get('Requires-Dist', [])
        conda_reqs = requires_dist_to_conda_requirements(reqs,
                                 python_version=self.python_version,
                                 platform=self.platform.name,
                                 bitness=self.bitness,
        )
        ix = {
          "arch": "x86_64" if (self.bitness == '64') else 'x86',
          "build": "py{}_{}".format(py_version_nodot, self.build_no),
          "build_number": self.build_no,
          "depends": [
            "python {}*".format(self.python_version)
          ] + conda_reqs,
          "license": identify_license(self.wheel_contents.metadata),
          "name": self.wheel_contents.metadata['Name'][0],
          "platform": self.platform.name,
          "subdir": "{}-{}".format(self.platform.name, self.bitness),
          "version": self.wheel_contents.metadata['Version'][0]
        }
        contents = json.dumps(ix, indent=2, sort_keys=True).encode('utf-8')
        _add_to_tarball(tf, 'info/index.json', contents)

    def write_has_prefix_list(self, tf):
        lines = [
            '{prefix} text {path}'.format(prefix=PREFIX, path=path)
            for path in self.has_prefix_files
        ]
        contents = '\n'.join(lines).encode('utf-8')
        _add_to_tarball(tf, 'info/has_prefix', contents)

    def write_files_list(self, tf):
        contents = '\n'.join(self.files).encode('utf-8')
        _add_to_tarball(tf, 'info/files', contents)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('wheel_file')
    args = ap.parse_args(argv)

    wc = WheelContents(args.wheel_file)
    wc.check()

    for platform, bitness in PLATFORM_PAIRS:
        d = Path(platform.name + '-' + bitness)
        try:
            d.mkdir()
        except FileExistsError:
            pass

        for py_version in wc.filter_compatible_pythons(PYTHON_VERSIONS):
            print('Converting for: {}-{},'.format(platform.name, bitness),
                  'Python', py_version)
            pb = PackageBuilder(wc, py_version, platform, bitness)
            filename = '{name}-{version}-py{xy}_0.tar.bz2'.format(
                name = wc.metadata['Name'][0],
                version = wc.metadata['Version'][0],
                xy = py_version.replace('.', ''),
            )
            with (d / filename).open('wb') as f:
                pb.build(f)
    wc.td.cleanup()
