from collections import defaultdict
import configparser
from enum import Enum
from io import BytesIO
import json
import os
from pathlib import Path
import posixpath
import sys
import tarfile
import tempfile
import zipfile

import win_cli_launchers

class Platform(Enum):
    linux = 1
    osx = 2
    windows = 3

pkgdir = Path(__file__).parent

script_template = """\
#!{interpreter}
from {module} import {func}
if __name__ == '__main__':
    {func}()
"""

PREFIX = '/opt/anaconda1anaconda2anaconda3'

class CaseSensitiveContextParser(configparser.ConfigParser):
    optionxfrom = staticmethod(str)

class PackageBuilder:
    def __init__(self, unpacked_wheel, metadata, python_version, platform, bitness):
        self.unpacked_wheel = unpacked_wheel
        self.metadata = metadata
        self.python_version = python_version
        self.platform = platform
        self.bitness = bitness
        self.files = []
        self.has_prefix_files = []
        self.build_no = 0

    def _find_dist_info(self):
        for p in self.unpacked_wheel.iterdir():
            if p.is_dir() and p.name.endswith('.dist-info'):
                return p

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
        if self.platform is Platform.windows:
            return 'Lib/site-packages/'
        else:
            return 'lib/python{}/site-packages/'.format(self.python_version)

    def scripts_path(self):
        if self.platform is Platform.windows:
            return 'Scripts/'
        else:
            return 'bin/'

    def build(self, fileobj):
        with tarfile.open(fileobj=fileobj, mode='w:bz2') as tf:
            self.add_module(tf)
            self.create_scripts(tf)
            self.write_index(tf)
            self.write_has_prefix_list(tf)
            self.write_files_list(tf)

    def add_module(self, tf):
        site_packages = self.site_packages_path()
        for src in self.unpacked_wheel.iterdir():
            if src.name.endswith('.data'):
                self._add_data_dir(tf, src)
                continue

            dst = site_packages + src.name
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


    def _write_script_unix(self, tf, name, contents):
        ti = tarfile.TarInfo(self.scripts_path() + name)
        contents = contents.encode('utf-8')
        ti.size = len(contents)
        tf.addfile(ti, BytesIO(contents))
        self.record_file(ti.name, has_prefix=True)

    def _write_script_windows(self, tf, name, contents):
        self._write_script_unix(tf, name+'-script.py', contents)
        arch = 'x64' if self.bitness == '64' else 'x86'
        src = win_cli_launchers.find_exe(arch)
        dst = self.scripts_path() + name + '.exe'
        tf.add(src, arcname=dst)
        self.record_file(dst)

    def create_scripts(self, tf):
        ep_file = self._find_dist_info() / 'entry_points.txt'
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
            if self.platform == Platform.windows:
                self._write_script_windows(tf, name, s)
            else:
                self._write_script_unix(tf, name, s)

    def write_index(self, tf):
        py_version_nodot = self.python_version.replace('.', '')
        # TODO: identify dependencies, license
        ix = {
          "arch": "x86_64" if (self.bitness == '64') else 'x86',
          "build": "py{}_{}".format(py_version_nodot, self.build_no),
          "build_number": self.build_no,
          "depends": [
            "python {}*".format(self.python_version)
          ],
          "license": "UNKNOWN",
          "name": self.metadata['Name'][0],
          "platform": self.platform.name,
          "subdir": "{}-{}".format(self.platform.name, self.bitness),
          "version": self.metadata['Version'][0]
        }
        contents = json.dumps(ix, indent=2, sort_keys=True).encode('utf-8')
        ti = tarfile.TarInfo('info/index.json')
        ti.size = len(contents)
        tf.addfile(ti, BytesIO(contents))

    def write_has_prefix_list(self, tf):
        lines = [
            '{prefix} text {path}'.format(prefix=PREFIX, path=path)
            for path in self.has_prefix_files
        ]
        contents = '\n'.join(lines).encode('utf-8')
        ti = tarfile.TarInfo('info/has_prefix')
        ti.size = len(contents)
        tf.addfile(ti, BytesIO(contents))

    def write_files_list(self, tf):
        contents = '\n'.join(self.files).encode('utf-8')
        ti = tarfile.TarInfo('info/files')
        ti.size = len(contents)
        tf.addfile(ti, BytesIO(contents))

def unpack_wheel(whl_file):
    td = tempfile.TemporaryDirectory()
    with zipfile.ZipFile(whl_file) as zf:
        zf.extractall(td.name)
    return td

class BadWheelError(Exception):
    pass

def _read_metadata(path):
    res = defaultdict(list)
    with path.open() as f:
        for line in f:
            if not line.strip():
                break
            k, v = line.strip().split(':', 1)
            k = k.strip()
            v = v.strip()
            res[k].append(v)

    return dict(res)

def check_wheel_contents(unpacked_wheel):
    dist_info = None
    data_dir = None

    for x in unpacked_wheel.iterdir():
        if x.name.endswith('.dist-info'):
            if not x.is_dir():
                raise BadWheelError(".dist-info not a directory")
            if dist_info is not None:
                raise BadWheelError("Multiple .dist-info directories")
            dist_info = x

        if x.name.endswith('.data'):
            if not x.is_dir():
                raise BadWheelError(".data not a directory")
            elif data_dir is not None:
                raise BadWheelError("Multiple .data directories")
            data_dir = x

    if dist_info is None:
        raise BadWheelError("Didn't find .dist-info directory")

    wheel_metadata = _read_metadata(dist_info / 'WHEEL')
    if wheel_metadata['Wheel-Version'][0] != '1.0':
        raise BadWheelError("wheel2conda only knows about wheel format 1.0")
    if wheel_metadata['Root-Is-Purelib'][0].lower() != 'true':
        raise BadWheelError("Can't currently autoconvert packages with platlib")

    metadata = _read_metadata(dist_info / 'METADATA')
    for field in ('Name', 'Version'):
        if field not in metadata:
            raise BadWheelError("Missing required metadata field: %s" % field)

    return metadata
    

def main():
    td = unpack_wheel(sys.argv[1])
    unpacked_wheel = Path(td.name)
    metadata = check_wheel_contents(unpacked_wheel)
    pb = PackageBuilder(unpacked_wheel, metadata, '3.5', Platform.linux, '64')
    with open('test_pkg.tar.bz2', 'wb') as f:
        pb.build(f)
    td.cleanup()

if __name__ == '__main__':
    main()
