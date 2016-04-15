from enum import Enum
from io import BytesIO
from pathlib import Path
import sys
import tarfile
import tempfile
import zipfile

class Platform(Enum):
    linux = 1
    osx = 2
    windows = 3

pkgdir = Path(__file__).parent

class PackageBuilder:
    def __init__(self, unpacked_wheel, python_version, platform, bitness):
        self.unpacked_wheel = unpacked_wheel
        self.python_version = python_version
        self.platform = platform
        self.bitness = bitness
        self.files = []
        self.has_prefix_files = []

    def record_file(self, arcname, has_prefix=False):
        self.files.append(arcname)
        if has_prefix:
            self.has_prefix_files.append(arcname)

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
            #self.write_index(tf)
            self.write_has_prefix_list(tf)
            self.write_files_list(tf)

    def add_module(self, tf):
        src = str(self.module.path)
        dst = self.site_packages_path() + self.module.path.name
        tf.add(src, arcname=dst)

    def _write_script_unix(self, tf, name, contents):
        ti = tarfile.TarInfo(self.scripts_path() + name)
        contents = contents.encode('utf-8')
        ti.size = len(contents)
        tf.addfile(ti, BytesIO(contents))
        self.record_file(ti.name)

    def _write_script_windows(self, tf, name, contents):
        self._write_script_unix(tf, name+'-script.py', contents)
        src = str(pkgdir / 'cli-{}.exe'.format(self.bitness))
        dst = self.scripts_path() + name + '.exe'
        tf.add(src, arcname=dst)
        self.record_file(dst)

    def create_scripts(self, tf):
        for name, (mod, func) in self.ini_info['scripts'].items():
            s = common.script_template.format(
                module=mod, func=func,
                # This is replaced when the package is installed:
                interpreter='/opt/anaconda1anaconda2anaconda3/bin/python',
            )
            if self.platform == Platform.windows:
                self._write_script_windows(tf, name, s)
            else:
                self._write_script_unix(tf, name, s)

    def write_index(self, tf):
        raise NotImplementedError

    def write_has_prefix_list(self, tf):
        contents = '\n'.join(self.has_prefix_files).encode('utf-8')
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

    wheel_md = {'tags':[]}
    with (dist_info / 'WHEEL').open() as f:
        for line in f:
            k, v = line.strip().split(':', 1)
            k = k.strip()
            v = v.strip().lower()
            if k == 'Tag':
                wheel_md['tags'].append(v)
            else:
                wheel_md[k] = v

    if wheel_md['Wheel-Version'] != '1.0':
        raise BadWheelError("wheel2conda only knows about wheel format 1.0")
    if wheel_md['Root-Is-Purelib'] != 'true':
        raise BadWheelError("Can't currently autoconvert packages with platlib")

    

if __name__ == '__main__':
    whl_file = Path(sys.argv[1])
    wtd = Path(unpack_wheel(whl_file))
    check_wheel_contents(wtd)
    pb = PackageBuilder(wtd.name, '3.5', Platform.linux, '64')
    with open('test_pkg.tar.bz2', 'wb') as f:
        pb.build(f)
    wtd.cleanup()
