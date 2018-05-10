from collections import defaultdict
from pathlib import Path
import tempfile
import zipfile

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

class WheelContents:
    def __init__(self, whl_file):
        self.td = tempfile.TemporaryDirectory()
        with zipfile.ZipFile(whl_file) as zf:
            zf.extractall(self.td.name)
        self.unpacked = Path(self.td.name)

        self.metadata = _read_metadata(self.find_dist_info() / 'METADATA')

    def find_dist_info(self):
        for x in self.unpacked.iterdir():
            if x.name.endswith('.dist-info'):
                return x

        raise BadWheelError("Didn't find .dist-info directory")

    def check(self):
        dist_info = None
        data_dir = None

        for x in self.unpacked.iterdir():
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

        for field in ('Name', 'Version'):
            if field not in self.metadata:
                raise BadWheelError("Missing required metadata field: %s" % field)

    def filter_compatible_pythons(self, versions):
        if 'Requires-Python' in self.metadata:
            rp = self.metadata['Requires-Python'][0]
            if rp.startswith(('3', '>3', '>=3', '~=3',)):
                return [p for p in versions if not p.startswith('2.')]
            elif rp in ('<3', '<3.0'):
                return [p for p in versions if p.startswith('2.')]

        wheel_metadata = _read_metadata(self.find_dist_info() / 'WHEEL')
        py_tags = {t.split('-')[0] for t in wheel_metadata['Tag']}
        if py_tags == {'py3'}:
            return [p for p in versions if not p.startswith('2.')]
        elif py_tags == {'py2'}:
            return [p for p in versions if p.startswith('2.')]

        return versions
