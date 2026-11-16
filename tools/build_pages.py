"""Build a portable GitHub Pages site from maintained Markdown and Desktop assets.

Install requirements-site.txt, then run python tools/build_pages.py. Generated
HTML stays under build/pages. Existing Markdown remains the single source for
App, Agent/SDK, reference, architecture and development documentation. Local
links are rewritten for project-path hosting, and referenced source files have
readable HTML views. No GitHub authentication or publishing occurs here.
"""

import argparse
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import posixpath
import re
import shutil
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
from sim_platform.design import TYPE, SPACE, palette

ASSET_REVISION = hashlib.sha256(
    b''.join(
        path.read_bytes()
        for path in sorted((ROOT / 'docs/site/assets').glob('*'))
        if path.suffix in ('.css', '.js')
    )
    + (ROOT / 'sim_platform/design.py').read_bytes()
).hexdigest()[:12]

PUBLIC_ROOTS = ('docs', 'sim_platform', 'src', 'include', 'examples', 'tests', 'tools')
NAV = (
    (
        'Start here',
        [
            ('Overview', 'index.html', 'home'),
            ('Watch the demo', 'index.html#watch', 'play'),
            ('Private beta guide', 'docs/user-guide/private-beta.html', 'flask'),
        ],
    ),
    (
        'Use the App',
        [
            ('App guide', 'docs/user-guide/README.html', 'book'),
            ('First experiment', 'docs/user-guide/getting-started.html', 'flask'),
            ('Execution modes', 'docs/user-guide/execution-modes.html', 'clock'),
            ('Your own model', 'docs/user-guide/model-policies.html', 'layers'),
            ('Evaluate an Agent', 'docs/user-guide/evaluation.html', 'chart'),
            ('Local models & lineage', 'docs/user-guide/research-controls.html', 'layers'),
            ('Explain & review', 'docs/user-guide/study-tools.html', 'chart'),
            ('Command line', 'docs/user-guide/command-line.html', 'code'),
            ('Troubleshooting', 'docs/user-guide/troubleshooting.html', 'list'),
        ],
    ),
    (
        'Build an Agent',
        [
            ('Agent & SDK', 'docs/agent-development/README.html', 'agent'),
            ('Python projects', 'docs/agent-development/projects.html', 'folder'),
            ('Platform API', 'docs/reference/platform-api.html', 'code'),
            ('API reference', 'api/index.html', 'code'),
            ('API stability', 'docs/reference/api-stability.html', 'book'),
            ('Methodology', 'docs/reference/methodology.html', 'book'),
        ],
    ),
    (
        'Go deeper',
        [
            ('Architecture', 'docs/architecture/README.html', 'panel'),
            ('Development', 'docs/development/README.html', 'settings'),
            ('All documentation', 'docs/README.html', 'list'),
        ],
    ),
)
# Line icons for the navigation, drawn like the Desktop's (24-unit grid, 1.8 stroke).
ICONS = {
    'home': 'M3 11 12 4l9 7M5 10v10h5v-6h4v6h5V10',
    'play': 'm8 4 12 8-12 8z',
    'book': 'M4 4h6a2 2 0 0 1 2 2v14a2 2 0 0 0-2-2H4zM20 4h-6a2 2 0 0 0-2 2v14a2 2 0 0 1 2-2h6z',
    'flask': 'M9 3h6m-5 0v6l-6 10c-.5 1 .2 2 1.4 2h13.2c1.2 0 1.9-1 1.4-2L14 9V3M7 15h10',
    'clock': 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M12 7v5l3 2',
    'layers': 'm12 3 9 5-9 5-9-5zm-9 9 9 5 9-5m-18 4 9 5 9-5',
    'chart': 'M4 3v17h17M7 14l4-5 4 3 5-7',
    'agent': 'M8 4h8v3H8zM5 7h14v13H5zM2 11v5m20-5v5M9 11v2m6-2v2m-6 4h6',
    'folder': 'M3 6h7l2 2h9v12H3z',
    'code': 'm8 6-6 6 6 6m8-12 6 6-6 6',
    'panel': 'M3 4h18v16H3zM15 4v16',
    'settings': 'm9 3 6 0 .7 3 2 1.2 2.8-.8 3 5-2.2 2v2.2l2.2 2-3 5-2.8-.8-2 1.2-.7 3H9l-.7-3-2-1.2-2.8.8-3-5 2.2-2v-2.2l-2.2-2 3-5 2.8.8 2-1.2zM15 13a3 3 0 1 1-6 0 3 3 0 0 1 6 0',
    'list': 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
}


SECTIONS = {
    'user-guide': 'App guide',
    'agent-development': 'Agent & SDK',
    'reference': 'Reference',
    'architecture': 'Architecture',
    'development': 'Development',
}


def relative(target, page):
    """Keep all routes relative so user sites and repository project sites both work."""
    path, separator, fragment = target.partition('#')
    value = posixpath.relpath(path, posixpath.dirname(page) or '.')
    return value + (separator + fragment if separator else '')


def tutorial_chapters(evidence):
    """Render seek buttons from recorded chapter titles and their actual timings."""
    entries = []
    for caption in evidence['captions']:
        title = caption.get('chapter')
        if not isinstance(title, str) or not title.strip():
            raise ValueError('Tutorial evidence is missing a chapter title; record it again')
        name = title.split(' / ', 1)[-1].strip().title()
        seconds = int(caption['start'])
        entries.append(
            f'<button type="button" data-seek="{seconds}"><time>{seconds//60}:{seconds%60:02}</time>{html.escape(name)}</button>'
        )
    return ''.join(entries)


def chrome(page, title, description, content, repository, *, home=False):
    escape = html.escape
    base = relative('index.html', page).removesuffix('index.html') or './'
    links = []
    for label, entries in NAV:
        links.append(f'<p class="nav-section">{escape(label.upper())}</p>')
        for name, target, symbol in entries:
            current = ' aria-current="page"' if target == page else ''
            links.append(
                f'<a href="{relative(target, page)}"{current}><svg class="nav-symbol" viewBox="0 0 24 24" aria-hidden="true"><path d="{ICONS[symbol]}"/></svg>{escape(name)}</a>'
            )
    repo_link = (
        f'<a href="https://github.com/{escape(repository)}">Source on GitHub ↗</a>'
        if repository
        else ''
    )
    home_links = f'<nav class="home-links" aria-label="Explore Market Lab"><a href="{base}docs/user-guide/README.html">App guide</a><a href="{base}docs/user-guide/evaluation.html">Evaluation</a><a href="{base}docs/agent-development/README.html">Build an Agent</a><a href="{base}docs/reference/platform-api.html">API</a></nav>'
    layout_class = 'layout home-layout' if home else 'layout'
    page_kind = 'home' if home else 'documentation'
    return f'''<!doctype html>
<html lang="en" data-theme="light"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)} · Market Lab</title><meta name="description" content="{escape(description)}">
<meta name="theme-color" content="#ffffff"><meta property="og:title" content="{escape(title)} · Market Lab"><meta property="og:description" content="{escape(description)}"><meta property="og:type" content="website">
<link rel="icon" href="{base}assets/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="{base}assets/design.css?v={ASSET_REVISION}"><link rel="stylesheet" href="{base}assets/utilities.css?v={ASSET_REVISION}"><link rel="stylesheet" href="{base}assets/site.css?v={ASSET_REVISION}"><link rel="stylesheet" href="{base}assets/presentation.css?v={ASSET_REVISION}"><script src="{base}assets/site.js?v={ASSET_REVISION}" defer></script></head>
<body data-base="{base}" data-page="{page_kind}"><a class="skip" href="#main">Skip to content</a>
<header class="topbar"><div><a class="wordmark" href="{base}index.html"><span class="mark">m/</span>market lab</a><span class="top-label">Research, with a record.</span></div>{home_links}<div class="top-actions">{repo_link}<button class="icon-button" type="button" data-theme-toggle aria-label="Switch to dark appearance">Dark mode</button><button class="icon-button menu-toggle" type="button" data-menu-toggle aria-controls="navigation" aria-expanded="false">Menu</button></div></header>
<div class="{layout_class}"><aside class="sidebar" id="navigation"><button class="search-trigger" type="button" data-search-open>Search documentation <kbd>/</kbd></button><nav aria-label="Documentation">{''.join(links)}</nav><div class="side-bottom"><div class="local-tag">● LOCAL WORKSPACE</div><p class="side-note">Simulated trading.<br>Real experiment records.</p></div></aside><main id="main">{content}</main></div>
<dialog class="search-dialog" aria-label="Search documentation"><div class="search-top"><input type="search" placeholder="Search guides and API…" aria-label="Search documentation" autocomplete="off"><button type="button" class="icon-button" data-search-close>Cancel</button></div><p class="search-status" role="status" aria-live="polite"></p><div class="search-results"></div><div class="search-footer">Local documentation search · / to open · Click outside or press Escape to cancel</div></dialog></body></html>'''


def footer(page):
    return f'<footer><span>Market Lab / 0.1 source edition</span><span><a href="{relative("LICENSE.html", page)}">Apache-2.0</a> · <a href="{relative("docs/user-guide/support.html", page)}">Support &amp; limitations</a></span></footer>'


class Links(HTMLParser):
    """Collect local URLs in generated HTML for a bounded offline integrity check."""

    def __init__(self):
        super().__init__()
        self.urls, self.ids = [], set()

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if 'id' in values:
            self.ids.add(values['id'])
        for name in ('href', 'src', 'poster'):
            if values.get(name):
                self.urls.append(values[name])


def check_links(output):
    output = output.resolve()
    pages = {}
    for path in output.rglob('*.html'):
        # Sphinx checks the generated API reference's own links.
        if 'api' in path.relative_to(output).parts[:1]:
            continue
        parser = Links()
        parser.feed(path.read_text())
        pages[path.resolve()] = parser
    errors = []
    for path, parser in pages.items():
        for url in parser.urls:
            parts = urlsplit(url)
            if parts.scheme or parts.netloc:
                continue
            target = (path.parent / unquote(parts.path)).resolve() if parts.path else path
            if not target.is_relative_to(output.resolve()):
                errors.append(f'{path.relative_to(output)}: URL leaves site: {url}')
            elif not target.is_file():
                errors.append(f'{path.relative_to(output)}: missing {url}')
            elif (
                parts.fragment
                and target in pages
                and target.suffix == '.html'
                and unquote(parts.fragment) not in pages[target].ids
            ):
                errors.append(f'{path.relative_to(output)}: missing anchor {url}')
    if errors:
        raise ValueError('\n'.join(errors[:40]))
    return len(pages)


def design_stylesheet():
    """Emit shared semantic values without importing Desktop or requiring Node."""
    rules = []
    for light, selector in ((True, ':root'), (False, ':root[data-theme=dark]')):
        values = {'--ml-' + name.replace('_', '-'): value for name, value in palette(light).items()}
        values.update({'--ml-type-' + name: str(value) + 'px' for name, value in TYPE.items()})
        values.update({'--ml-space-' + name: str(value) + 'px' for name, value in SPACE.items()})
        rules.append(
            selector
            + ' {\n'
            + ''.join(f'  {name}: {value};\n' for name, value in values.items())
            + '}\n'
        )
    return '\n'.join(rules)


def build(root, output, repository=''):
    import markdown

    root, output = root.resolve(), output.resolve()
    if not output.is_relative_to(root / 'build'):
        raise ValueError('Output must be inside this checkout’s build/ directory')
    if repository and not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Repository must have the form owner/name')
    media = root / 'docs/site/media'
    required = ('demo.mp4', 'demo-poster.jpg', 'demo.en.vtt', 'demo-evidence.json')
    if any(not (media / name).is_file() for name in required):
        raise ValueError('Record the Desktop tutorial first; required demo media is missing')
    documents = set(root.glob('*.md'))
    for directory in PUBLIC_ROOTS:
        documents.update(
            p
            for p in (root / directory).rglob('*.md')
            if 'site' not in p.relative_to(root / directory).parts
        )
    documents = sorted(documents)
    routes = {p: p.relative_to(root).with_suffix('.html').as_posix() for p in documents}
    routes[root / 'LICENSE'] = 'LICENSE.html'
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    shutil.copytree(root / 'docs/site/assets', output / 'assets')
    (output / 'assets/design.css').write_text(design_stylesheet())
    shutil.copytree(media, output / 'media')
    (output / '.nojekyll').touch()
    source_views = {}
    directory_views = {}
    search = []

    def rewrite_url(match, source, destination):
        name, quote, url = match.group(1), match.group(2), match.group(3)
        parts = urlsplit(html.unescape(url))
        if parts.scheme or parts.netloc or not parts.path:
            return match.group(0)
        target = (source.parent / unquote(parts.path)).resolve()
        if (
            not target.is_relative_to(root)
            or not target.exists()
            or target.is_relative_to(root / 'internal')
        ):
            raise ValueError(f'Invalid public document link in {source}: {url}')
        if target.is_relative_to(media):
            path = 'media/' + target.relative_to(media).as_posix()
        elif target in routes:
            path = routes[target]
        elif target.suffix in (
            '.py',
            '.cpp',
            '.hpp',
            '.txt',
            '.json',
            '.yml',
            '.toml',
        ) or target.name in ('Makefile', 'CMakeLists.txt'):
            path = '_source/' + target.relative_to(root).as_posix() + '.html'
            source_views[target] = path
        elif target.is_file():
            path = target.relative_to(root).as_posix()
            asset = output / path
            asset.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(target, asset)
        else:
            path = routes.get(target / 'README.md')
            if not path:
                path = target.relative_to(root).as_posix() + '/index.html'
                directory_views[target] = path
        fragment = ('#' + parts.fragment) if parts.fragment else ''
        return f'{name}={quote}{relative(path + fragment, destination)}{quote}'

    for source in documents:
        page = routes[source]
        text = source.read_text()
        renderer = markdown.Markdown(
            extensions=['fenced_code', 'tables', 'toc', 'sane_lists'],
            extension_configs={'toc': {'permalink': '¶'}},
        )
        article = renderer.convert(text)
        article = re.sub(
            r'(href|src)=([\"\'])(.*?)\2', lambda m: rewrite_url(m, source, page), article
        )
        title_match = re.search(r'^# (.+)$', text, re.M)
        title = title_match[1] if title_match else source.stem.replace('_', ' ').title()
        plain = re.sub('<[^>]+>', ' ', article)
        plain = re.sub(r'\s+', ' ', html.unescape(plain).replace('¶', '')).strip()
        section = next(
            (value for key, value in SECTIONS.items() if key in source.parts),
            'Source & package docs',
        )
        content = f'<div class="content doc-content"><div class="breadcrumbs"><a href="{relative("index.html",page)}">Market Lab</a> / {html.escape(section)}</div><div class="article-layout"><article class="article">{article}<div class="source-note">Maintained source: {html.escape(source.relative_to(root).as_posix())}</div></article><aside class="toc" aria-label="On this page"><p>ON THIS PAGE</p>{renderer.toc}</aside></div>{footer(page)}</div>'
        destination = output / page
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(chrome(page, title, plain[:170], content, repository))
        search.append(
            dict(
                title=title,
                url=page,
                section=section,
                summary=plain[len(title) :].strip()[:100],
                text=plain,
            )
        )
    license_content = (
        '<div class="content doc-content"><article class="article"><h1>Apache License 2.0</h1><pre>'
        + html.escape((root / 'LICENSE').read_text())
        + '</pre></article>'
        + footer('LICENSE.html')
        + '</div>'
    )
    (output / 'LICENSE.html').write_text(
        chrome(
            'LICENSE.html',
            'License',
            'Market Lab is licensed under Apache-2.0.',
            license_content,
            repository,
        )
    )
    for source, page in source_views.items():
        lines = ''.join(
            f'<span id="L{i}"><a href="#L{i}" aria-label="Line {i}">{i}</a>{html.escape(line)}</span>'
            for i, line in enumerate(source.read_text().splitlines(), 1)
        )
        name = source.relative_to(root).as_posix()
        content = f'<div class="content doc-content"><div class="breadcrumbs"><a href="{relative("docs/development/code-map.html",page)}">Source code map</a> / {html.escape(name)}</div><h1>{html.escape(source.name)}</h1><pre class="source-code">{lines}</pre>{footer(page)}</div>'
        destination = output / page
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            chrome(page, source.name, 'Source reference for ' + name, content, repository)
        )
    for directory, page in directory_views.items():
        entries = []
        for asset in sorted(directory.iterdir()):
            if asset.is_file() and not asset.name.startswith('.'):
                target = output / asset.relative_to(root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(asset, target)
                url = relative(asset.relative_to(root).as_posix(), page)
                entries.append(f'<li><a href="{url}">{html.escape(asset.name)}</a></li>')
        content = (
            '<div class="content doc-content"><article class="article"><h1>'
            + html.escape(directory.name)
            + '</h1><ul>'
            + ''.join(entries)
            + '</ul></article>'
            + footer(page)
            + '</div>'
        )
        (output / page).write_text(
            chrome(page, directory.name, 'Reference assets', content, repository)
        )
    evidence = json.loads((media / 'demo-evidence.json').read_text())
    home = (
        (root / 'docs/site/home.html')
        .read_text()
        .replace('{{chapters}}', tutorial_chapters(evidence))
        .replace('{{footer}}', footer('index.html'))
    )
    (output / 'index.html').write_text(
        chrome(
            'index.html',
            'A local workbench for trading agents',
            'Build Python trading agents, run simulated experiments on historical market data, and inspect or replay the results.',
            home,
            repository,
            home=True,
        )
    )
    (output / 'search-index.json').write_text(json.dumps(search, ensure_ascii=False))
    (output / 'build-info.json').write_text(
        json.dumps(
            dict(
                repository=repository,
                documents=len(documents),
                source_views=len(source_views),
                demo_seconds=evidence['duration_seconds'],
            ),
            indent=2,
        )
        + '\n'
    )
    from build_api_reference import build as build_api

    build_api(output / 'api')
    count = check_links(output)
    print(f'Built and checked {count} HTML pages in {output}')
    return count


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/pages')
    parser.add_argument('--repository', default=os.environ.get('GITHUB_REPOSITORY', ''))
    args = parser.parse_args()
    build(ROOT, args.output, args.repository)
