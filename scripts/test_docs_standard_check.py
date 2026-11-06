"""Tests for the Documentation Standard conformance checker.

Each test builds a tiny repo on disk and asserts the verdict, so the §6
checklists are pinned by behaviour rather than by reading regexes.

Two properties matter more than coverage here, and most of these tests exist
to hold one of them:

  * **An absent pillar is conformant.** §2.6 — "folders exist when they have
    content; empty skeletons SHOULD NOT be scaffolded". A checker that scolds a
    one-repo team for having no ADRs teaches them to scaffold empty folders,
    which is the thing the standard forbids.
  * **A finding binds at the level it belongs to.** Level 2 advice reported as
    a Level 1 failure makes the tool a liar about what conformance costs.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import docs_standard_check as chk  # noqa: E402


def write(root: Path, rel: str, text: str = 'x\n') -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding='utf-8')


ADR_BODY = """# ADR-001: Record architecture decisions
**Date:** 2026-01-01
**Status:** Accepted
**Deciders:** someone

## Context
## Decision
"""


def minimal(root: Path) -> None:
    """The smallest repo §6 calls Level 1: a README and nothing else."""
    write(root, 'README.md', '# repo\n')


def run(root: Path, level=None) -> chk.Result:
    return chk.run(str(root), level)


def rules(res: chk.Result, level: int | None = None) -> list[str]:
    findings = res.binding() if level is None else [f for f in res.findings if f.level == level]
    return [f'{f.rule}:{f.path}' for f in findings]


# ── the floor ─────────────────────────────────────────────────────────────

def test_a_readme_alone_is_level_1(tmp_path):
    # §6 "Adopt in 15 minutes": everything else arrives when its content does.
    minimal(tmp_path)
    assert rules(run(tmp_path)) == []


def test_a_repo_with_no_readme_fails(tmp_path):
    assert 'L1.1:README.md' in rules(run(tmp_path))


def test_an_absent_pillar_is_skipped_not_failed(tmp_path):
    minimal(tmp_path)
    res = run(tmp_path)
    joined = ' '.join(res.skipped)
    for pillar in ('ADRs', 'change specs', 'API contract'):
        assert pillar in joined, f'{pillar} should be skipped, not judged'


# ── ADRs (L1.3) ───────────────────────────────────────────────────────────

def test_a_conformant_adr_passes(tmp_path):
    minimal(tmp_path)
    write(tmp_path, 'docs/design/adr/ADR-001-record-architecture-decisions.md', ADR_BODY)
    assert rules(run(tmp_path)) == []


def test_the_grandfathered_adr_directory_passes(tmp_path):
    # §3.2 permits docs/adr/ permanently for a pre-existing sequence.
    minimal(tmp_path)
    write(tmp_path, 'docs/adr/ADR-001-record-architecture-decisions.md', ADR_BODY)
    assert rules(run(tmp_path)) == []


def test_two_adr_directories_fail(tmp_path):
    # "A repo MUST use exactly one of the two — never both."
    minimal(tmp_path)
    write(tmp_path, 'docs/design/adr/ADR-001-a.md', ADR_BODY)
    write(tmp_path, 'docs/adr/ADR-002-b.md', ADR_BODY.replace('ADR-001', 'ADR-002'))
    assert any('L1.3:docs/adr' in r for r in rules(run(tmp_path)))


def test_a_badly_named_adr_fails(tmp_path):
    minimal(tmp_path)
    write(tmp_path, 'docs/adr/decision-about-auth.md', ADR_BODY)
    assert 'L1.3:docs/adr/decision-about-auth.md' in rules(run(tmp_path))


def test_a_reused_adr_number_fails(tmp_path):
    # §3.2: numbers are never reused — a citation graph depends on it.
    minimal(tmp_path)
    write(tmp_path, 'docs/adr/ADR-001-first.md', ADR_BODY)
    write(tmp_path, 'docs/adr/ADR-001-second.md', ADR_BODY)
    assert 'L1.3:docs/adr/ADR-001-second.md' in rules(run(tmp_path))


def test_a_missing_status_or_date_fails(tmp_path):
    minimal(tmp_path)
    write(tmp_path, 'docs/adr/ADR-001-x.md', '# ADR-001: X\n\n## Context\n')
    found = rules(run(tmp_path))
    assert found.count('L1.3:docs/adr/ADR-001-x.md') >= 2  # date AND status


def test_a_status_outside_the_lifecycle_fails(tmp_path):
    minimal(tmp_path)
    write(tmp_path, 'docs/adr/ADR-001-x.md', ADR_BODY.replace('Accepted', 'Kind of agreed'))
    assert 'L1.3:docs/adr/ADR-001-x.md' in rules(run(tmp_path))


def test_a_status_carrying_a_payload_passes(tmp_path):
    # "Superseded by ADR-NNN" and "Deprecated (date: reason)" carry text after
    # the keyword; prefix-matching is why they are accepted.
    minimal(tmp_path)
    write(tmp_path, 'docs/adr/ADR-001-x.md',
          ADR_BODY.replace('Accepted', 'Deprecated (2026-02-02: component deleted)'))
    write(tmp_path, 'docs/adr/ADR-002-y.md',
          ADR_BODY.replace('ADR-001', 'ADR-002').replace('Accepted', 'Superseded by ADR-003'))
    assert rules(run(tmp_path)) == []


def test_a_one_ended_supersede_chain_is_a_level_2_finding(tmp_path):
    # §3.2: "links MUST be bidirectional". ADR-002 exists and never claims to
    # supersede ADR-001 — the chain is nailed down at one end only.
    minimal(tmp_path)
    write(tmp_path, 'docs/adr/ADR-001-old.md',
          ADR_BODY.replace('Accepted', 'Superseded by ADR-002'))
    write(tmp_path, 'docs/adr/ADR-002-new.md', ADR_BODY.replace('ADR-001', 'ADR-002'))
    assert 'L2:docs/adr/ADR-001-old.md' in rules(run(tmp_path), level=2)


def test_a_bidirectional_supersede_chain_passes(tmp_path):
    minimal(tmp_path)
    write(tmp_path, 'docs/adr/ADR-001-old.md',
          ADR_BODY.replace('Accepted', 'Superseded by ADR-002'))
    write(tmp_path, 'docs/adr/ADR-002-new.md',
          ADR_BODY.replace('ADR-001', 'ADR-002') + '\nSupersedes ADR-001.\n')
    # Filtered to the ADR: Level 2 also wants AGENTS.md / docs/index.md /
    # CODEOWNERS, which this fixture has not got and this test is not about.
    assert 'L2:docs/adr/ADR-001-old.md' not in rules(run(tmp_path), level=2)


def test_a_successor_in_another_repo_is_not_flagged(tmp_path):
    # A cross-repo successor is §5.4's business, not this check's — flagging it
    # would punish a correct repo-qualified reference.
    minimal(tmp_path)
    write(tmp_path, 'docs/adr/ADR-001-old.md',
          ADR_BODY.replace('Accepted', 'Superseded by ADR-099'))
    assert 'L2:docs/adr/ADR-001-old.md' not in rules(run(tmp_path), level=2)


def test_design_architecture_is_refused(tmp_path):
    # §3.2: content there silently loses its typing — silence is why it is checked.
    minimal(tmp_path)
    write(tmp_path, 'docs/design/architecture/overview.md')
    assert 'L1.3:docs/design/architecture' in rules(run(tmp_path))


# ── change specs (L1.4) ───────────────────────────────────────────────────

def test_a_three_artifact_change_passes(tmp_path):
    minimal(tmp_path)
    write(tmp_path, 'specs/changes/add-two-factor-auth/proposal.md', '# why\n')
    write(tmp_path, 'specs/changes/add-two-factor-auth/specs/two-factor-login.md',
          'The system **MUST** ask for a second factor.\n')
    assert rules(run(tmp_path)) == []


def test_the_single_file_form_passes(tmp_path):
    minimal(tmp_path)
    write(tmp_path, 'specs/changes/fix-session-expiry/spec.md',
          'Sessions **MUST** expire after 30 minutes.\n')
    assert rules(run(tmp_path)) == []


def test_a_change_folder_with_neither_file_fails(tmp_path):
    minimal(tmp_path)
    write(tmp_path, 'specs/changes/add-thing/notes.md')
    assert 'L1.4:specs/changes/add-thing' in rules(run(tmp_path))


def test_a_numbered_change_slug_fails(tmp_path):
    # §3.3: number-based ids break when the Specs host is not the code repo.
    minimal(tmp_path)
    write(tmp_path, 'specs/changes/123-add-thing/spec.md', 'It **MUST** work.\n')
    assert 'L1.4:specs/changes/123-add-thing' in rules(run(tmp_path))


def test_a_requirement_file_with_no_rfc2119_keyword_fails(tmp_path):
    # Without one it is prose, not a requirement anything can be checked against.
    minimal(tmp_path)
    write(tmp_path, 'specs/changes/add-thing/spec.md', 'The system should probably do this.\n')
    assert 'L1.4:specs/changes/add-thing/spec.md' in rules(run(tmp_path))


def test_an_unbolded_keyword_does_not_count(tmp_path):
    # §2.4 and Appendix A: the keywords are BOLD. Plain "MUST" in prose is
    # exactly the ambiguity the convention exists to remove.
    minimal(tmp_path)
    write(tmp_path, 'specs/changes/add-thing/spec.md', 'The system MUST do this.\n')
    assert 'L1.4:specs/changes/add-thing/spec.md' in rules(run(tmp_path))


# ── contracts and requirements ────────────────────────────────────────────

def test_a_contract_under_docs_fails(tmp_path):
    # §5.1: the contract is NOT under docs/.
    minimal(tmp_path)
    write(tmp_path, 'docs/openapi.yaml', 'openapi: 3.0.0\n')
    assert 'L1.5:docs/openapi.yaml' in rules(run(tmp_path))


def test_both_permitted_contract_locations_pass(tmp_path):
    minimal(tmp_path)
    write(tmp_path, 'openapi/openapi.yaml', 'openapi: 3.0.0\n')
    assert rules(run(tmp_path)) == []


def test_a_requirement_folder_without_specs_fails(tmp_path):
    minimal(tmp_path)
    write(tmp_path, 'docs/requirements/Authentication/overview.md')
    assert 'L1.6:docs/requirements/Authentication' in rules(run(tmp_path))


def test_an_empty_specs_folder_is_clutter(tmp_path):
    minimal(tmp_path)
    (tmp_path / 'docs/requirements/Authentication/specs').mkdir(parents=True)
    assert 'L1.6:docs/requirements/Authentication/specs' in rules(run(tmp_path))


# ── levels ────────────────────────────────────────────────────────────────

def test_level_2_advice_does_not_fail_a_level_1_repo(tmp_path):
    # The property that keeps the tool honest about what conformance costs.
    minimal(tmp_path)
    res = run(tmp_path, level=1)
    assert rules(res) == []
    assert res.next_level(), 'Level 2 gaps should still be REPORTED, just not binding'


def test_level_2_binds_what_level_1_only_noted(tmp_path):
    minimal(tmp_path)
    found = rules(run(tmp_path, level=2))
    assert 'L2:AGENTS.md' in found
    assert 'L2:docs/index.md' in found
    assert 'L2:CODEOWNERS' in found


def test_level_2_requires_api_md_only_when_a_contract_exists(tmp_path):
    minimal(tmp_path)
    assert 'L2:docs/api.md' not in rules(run(tmp_path, level=2))
    write(tmp_path, 'openapi/openapi.yaml', 'openapi: 3.0.0\n')
    assert 'L2:docs/api.md' in rules(run(tmp_path, level=2))


def test_fitness_scripts_without_a_runner_fail_at_level_2(tmp_path):
    minimal(tmp_path)
    write(tmp_path, 'scripts/check_layering.sh', '#!/bin/sh\n')
    assert 'L2:scripts/run_all.sh' in rules(run(tmp_path, level=2))


def test_a_repo_with_no_fitness_scripts_is_not_asked_for_a_runner(tmp_path):
    minimal(tmp_path)
    assert 'L2:scripts/run_all.sh' not in rules(run(tmp_path, level=2))


def test_level_3_requires_the_declaration_with_pillars_and_aliases(tmp_path):
    minimal(tmp_path)
    assert '.github/docs-standard.yml' in ' '.join(rules(run(tmp_path, level=3)))

    write(tmp_path, '.github/docs-standard.yml',
          'standard: "1.0"\nlevel: 3\npillars:\n  design: host\naliases:\n  - old/name\n')
    found = rules(run(tmp_path, level=3))
    assert not [r for r in found if 'docs-standard.yml' in r]


def test_level_0_is_a_declared_state(tmp_path):
    # §6: Level 0 is "a valid, declared state — not silent non-conformance",
    # so an adopting repo is judged on the declaration and nothing else.
    write(tmp_path, '.github/docs-standard.yml', 'standard: "1.0"\nlevel: 0\n')
    assert rules(run(tmp_path)) == []          # no README yet, and that is fine
    assert run(tmp_path).target == 0           # target read from the declaration


def test_level_0_without_a_declaration_is_not_adopting(tmp_path):
    assert 'L0:.github/docs-standard.yml' in rules(run(tmp_path, level=0))


def test_the_declared_level_is_the_default_target(tmp_path):
    minimal(tmp_path)
    write(tmp_path, '.github/docs-standard.yml', 'standard: "1.0"\nlevel: 2\n')
    assert run(tmp_path).target == 2
    assert 'L2:AGENTS.md' in rules(run(tmp_path))


def test_an_explicit_level_overrides_the_declaration(tmp_path):
    minimal(tmp_path)
    write(tmp_path, '.github/docs-standard.yml', 'standard: "1.0"\nlevel: 2\n')
    assert run(tmp_path, level=1).target == 1


def test_a_wrong_standard_version_is_flagged(tmp_path):
    minimal(tmp_path)
    write(tmp_path, '.github/docs-standard.yml', 'standard: "0.9"\nlevel: 1\n')
    assert 'decl:.github/docs-standard.yml' in rules(run(tmp_path))


# ── the host repo itself ──────────────────────────────────────────────────

def test_the_standards_own_repo_is_conformant():
    """§5.7: the standard "eats its own dogfood". A checker whose own host
    repo fails it is a checker nobody will believe."""
    root = Path(__file__).resolve().parent.parent
    res = chk.run(str(root), None)
    assert res.binding() == [], [f'{f.rule} {f.path}: {f.message}' for f in res.binding()]
