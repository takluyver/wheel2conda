This is an experimental tool to convert pure Python wheels to conda packages

It bypasses ``conda-build``, creating conda packages for all platforms directly.
It converts the wheel metadata into conda metadata, so the only input needed is
a wheel file.

To use it, download a wheel file and run::

     wheel2conda foo-0.1-py2.py3-none-any.whl

Output is arranged in directories by platform, e.g. ``linux-64``.
