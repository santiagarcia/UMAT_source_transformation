# Troubleshooting

## Config says the source could not be resolved

- Use an absolute `source` path.
- If you want a relative path, keep the JSON file on disk and run `check_config.py --config path/to/file.json` so the loader can resolve relative to that file.
- Browser uploads are safest with absolute `source` paths.

## Config says required fields are missing

The compact contract requires all of these:

- `name`
- `source`
- `jacobian.seed`
- `jacobian.output`
- `jacobian.target`
- `promote`
- `constant`
- `real`
- `replace`
- `ntens`
- `order`

## Config says a variable has multiple roles

- A variable cannot appear in more than one of `promote`, `constant`, or `real`.
- Remove the overlap and run `check_config.py` again.

## Anchor status says `needs_json_completion`

- The JSON loaded, but the transform still needs more explicit completion data.
- Start by checking the `replace` line ranges.
- Make sure the old DDSDDE block is fully covered and only that block is covered.

## I do not know what to put in `replace`

- Run `show_source_lines.py` on the source file.
- Find the old DDSDDE assignment block.
- Add inclusive 1-based line ranges like `"83-87"`.

## No UMAT routine was detected

- Make sure the source file actually contains the entry routine you want.
- If the entry routine is not named `UMAT`, set the optional top-level `umat` field explicitly.

## `run_from_json.py` says the transform was not successful

- Inspect the `warnings`, `blockers`, `report_path`, and `semantic_checks` in the printed JSON summary.
- The compact JSON may still be correct. The current transform pipeline has known semantic-check failures on benchmark UMATs.
- Compare your case against `examples/elastic_minimal.json` and `examples/hin_reference.json`.

## The GUI loads the JSON but the transform still fails

- That is consistent with the current repository state.
- The JSON/config layer is working more reliably than the transform backend right now.

## I need advanced constitutive Jacobians

- Start from `templates/new_umat_constitutive_template.json`.
- Keep the main tangent contract working first.
- Add `constitutive_jacobians` and `helper_surfaces` only after the base contract is stable.# Troubleshooting

## `source` Could Not Be Resolved

Symptom:

- The loader says it could not resolve the UMAT source path.

Fix:

- Use an absolute path in `source`.
- If you use a relative path, make sure it is relative to the JSON file location,
  not just your current shell directory.
- Run `scripts/check_config.py` again after fixing the path.

## Missing Explicit User Contract Fields

Symptom:

- The loader says the compact configuration must define fields such as
  `name`, `source`, `jacobian.target`, `replace`, `ntens`, or `order`.

Fix:

- Start from `templates/new_umat_minimal_template.json`.
- Fill every required field before using the GUI or runner.

## Variable In Multiple Roles

Symptom:

- The loader reports that a variable has multiple roles.

Fix:

- Remove duplicates across `promote`, `constant`, and `real`.
- Each variable should be listed in only one of those role lists.

## `needs_json_completion`

Symptom:

- `scripts/check_config.py` reports an anchor status of `needs_json_completion`.

Meaning:

- The source loaded, but the contract still does not identify enough of the
  transform surface for the current pipeline to proceed cleanly.

Fix:

- Recheck the `replace` line ranges.
- If your file contains multiple routines, set `umat` explicitly.
- Compare against `examples/elastic_minimal.json` and the completed configs
  under `json_files_completed/`.

## Transform Returned Warnings Or Failed Semantic Checks

Symptom:

- `scripts/run_from_json.py` finishes with warnings, blockers, or
  `transform_success: false`.

Meaning:

- This does not automatically mean your JSON is wrong.
- Some UMATs in the current prototype still fail downstream semantic checks even
  with valid compact JSON contracts.

Fix:

- Read the transform report path printed by `scripts/run_from_json.py`.
- Compare against working examples.
- If the config loads cleanly but the transform still fails, the limitation may
  be in the transform pipeline rather than the JSON.

## Relative Path Works In One Place But Not Another

Symptom:

- A relative `source` works from the helper scripts but not when uploading the
  same JSON through the browser.

Meaning:

- Browser-uploaded JSON may not preserve a meaningful on-disk origin path.

Fix:

- Prefer absolute `source` paths when loading JSON from the browser.
- Relative paths are safest for server-side JSON files already stored in the repo.

## I Need More Than The Minimal Contract

If the main `DDSDDE = d STRESS / d DSTRAN` contract is not enough:

- start from `templates/new_umat_advanced_template.json`,
- add `constitutive_jacobians` only where needed,
- add `helper_surfaces` only when helper-call data must be declared explicitly.

Keep the first pass minimal whenever possible.