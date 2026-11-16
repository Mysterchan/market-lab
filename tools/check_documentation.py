"""Check documentation language and local links without network access.

Run from any directory. Historical internal archives are excluded. Use
--include-internal to check the active development documents as well.
Canonical guides and API docstrings use English; explicitly named Cantonese
user-guide translations retain their language and receive the same link checks.
"""

import argparse
import ast
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
HAN = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]')
LINK = re.compile(r'!?\[[^\]\n]*\]\(\s*(<[^>]+>|[^\s)]+)(?:\s+"[^"]*")?\s*\)')
ROOTS = ('docs', 'sim_platform', 'src', 'include', 'examples', 'tests', 'tools')
EXCLUDED_DIRECTORIES = frozenset(('node_modules', '__pycache__', '.venv', 'build'))


def current_pages(root: Path, include_internal: bool) -> list[Path]:
    """Find maintained Markdown; generated output and historical records stay outside it."""
    pages = set(root.glob('*.md'))
    for name in ROOTS + (('internal',) if include_internal else ()):
        for page in (root / name).rglob('*.md'):
            relative = page.relative_to(root)
            if not (
                relative.as_posix().startswith('internal/archive/')
                or EXCLUDED_DIRECTORIES.intersection(relative.parts[:-1])
            ):
                pages.add(page)
    return sorted(pages)


def prose_lines(text: str):
    """Skip fenced examples when interpreting Markdown headings and links."""
    fence = None
    for number, line in enumerate(text.splitlines(), 1):
        marker = re.match(r'^\s*(`{3,}|~{3,})', line)
        if marker:
            kind = marker[1][0]
            if fence is None:
                fence = kind
            elif fence == kind:
                fence = None
        elif fence is None:
            yield number, line


def anchors(text: str) -> set[str]:
    """Recognize ordinary GitHub heading anchors and explicit HTML IDs."""
    result, counts = set(), {}
    for _, line in prose_lines(text):
        result.update(re.findall(r'\bid=["\']([^"\']+)["\']', line))
        heading = re.match(r'^#{1,6}\s+(.+?)\s*#*\s*$', line)
        if heading:
            slug = re.sub(r'[^\w\- ]', '', heading[1].lower()).replace(' ', '-')
            count = counts.get(slug, 0)
            counts[slug] = count + 1
            result.add(slug + ('-' + str(count) if count else ''))
    return result


def check_page(page: Path, root: Path) -> list[str]:
    text = page.read_text(encoding='utf-8')
    name = page.relative_to(root).as_posix()
    translated = re.fullmatch(r'docs/user-guide/[^/]+-zh-hk\.md', name) is not None
    errors = [
        f'{name}:{line}: write current documentation in English'
        for line, value in enumerate(text.splitlines(), 1)
        if not translated and HAN.search(value)
    ]
    for line, value in prose_lines(text):
        for match in LINK.finditer(value):
            link = urlsplit(match[1].strip('<>'))
            if link.scheme or link.netloc:
                continue
            target = (page.parent / unquote(link.path)).resolve() if link.path else page
            if not target.is_relative_to(root.resolve()):
                errors.append(f'{name}:{line}: local link leaves repository: {match[1]}')
            elif not target.exists():
                errors.append(f'{name}:{line}: missing local target: {match[1]}')
            elif not name.startswith('internal/') and target.is_relative_to(root / 'internal'):
                errors.append(f'{name}:{line}: public guide depends on internal documentation')
            elif link.fragment and target.suffix == '.md':
                if unquote(link.fragment) not in anchors(target.read_text(encoding='utf-8')):
                    errors.append(f'{name}:{line}: missing heading: {match[1]}')
    return errors


def check_docstrings(root: Path) -> list[str]:
    errors = []
    for source in (root / 'sim_platform').rglob('*.py'):
        tree = ast.parse(source.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if HAN.search(ast.get_docstring(node) or ''):
                    errors.append(
                        f'{source.relative_to(root)}:{getattr(node, "lineno", 1)}: '
                        'write module and API documentation in English'
                    )
    return errors


def check(root: Path, *, include_internal: bool = False) -> tuple[list[Path], list[str]]:
    root = root.resolve()
    pages = current_pages(root, include_internal)
    errors = [error for page in pages for error in check_page(page, root)]
    errors.extend(check_docstrings(root))
    return pages, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--include-internal', action='store_true')
    args = parser.parse_args()
    pages, errors = check(ROOT, include_internal=args.include_internal)
    if errors:
        print('\n'.join(errors))
        return 1
    print(
        f'Checked {len(pages)} guides, language rules, local links/headings and Python docstrings.'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
