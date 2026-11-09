#!/usr/bin/env python3
"""Build the single-file Persian project guide from maintained source documents."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'docs/PROJECT_GUIDE.md'
SOURCES = [
    'README.md', 'docs/STATUS.md', 'docs/PC_VOICE.md', 'docs/CONFIGURATION.md',
    'docs/STREAMING_ARCHITECTURE.md', 'docs/AEC.md', 'docs/PROTOCOL.md',
    'docs/DATASETS.md', 'docs/PLATFORMS.md', 'docs/TROUBLESHOOTING.md',
    'docs/BENCHMARKS.md', 'docs/HARDWARE_VALIDATION.md', 'THIRD_PARTY.md',
    'AGENTS.md', 'docs/SPEC.md', 'docs/ISSUE_REGISTER.md', 'docs/AUDIT_REPORT.md',
]


def render() -> str:
    parts = [
        '# راهنمای یکپارچهٔ Persian Voice AI\n\n'
        '> این فایل خروجی خودکار `tools/build_docs.py` است؛ مستقیم ویرایش نکنید.\n'
        '> اسناد منبع را اصلاح و سپس `python tools/build_docs.py` را اجرا کنید.\n\n'
        'مرجع واحد مطالعهٔ پروژه: راه‌اندازی، وضعیت فعلی، تنظیمات، معماری، داده،\n'
        'امنیت و تست. SPEC شامل اهداف آینده است؛ گزارش‌های audit و issue سابقه‌اند،\n'
        'نه اثبات قابلیت فعلی. در تعارض تاریخی، کد و STATUS فعلی ملاک‌اند.\n\n'
        '## فهرست بخش‌ها\n'
    ]
    for index, source in enumerate(SOURCES, 1):
        parts.append(f'- [بخش {index}: {source}](#section-{index})\n')
    for index, source in enumerate(SOURCES, 1):
        path = ROOT / source
        text = path.read_text(encoding='utf-8')
        # Preserve links to their original documents (including local anchors).
        # This avoids duplicate-heading anchors in a concatenated guide.
        def link(match: re.Match[str]) -> str:
            target = match.group(1)
            if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', target) or target.startswith('/'):
                return match.group(0)
            name, sep, anchor = target.partition('#')
            resolved = (path.parent / name).resolve() if name else path
            relative = Path(os.path.relpath(resolved, OUTPUT.parent)).as_posix()
            return '](' + relative + (sep + anchor if sep else '') + ')'
        text = re.sub(r'\]\(([^)]+)\)', link, text)
        parts.append(f'\n---\n\n<a id="section-{index}"></a>\n\n'
                     f'## بخش {index} — {source}\n\n{text.rstrip()}\n')
    return ''.join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='fail if generated guide is stale')
    args = parser.parse_args(argv)
    content = render()
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding='utf-8') != content:
            print('Guide is stale; run python tools/build_docs.py')
            return 1
    else:
        OUTPUT.write_text(content, encoding='utf-8')
        print(f'Updated {OUTPUT.relative_to(ROOT)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
