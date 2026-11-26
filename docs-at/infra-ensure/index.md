# Infra-Ensure

This repository is a soft-fork of [PyPSA-AT](https://github.com/aggm-ag/pypsa-at)
for the Infra-Ensure project. Infra-Ensure adds industry elements and the
coupling of PyPSA-AT with other models. Everything else in these docs describes
PyPSA-AT itself.

## Layers

| Layer          | Owner                           | Rule in this fork                                   |
|----------------|---------------------------------|-----------------------------------------------------|
| PyPSA-Eur / DE | upstream of PyPSA-AT            | never changed, except registered touch points       |
| PyPSA-AT       | AGGM, merged from `upstream/main` | never changed, except registered touch points     |
| Infra-Ensure   | this fork                       | own folders, edit freely                            |

## Infra-Ensure folders

| Path                         | Purpose                                                   |
|------------------------------|-----------------------------------------------------------|
| `infra_ensure/`              | Python package with the business logic (like `mods/`)     |
| `scripts/infra-ensure/`      | Scripts, 1:1 with the rules                               |
| `rules/infra-ensure/`        | Snakemake rules, one file per phase like `rules/pypsa-at/` |
| `config/infra-ensure/`       | Configuration overlays and scenario files                 |
| `test/test_infra_ensure/`    | Tests, mirroring the `infra_ensure` package               |
| `docs-at/infra-ensure/`      | This part of the documentation                            |
| `CHANGELOG.infra-ensure.md`  | Changelog of the fork                                     |

The Python package is called `infra_ensure` because Python cannot import a
name with a hyphen. All other folders and files use `infra-ensure`.

## Touch points

Some Infra-Ensure changes have to go into PyPSA-AT files, e.g. including the
Infra-Ensure rules in the `Snakefile`. Every such file is registered in
`infra_ensure/touchpoints.yaml` with the reason and where the change should go
eventually (`none`, `pypsa-at`, `pypsa-de` or `pypsa-eur`). Every changed line
lies inside a marker block:

```python
# --- START: Block Maintained by Infra-Ensure ---
# Why the change is needed
...
# --- END: Block Maintained by Infra-Ensure ---
```

Changes that are useful for PyPSA-AT in general go to AGGM as a pull request
and leave the registry once they are merged upstream.

## Compatibility guard

```bash
pixi run check-at-compat                  # compare with upstream/main
pixi run check-at-compat --base <ref>     # compare with another ref
```

The guard compares the working tree with the merge base of `upstream/main`.
It fails if

- a file outside the Infra-Ensure folders changed and is not registered,
- a changed line of a registered file lies outside a marker block,
- markers are unbalanced, or a PyPSA-AT file was deleted.

Blank-line-only changes are ignored. Only tracked files are checked, so `git add`
new files before running the guard. Registered files that no longer differ from PyPSA-AT are reported as warnings.
The guard needs the `upstream` remote: `git remote add upstream https://github.com/AGGM-AG/pypsa-at.git`.
