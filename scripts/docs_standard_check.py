#!/usr/bin/env python3
"""Conformance checker for the puglieseweb Documentation Standard.

The standard owes this script: §5.7 says "The standard's host repository SHALL
publish a dependency-free reference checker script implementing [the §6 level
checklists]; repos SHOULD run it in CI at Level 2+." Until it existed, §6 was
audited by hand or not at all — and a conformance level nobody can measure is
a conformance level nobody has.

WHAT IT CHECKS, AND WHAT IT REFUSES TO GUESS

§5.7 promises the level checklists are "deliberately checkable from paths,
filename grammar, and marker lines alone", and that is exactly the line this
script holds. It reads a checkout; it runs nothing, fetches nothing and parses
no Markdown beyond looking for marker lines. Rules that need judgement —
"no wiki holds canonical content", "scenario-based acceptance criteria",
"repo-qualified cross-references everywhere" — are NOT implemented and are
listed by `--list-unchecked`, so a green run never reads as more assurance
than it is.

"IF ANY" IS THE WHOLE DESIGN. Level 1 items 3–6 bind only when the artifact
class exists, and §2.6 says an absent pillar is conformant while an empty one
is clutter ("Don't do that. It's horrible" — Diátaxis). So every pillar check
starts by asking whether the pillar is there at all, and a repo with no ADRs
passes the ADR rules by having none.

LEVELS. A finding binds at the level the repo TARGETS and above. The target
comes from `.github/docs-standard.yml` (`level:`), or `--level`, defaulting to
1 — the level §6 says every repo including a one-repo startup meets. A repo
that declares `level: 0` is *Adopting*: a declared, valid state, so only the
declaration itself is checked.

Exit code is non-zero if any finding binds at the target level. Findings above
the target are reported as `next-level` notes, because the useful question
after "am I conformant" is "what would the next level cost".
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field

STANDARD_VERSION = '1.0'

# §3.2: `ADR-NNN-kebab-title.md`, zero-padded. Both signals are required — the
# `adr` path segment AND this grammar — because stable ADR-NNN identifiers are
# what make "superseded by" chains and cross-repo references resolvable.
ADR_FILE_RE = re.compile(r'^ADR-\d{3,}-[a-z0-9]+(?:-[a-z0-9]+)*\.md$')

# §3.2 lifecycle. `Superseded by ADR-NNN` and `Deprecated (date: reason)` carry
# a payload, so they are prefix-matched.
ADR_STATUS_RE = re.compile(r'^\*\*Status:\*\*\s*(.+?)\s*$', re.M)
ADR_STATUSES = ('Proposed', 'Accepted', 'Rejected', 'Superseded by ADR-', 'Deprecated')
ADR_DATE_RE = re.compile(r'^\*\*Date:\*\*\s*\d{4}-\d{2}-\d{2}', re.M)
ADR_H1_RE = re.compile(r'^#\s+ADR-\d{3,}:\s*\S', re.M)
SUPERSEDED_BY_RE = re.compile(r'Superseded by ADR-(\d{3,})')
SUPERSEDES_RE = re.compile(r'Supersedes\s+ADR-(\d{3,})')

# §3.3: verb-led kebab-case slug, no numbers.
CHANGE_SLUG_RE = re.compile(r'^[a-z]+(?:-[a-z]+)*$')

# §3.1/§3.3: requirement statements use BOLD RFC 2119 keywords.
RFC2119_RE = re.compile(r'\*\*(MUST NOT|MUST|SHALL NOT|SHALL|SHOULD NOT|SHOULD|REQUIRED|RECOMMENDED|MAY|OPTIONAL)\*\*')

PRUNE_DIRS = {'.git', 'node_modules', '.venv', 'venv', '__pycache__', 'dist', 'build', '.next'}

# Rules a static reader must not pretend to judge. Printed by
# --list-unchecked so a green run is never mistaken for full conformance.
UNCHECKED = [
    ('L1.2', 'no wiki/Confluence holds canonical content — outside a checkout'),
    ('L1.7', 'no hand-maintained catalog table is the canonical index (§5.5) — a table that IS the index reads the same as one that is not'),
    ('L2', 'scenario-based acceptance criteria — a judgement about prose'),
    ('L2', 'graduation discipline actually practised (§3.3) — needs merge dates, not paths'),
    ('L2', 'regenerate-and-diff CI gate for code-first contracts — needs the contract build'),
    ('L3', 'repo-qualified cross-references everywhere (§5.4) — needs the org repo list'),
    ('L3', 'tombstones on moves and renames — needs history'),
]


@dataclass
class Finding:
    level: int
    rule: str
    path: str
    message: str


@dataclass
class Result:
    target: int
    findings: list[Finding] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def add(self, level: int, rule: str, path: str, message: str) -> None:
        self.findings.append(Finding(level, rule, path, message))

    def binding(self) -> list[Finding]:
        return [f for f in self.findings if f.level <= self.target]

    def next_level(self) -> list[Finding]:
        return [f for f in self.findings if f.level > self.target]


# ── filesystem helpers ────────────────────────────────────────────────────

def _walk(root: str, rel: str = '') -> list[str]:
    """Repo-relative paths under `root/rel`, pruned and crash-proof.

    Tolerates a directory it cannot read (a path past the platform's limit, a
    file vanishing mid-walk) rather than aborting: a checker that dies on one
    repo checks nothing.
    """
    start = os.path.join(root, rel) if rel else root
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(start, onerror=lambda _e: None):
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS]
        for name in filenames:
            full = os.path.join(dirpath, name)
            out.append(os.path.relpath(full, root).replace('\\', '/'))
    return sorted(out)


def _read(root: str, rel: str) -> str:
    try:
        with open(os.path.join(root, rel), encoding='utf-8', errors='replace') as fh:
            return fh.read()
    except OSError:
        return ''


def _exists(root: str, rel: str) -> bool:
    return os.path.exists(os.path.join(root, rel))


def _isdir(root: str, rel: str) -> bool:
    return os.path.isdir(os.path.join(root, rel))


def _subdirs(root: str, rel: str) -> list[str]:
    full = os.path.join(root, rel)
    if not os.path.isdir(full):
        return []
    return sorted(d for d in os.listdir(full) if os.path.isdir(os.path.join(full, d)))


# ── the declaration ───────────────────────────────────────────────────────

def read_declaration(root: str) -> dict:
    """`.github/docs-standard.yml`, read with a deliberately tiny parser.

    Only the flat scalars this checker acts on (`standard`, `level`) and the
    presence of `pillars`/`aliases` are read. A YAML dependency would break
    §5.7's "dependency-free", and the file's own schema (§5.7) is flat enough
    that a real parser buys nothing here.
    """
    text = _read(root, '.github/docs-standard.yml')
    if not text:
        return {}
    decl: dict = {'_present': True}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or line[:1].isspace():
            continue
        if ':' not in stripped:
            continue
        key, _, value = stripped.partition(':')
        value = value.split('#')[0].strip().strip('"\'')
        decl[key.strip()] = value
    return decl


# ── Level 1 ───────────────────────────────────────────────────────────────

def check_readme(root: str, res: Result) -> None:
    res.checked.append('L1.1 README.md at repo root')
    if not _exists(root, 'README.md'):
        res.add(1, 'L1.1', 'README.md',
                'missing. §3.5: the front door of every repo — what it is, how to run it, '
                'and a link into docs/.')


def adr_dirs(root: str) -> list[str]:
    """The repo's ADR directories. §3.2 canonical `docs/design/adr/`, with
    `docs/adr/` grandfathered permanently for a pre-existing sequence."""
    return [d for d in ('docs/design/adr', 'docs/adr') if _isdir(root, d)]


def check_adrs(root: str, res: Result) -> None:
    dirs = adr_dirs(root)
    # Any OTHER path with an `adr` segment is a third home the scanner will
    # not find — worth catching even though §3.2 names only the two.
    stray = sorted({
        os.path.dirname(p) for p in _walk(root)
        if '/adr/' in f'/{p}' and os.path.dirname(p) not in dirs
        and os.path.basename(os.path.dirname(p)) == 'adr'
    })
    if not dirs and not stray:
        res.skipped.append('L1.3 ADRs (none in this repo — an absent pillar is conformant)')
        return
    res.checked.append('L1.3 ADR directory, filenames and lifecycle')

    if len(dirs) > 1:
        res.add(1, 'L1.3', dirs[1],
                'a repo MUST use exactly ONE ADR directory, never both `docs/design/adr/` '
                'and `docs/adr/` (§3.2) — humans and scripts scan a single path.')
    for extra in stray:
        res.add(1, 'L1.3', extra,
                f'ADR directory outside the two §3.2 permits (`docs/design/adr/` canonical, '
                f'`docs/adr/` grandfathered). The scanner will not find it.')

    numbers: dict[str, str] = {}
    for adr_dir in dirs + stray:
        for name in sorted(os.listdir(os.path.join(root, adr_dir))):
            rel = f'{adr_dir}/{name}'
            if not name.endswith('.md') or os.path.isdir(os.path.join(root, rel)):
                continue
            if name.upper().startswith('README'):
                continue
            if not ADR_FILE_RE.match(name):
                res.add(1, 'L1.3', rel,
                        'ADR filename must be `ADR-NNN-kebab-title.md`, zero-padded (§3.2). '
                        'Stable ADR-NNN identifiers are what make supersede chains and '
                        'cross-repo references resolvable.')
                continue
            number = name[4:name.index('-', 4)]
            if number in numbers:
                res.add(1, 'L1.3', rel,
                        f'ADR number {number} is already used by `{numbers[number]}` (§3.2: '
                        f'numbers are never reused; renumber on collision).')
            numbers[number] = rel
            _check_adr_body(root, rel, res)

    _check_supersede_links(root, dirs + stray, res)


def _check_adr_body(root: str, rel: str, res: Result) -> None:
    text = _read(root, rel)
    if not ADR_H1_RE.search(text):
        res.add(1, 'L1.3', rel,
                'first heading must be `# ADR-NNN: Title` (§3.2 format; H1-as-title is '
                'REQUIRED — cross-repo aggregation depends on it).')
    if not ADR_DATE_RE.search(text):
        res.add(1, 'L1.3', rel, 'missing a `**Date:** YYYY-MM-DD` marker line (§3.2 format).')
    match = ADR_STATUS_RE.search(text)
    if not match:
        res.add(1, 'L1.3', rel, 'missing a `**Status:**` marker line (§3.2 lifecycle).')
        return
    status = match.group(1)
    if not any(status.startswith(valid) for valid in ADR_STATUSES):
        res.add(1, 'L1.3', rel,
                f'status {status!r} is not one of the §3.2 lifecycle values '
                f'({", ".join(ADR_STATUSES)}).')


def _check_supersede_links(root: str, dirs: list[str], res: Result) -> None:
    """§3.2: "links MUST be bidirectional". An ADR that says it is superseded
    by another, where that other never claims to supersede it, is a chain with
    one end nailed down — the reason the rule is a MUST."""
    bodies: dict[str, tuple[str, str]] = {}
    for adr_dir in dirs:
        for name in sorted(os.listdir(os.path.join(root, adr_dir))):
            if ADR_FILE_RE.match(name):
                rel = f'{adr_dir}/{name}'
                bodies[name[4:name.index('-', 4)]] = (rel, _read(root, rel))

    for number, (rel, text) in bodies.items():
        status = ADR_STATUS_RE.search(text)
        if not status:
            continue
        for target in SUPERSEDED_BY_RE.findall(status.group(1)):
            other = bodies.get(target)
            if other is None:
                continue  # a cross-repo or not-yet-written successor; §5.4's problem
            if target == number:
                continue
            if not any(n == number for n in SUPERSEDES_RE.findall(other[1])):
                res.add(2, 'L2', rel,
                        f'says "Superseded by ADR-{target}", but `{other[0]}` never says it '
                        f'supersedes ADR-{number} (§3.2: supersede links MUST be '
                        f'bidirectional).')


def check_change_specs(root: str, res: Result) -> None:
    if not _isdir(root, 'specs/changes'):
        res.skipped.append('L1.4 change specs (no specs/changes/ — an absent pillar is conformant)')
        return
    res.checked.append('L1.4 change-spec folders')

    for slug in _subdirs(root, 'specs/changes'):
        rel = f'specs/changes/{slug}'
        if not CHANGE_SLUG_RE.match(slug):
            res.add(1, 'L1.4', rel,
                    'change folders are verb-led kebab-case slugs with no numbers (§3.3) — '
                    'a number-based id breaks when the Specs host is not the code repo.')
        single = _exists(root, f'{rel}/spec.md')
        triple = _exists(root, f'{rel}/proposal.md')
        if not single and not triple:
            res.add(1, 'L1.4', rel,
                    'a change folder needs either `proposal.md` (three-artifact form) or '
                    '`spec.md` (permitted single-file form for small changes, §3.3).')
            continue
        # §3.3 + Appendix A: requirement statements carry bold RFC 2119 keywords.
        # Checked on the requirement files, never on proposal prose.
        req_files = [f'{rel}/spec.md'] if single else _walk(root, f'{rel}/specs')
        req_files = [p for p in req_files if p.endswith('.md')]
        for req in req_files:
            if not RFC2119_RE.search(_read(root, req)):
                res.add(1, 'L1.4', req,
                        'no bold RFC 2119 keyword found (§3.3: requirement statements use '
                        '**MUST** / **SHOULD** / **MAY**) — without one this is prose, not a '
                        'requirement anything can be checked against.')


def check_api_contract(root: str, res: Result) -> None:
    canonical = _exists(root, 'openapi/openapi.yaml')
    permitted = _exists(root, 'openapi.yaml')
    strays = [p for p in _walk(root)
              if os.path.basename(p) in ('openapi.yaml', 'openapi.yml')
              and p not in ('openapi/openapi.yaml', 'openapi.yaml')]
    if not (canonical or permitted or strays):
        res.skipped.append('L1.5 API contract (none in this repo — an absent pillar is conformant)')
        return
    res.checked.append('L1.5 API contract location')
    for stray in strays:
        if stray.startswith('docs/'):
            res.add(1, 'L1.5', stray,
                    'the API contract is NOT under docs/ (§5.1) — it belongs at '
                    '`openapi/openapi.yaml`, or root `openapi.yaml` for a small service.')
        else:
            res.add(2, 'L2', stray,
                    'contract outside `openapi/openapi.yaml` (root `openapi.yaml` is '
                    'permitted for small services, §5.1).')


def check_requirements(root: str, res: Result) -> None:
    if not _isdir(root, 'docs/requirements'):
        res.skipped.append('L1.6 requirements (no docs/requirements/ — an absent pillar is conformant)')
        return
    res.checked.append('L1.6 requirement document folders')
    for title in _subdirs(root, 'docs/requirements'):
        rel = f'docs/requirements/{title}'
        if not _isdir(root, f'{rel}/specs'):
            res.add(1, 'L1.6', rel,
                    'a requirement document is a folder-per-title with a REQUIRED `specs/` '
                    'holding the living body (§3.1).')
            continue
        if not [p for p in _walk(root, f'{rel}/specs') if p.endswith('.md')]:
            res.add(1, 'L1.6', f'{rel}/specs',
                    'empty. §2.6: folders exist when they have content — an absent pillar is '
                    'conformant, an empty one is clutter.')


def check_design_architecture_split(root: str, res: Result) -> None:
    """§3.2: `docs/design/architecture/` MUST NOT be used — the classifier's
    architecture type fires only on `docs/architecture/**`, so prose put there
    silently loses its typing. Silent is what makes it worth a check."""
    if _isdir(root, 'docs/design/architecture'):
        res.checked.append('L1 design/architecture split')
        res.add(1, 'L1.3', 'docs/design/architecture',
                'MUST NOT be used (§3.2). System-level architecture prose lives at '
                '`docs/architecture/`, a SIBLING of `docs/design/` — content here loses its '
                'typing without any error.')


# ── Level 2 ───────────────────────────────────────────────────────────────

def check_level2(root: str, res: Result) -> None:
    res.checked.append('L2 entry page, api.md, AGENTS.md, CODEOWNERS, fitness contract')

    if not _exists(root, 'AGENTS.md'):
        res.add(2, 'L2', 'AGENTS.md', 'missing (§5.6: the agent-facing entry point).')
    if not _exists(root, 'docs/index.md'):
        res.add(2, 'L2', 'docs/index.md',
                'missing. SHOULD exist at Level 2 — the hand-authored entry page for a '
                'component repo (§6).')

    has_contract = _exists(root, 'openapi/openapi.yaml') or _exists(root, 'openapi.yaml')
    if has_contract and not _exists(root, 'docs/api.md'):
        res.add(2, 'L2', 'docs/api.md',
                'MUST exist for every service with a contract (§6 Level 2, §5.1): the '
                'markdown page that links or embeds the OpenAPI contract.')

    if not any(_exists(root, p) for p in
               ('CODEOWNERS', '.github/CODEOWNERS', 'docs/CODEOWNERS')):
        res.add(2, 'L2', 'CODEOWNERS', 'missing — Level 2 wants CODEOWNERS on docs paths (§6).')

    # §3.4: the fitness pillar's contract is `scripts/` + `run_all.sh`. Only
    # binding when the repo HAS fitness scripts; a repo with none is not
    # obliged to invent them.
    scripts = [p for p in _walk(root, 'scripts') if p.endswith('.sh')] if _isdir(root, 'scripts') else []
    checks = [p for p in scripts if os.path.basename(p).startswith('check_')]
    if checks and not _exists(root, 'scripts/run_all.sh'):
        res.add(2, 'L2', 'scripts/run_all.sh',
                f'{len(checks)} `check_*.sh` fitness script(s) but no runner — §3.4\'s contract '
                f'is `scripts/` plus `run_all.sh`.')


# ── Level 3 ───────────────────────────────────────────────────────────────

def check_level3(root: str, decl: dict, res: Result) -> None:
    res.checked.append('L3 declared pillar bindings')
    if not decl.get('_present'):
        res.add(3, 'L3', '.github/docs-standard.yml',
                'REQUIRED at Level 3 (§5.7): pillar bindings declared in git, not only in a '
                'platform UI.')
        return
    if 'pillars' not in _read(root, '.github/docs-standard.yml'):
        res.add(3, 'L3', '.github/docs-standard.yml',
                'declares no `pillars:` — at Level 3 the bindings are REQUIRED here as well '
                'as in Repository roles (§5.7).')
    if 'aliases' not in _read(root, '.github/docs-standard.yml'):
        res.add(3, 'L3', '.github/docs-standard.yml',
                'declares no `aliases:` — REQUIRED at Level 3, including maintenance (§5.7): '
                'it is how a renamed repo\'s old references still resolve (§5.4).')


# ── the declaration itself ────────────────────────────────────────────────

def check_declaration(root: str, decl: dict, res: Result) -> None:
    if not decl.get('_present'):
        # OPTIONAL at L1, RECOMMENDED at L2, REQUIRED at L3 (checked there).
        res.skipped.append('declaration (.github/docs-standard.yml absent — OPTIONAL below Level 3)')
        return
    res.checked.append('declaration .github/docs-standard.yml')
    version = decl.get('standard', '')
    if version and version != STANDARD_VERSION:
        res.add(1, 'decl', '.github/docs-standard.yml',
                f'targets standard {version!r}; this checker implements {STANDARD_VERSION!r}.')
    level = decl.get('level', '')
    if level and level not in ('0', '1', '2', '3'):
        res.add(1, 'decl', '.github/docs-standard.yml',
                f'level {level!r} is not one of 0–3 (§6).')


# ── driver ────────────────────────────────────────────────────────────────

def run(root: str, level: int | None) -> Result:
    decl = read_declaration(root)
    declared = decl.get('level', '')
    target = level if level is not None else (int(declared) if declared.isdigit() else 1)
    res = Result(target=target)

    check_declaration(root, decl, res)
    if target == 0:
        # §6 Level 0 is Adopting: a declared, valid state with a migration
        # plan. The declaration is the whole of the obligation.
        res.skipped.append('Level 0 (Adopting) — only the declaration is checked')
        if not decl.get('_present'):
            res.add(0, 'L0', '.github/docs-standard.yml',
                    'Level 0 is a DECLARED state (§6) — without this file the repo is not '
                    'adopting, it is silently non-conformant.')
        return res

    check_readme(root, res)
    check_adrs(root, res)
    check_change_specs(root, res)
    check_api_contract(root, res)
    check_requirements(root, res)
    check_design_architecture_split(root, res)
    check_level2(root, res)
    check_level3(root, decl, res)
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Documentation Standard conformance checker (§6).')
    ap.add_argument('--root', default='.', help='repo root to check')
    ap.add_argument('--level', type=int, choices=[0, 1, 2, 3], default=None,
                    help='target level; default: read from .github/docs-standard.yml, else 1')
    ap.add_argument('--json-out', default='', help='write a JSON report here')
    ap.add_argument('--list-unchecked', action='store_true',
                    help='print the rules this script deliberately does not judge, and exit')
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass

    if args.list_unchecked:
        print('Rules the checker does NOT judge (§5.7 is checkable from paths and marker')
        print('lines; these need judgement, history or the network):')
        for rule, why in UNCHECKED:
            print(f'  {rule:<5} {why}')
        return 0

    res = run(args.root, args.level)
    print(f'Documentation Standard {STANDARD_VERSION} — conformance check, target Level {res.target}')
    print()

    for finding in res.binding():
        print(f'::error file={finding.path}::[{finding.rule}] {finding.message}')

    print('-- summary --------------------------------')
    for name in res.checked:
        print(f'  ran     {name}')
    for name in res.skipped:
        print(f'  skipped {name}')

    upcoming = res.next_level()
    if upcoming:
        print()
        print(f'  {len(upcoming)} finding(s) above Level {res.target} — what the next level would cost:')
        for finding in upcoming:
            print(f'    L{finding.level} {finding.path}: {finding.message.splitlines()[0]}')

    binding = res.binding()
    print()
    if binding:
        print(f'{len(binding)} conformance defect(s) at Level {res.target}:')
        for finding in binding:
            print(f'  FAIL [{finding.rule}] {finding.path}: {finding.message}')
    else:
        print(f'PASS: conformant at Level {res.target}')
    print()
    print('Not judged here (see --list-unchecked): wiki content, catalog tables, '
          'acceptance-criteria style, graduation practice, cross-reference qualification.')

    if args.json_out:
        with open(args.json_out, 'w', encoding='utf-8') as fh:
            json.dump({
                'standard': STANDARD_VERSION,
                'target': res.target,
                'pass': not binding,
                'findings': [vars(f) for f in res.findings],
                'checked': res.checked,
                'skipped': res.skipped,
                'unchecked': [{'rule': r, 'why': w} for r, w in UNCHECKED],
            }, fh, indent=2)

    return 1 if binding else 0


if __name__ == '__main__':
    sys.exit(main())
