"""Build the versioned API reference from the public objects' docstrings with Sphinx.

    python -m pip install -r requirements-site.txt
    python tools/build_api_reference.py            # into build/pages/api/<version>/

The public objects are the ones ``tools/check_public_api.py`` freezes, so the reference
documents exactly the supported API. Docstrings use Google style (Args, Returns,
Raises, Example), rendered by Sphinx's napoleon extension. Each release builds its own
``api/<version>/`` folder; ``api/versions.json`` lists them and ``api/index.html``
opens the newest. tools/build_pages.py calls this when Sphinx is installed.
"""

import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

CONF = '''project = "Market Lab"
release = version = {version!r}
extensions = ["sphinx.ext.autodoc", "sphinx.ext.napoleon", "sphinx.ext.viewcode"]
autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_default_options = {{"members": True, "undoc-members": False, "show-inheritance": True}}
napoleon_google_docstring = True
napoleon_numpy_docstring = False
html_theme = {theme!r}
html_title = "Market Lab {version} API"
html_static_path = []


def _examples_as_code(app, what, name, obj, options, lines):
    # Docstring 'Example:' sections hold Python code; show them as code blocks.
    for index, line in enumerate(lines):
        if line.strip() in ("Example:", "Examples:"):
            lines[index] = line.rstrip()[:-1] + "::"


def setup(app):
    app.connect("autodoc-process-docstring", _examples_as_code, priority=400)
'''


def sources(target: Path, version: str) -> None:
    """Write conf.py, index.rst and one page per public module into ``target``."""
    from check_public_api import PUBLIC

    target.mkdir(parents=True, exist_ok=True)
    try:
        import furo  # noqa: F401

        theme = 'furo'
    except ImportError:
        theme = 'alabaster'
    (target / 'conf.py').write_text(CONF.format(version=version, theme=theme))
    pages = []
    for module, names in PUBLIC.items():
        page = module.replace('.', '-')
        pages.append(page)
        lines = [module, '=' * len(module), '', f'.. automodule:: {module}']
        if module == 'sim_platform':
            # Platform's methods live in its record, Agent and credential groups.
            lines += [
                '   :no-members:',
                '',
                '.. autoclass:: sim_platform.Platform',
                '   :members:',
                '   :inherited-members:',
                '',
                '.. autoexception:: sim_platform.ContractError',
            ]
            (target / f'{page}.rst').write_text('\n'.join(lines) + '\n')
            continue
        if names is not None:
            members = [n for n in names if not n.startswith('__')]
            lines += [f'   :members: {", ".join(members)}']
        else:
            lines += ['   :members:']
        (target / f'{page}.rst').write_text('\n'.join(lines) + '\n')
    title = f'Market Lab {version} API reference'
    index = [
        title,
        '=' * len(title),
        '',
        'The supported public API. Anything not listed here is internal; see the',
        'API stability page in the documentation for versioning and deprecation.',
        '',
        '.. toctree::',
        '   :maxdepth: 2',
        '',
    ] + [f'   {page}' for page in pages]
    (target / 'index.rst').write_text('\n'.join(index) + '\n')


def build(output: Path, *, strict: bool = False) -> Path:
    """Build the reference for the current version; return its folder."""
    from sphinx.cmd.build import build_main
    from sim_platform import __version__

    work = ROOT / 'build/api-source'
    shutil.rmtree(work, ignore_errors=True)
    sources(work, __version__)
    target = output / __version__
    shutil.rmtree(target, ignore_errors=True)
    arguments = ['-b', 'html', '-q', str(work), str(target)]
    if strict:
        arguments.insert(0, '-W')
    if build_main(arguments) != 0:
        raise SystemExit('Sphinx failed to build the API reference')
    versions_file = output / 'versions.json'
    versions = json.loads(versions_file.read_text()) if versions_file.exists() else []
    if __version__ not in versions:
        versions.append(__version__)
    versions.sort(key=lambda v: [int(p) if p.isdigit() else p for p in v.split('.')])
    versions_file.write_text(json.dumps(versions, indent=1) + '\n')
    (output / 'index.html').write_text(
        '<!doctype html><meta charset="utf-8">'
        f'<meta http-equiv="refresh" content="0; url={versions[-1]}/index.html">'
        f'<title>Market Lab API</title><a href="{versions[-1]}/index.html">'
        f'Market Lab {versions[-1]} API reference</a>\n'
    )
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--output', type=Path, default=ROOT / 'build/pages/api')
    parser.add_argument('--strict', action='store_true', help='treat warnings as errors')
    arguments = parser.parse_args()
    print(build(arguments.output, strict=arguments.strict))
