import ast

sys_platforms = {
    'linux': 'linux',
    'osx': 'darwin',
    'win': 'win32',
}

os_names = {
    'linux': 'posix',
    'osx': 'posix',
    'win': 'nt',
}

platform_machines = {
    '32': 'i386',
    '64': 'x86_64'
}

class EnvMarkerNameFiller(ast.NodeTransformer):
    def __init__(self, python_version, platform, bitness):
        self.python_version = python_version
        self.platform = platform
        self.bitness = bitness

    def visit_Name(self, node):
        if node.id == 'python_version':
            new = ast.Str(self.python_version)
        elif node.id == 'python_full_version':
            # We can't really match this exactly, because we're building for
            # e.g. any Python 3.5.x
            new = ast.Str(self.python_version + '.0')
        else:
            raise ValueError("Unexpected name: %s" % node.id)

        return ast.copy_location(new, node)

    def visit_Attribute(self, node):
        if node.attr == 'platform':
            assert node.value.id == 'sys'
            new = ast.Str(sys_platforms[self.platform])
        elif node.attr == 'name':
            assert node.value.id == 'os'
            new = ast.Str(os_names[self.platform])
        elif node.attr == 'version':
            assert node.value.id == 'platform'
            # Don't know
            new = ast.Str('')
        elif node.attr == 'machine':
            assert node.value.id == 'platform'
            new = ast.Str(platform_machines[self.bitness])
        elif node.attr == 'python_implementation':
            assert node.value.id == 'platform'
            new = ast.Str('CPython')
        else:
            raise ValueError('Unknown attribute: %s' % node.attr)

        return ast.copy_location(new, node)

def eval_env_marker(s, python_version, platform, bitness):
    expr = ast.parse(s, '<environment_marker>', 'eval')
    filler = EnvMarkerNameFiller(python_version, platform, bitness)
    filler.visit(expr)
    codeobj = compile(expr, '<environment_marker', 'eval')
    return eval(codeobj)

def requires_dist_to_conda_requirements(reqs, python_version, platform, bitness):
    res = []
    for r in reqs:
        if ';' in r:
            r, env_marker = r.split(';', 1)
            applicable = eval_env_marker(env_marker, python_version, platform, bitness)
        else:
            applicable = True
        if applicable:
            res.append(r.replace('(', '').replace(')', ''))

    return res

# TODO: We're assuming that conda packages have the same name as PyPI distributions.
# This is mostly true, but it doesn't have to be. We may want some way to map
# them.
