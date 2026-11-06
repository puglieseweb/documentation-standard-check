# Documentation Standard checker

The reference conformance checker for the [SystemDox Documentation Standard](https://www.systemdox.com/guides/documentation-standard), version 1.0: how software documentation is structured, named and evolved inside Git repositories. §5.7 of the standard says its host repository publishes a dependency-free checker implementing the §6 level checklists. This is that checker.

It reads a checkout and judges only what paths, filename grammar and marker lines can show: the README, the ADR directory, filenames, numbers, lifecycle and supersede links, change-spec folders and their RFC 2119 keywords, where the API contract lives, requirement folders, and the Level 2 and Level 3 files. It runs nothing, fetches nothing and parses no Markdown beyond marker lines. Rules that need judgement are listed by `--list-unchecked`, so a green run never reads as more assurance than it is.

## Run it

Python 3, standard library only. Tested on Python 3.12.

```sh
python scripts/docs_standard_check.py --root /path/to/repo
python scripts/docs_standard_check.py --root . --level 2 --json-out report.json
python scripts/docs_standard_check.py --list-unchecked
```

The target level comes from `level:` in the repository's `.github/docs-standard.yml`, or `--level`, and defaults to 1, the level every repository meets. A finding binds at its own level and above. Findings above the target are printed as what the next level would cost. The exit code is non-zero when anything binds at the target level.

An absent pillar is conformant. A repository with no ADRs passes the ADR rules by having none, because the standard says folders exist when they have content.

## In CI

```yaml
- name: Documentation Standard
  run: |
    curl -fsSLo docs_standard_check.py \
      https://raw.githubusercontent.com/puglieseweb/documentation-standard-check/<commit>/scripts/docs_standard_check.py
    python3 docs_standard_check.py --root .
```

Pin `<commit>` to a commit of this repository rather than a branch, so that a new rule never turns your pipeline red unannounced.

## Test it

```sh
python -m pip install pytest
python -m pytest scripts -q
```

The last test checks this repository against the standard.

## Source and licence

This is a read-only mirror. The checker is developed beside the standard in the standard's host repository, and every change that reaches its main branch is copied here as one commit. Issues are welcome; a pull request here is read, and any change it proposes is made at the source.

ISC licence, © 2026 PuglieseWeb LTD. SystemDox, which PuglieseWeb builds, reads repositories laid out to this standard. Nothing here needs it.
