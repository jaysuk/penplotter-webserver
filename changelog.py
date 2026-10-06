"""Reading CHANGELOG.md (no Flask here).

Entries are `## <version> - <date>`, with optional `### <group>` headings and `- ` bullets (a bullet may
continue on indented lines). Everything is returned as plain text, because a changelog fetched from GitHub
is shown in the page and must never be able to put markup there.
"""
import os
import re

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'CHANGELOG.md')
MAX_BYTES = 200 * 1024
MAX_ENTRIES = 60
MAX_ITEMS = 60          # per entry
MAX_TEXT = 300          # per bullet or heading

ENTRY_RE = re.compile(r'##\s+\[?([0-9]{1,4}(?:\.[0-9]{1,4}){0,3})\]?(?:\s+[-–(]\s*([0-9]{4}-[0-9]{2}-[0-9]{2})\)?)?\s*')
GROUP_RE = re.compile(r'###\s+(.+?)\s*')
BULLET_RE = re.compile(r'[-*]\s+(.+)')
CONTROL_RE = re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')


def _key(version):
    parts = tuple(int(part) for part in version.split('.'))
    return parts + (0,) * (4 - len(parts))


def _text(value):
    return CONTROL_RE.sub('', value).strip()[:MAX_TEXT]


def parse(text):
    """[{'version', 'date', 'groups': [{'title', 'items': [str]}]}], newest first."""
    entries = []
    entry = group = None
    for line in text[:MAX_BYTES].splitlines():
        match = ENTRY_RE.fullmatch(line.rstrip())
        if match:
            if len(entries) >= MAX_ENTRIES:
                break
            entry = {'version': match.group(1), 'date': match.group(2) or '', 'groups': []}
            entries.append(entry)
            group = None
            continue
        if entry is None:
            continue
        match = GROUP_RE.fullmatch(line.rstrip())
        if match:
            group = {'title': _text(match.group(1)), 'items': []}
            entry['groups'].append(group)
            continue
        match = BULLET_RE.fullmatch(line.strip()) if not line.startswith((' ', '\t')) else None
        if match:
            if group is None:
                group = {'title': '', 'items': []}
                entry['groups'].append(group)
            if sum(len(g['items']) for g in entry['groups']) < MAX_ITEMS:
                group['items'].append(_text(match.group(1)))
        elif group is not None and group['items'] and line.startswith((' ', '\t')) and line.strip():
            # a continuation of the bullet above
            group['items'][-1] = _text(group['items'][-1] + ' ' + line.strip())
    entries.sort(key=lambda e: _key(e['version']), reverse=True)
    return entries


def load(path=PATH):
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            return parse(f.read(MAX_BYTES))
    except OSError:
        return []


def between(entries, after=None, upto=None):
    """The entries newer than `after` and no newer than `upto` (either may be None)."""
    return [e for e in entries
            if (after is None or _key(e['version']) > _key(after))
            and (upto is None or _key(e['version']) <= _key(upto))]
