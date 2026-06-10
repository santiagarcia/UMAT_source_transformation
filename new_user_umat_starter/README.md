# New User UMAT Starter

This folder is a self-contained starter pack for running the current UMAT-OTI workflow on a new UMAT.

It is built around the current simple JSON contract, not the older expanded internal schema.

## What Is In This Folder

- `README.md`: quickest path for a new user.
- `JSON_REFERENCE.md`: field-by-field explanation of the compact JSON contract.
- `TROUBLESHOOTING.md`: common failure modes and what to check next.
- `examples/elastic_minimal.json`: smallest real loadable example in this repository.
- `examples/hin_reference.json`: larger real loadable example.
- `templates/new_umat_minimal_template.json`: minimal contract to copy and edit.
- `templates/new_umat_constitutive_template.json`: advanced template for constitutive jacobians and helper surfaces.
- `scripts/scan_source.py`: inspect a raw UMAT source before writing JSON.
- `scripts/show_source_lines.py`: print source with line numbers so you can choose `replace` ranges.
- `scripts/check_config.py`: load-check a compact JSON contract and optionally write the expanded config.
- `scripts/run_from_json.py`: run the single-config transform path without using the batch driver.

## Minimal Workflow

Run the commands below from the repository root.

Use `/usr/bin/python3.12` on ARC or any machine where the default `python` is older than 3.10.

1. Inspect the UMAT source.

```bash
/usr/bin/python3.12 new_user_umat_starter/scripts/scan_source.py /absolute/path/to/YOUR_UMAT.for
/usr/bin/python3.12 new_user_umat_starter/scripts/show_source_lines.py /absolute/path/to/YOUR_UMAT.for --start 1 --end 220
```

2. Copy the minimal template and edit it.

```bash
cp new_user_umat_starter/templates/new_umat_minimal_template.json json_files/my_new_umat.json
```

3. Fill in at least these fields.

```text
name
source
jacobian.seed
jacobian.output
jacobian.target
promote
constant
real
replace
ntens
order
```

4. Load-check the JSON contract.

```bash
/usr/bin/python3.12 new_user_umat_starter/scripts/check_config.py --config json_files/my_new_umat.json
```

5. Optionally write the expanded internal config for inspection.

```bash
/usr/bin/python3.12 new_user_umat_starter/scripts/check_config.py \
  --config json_files/my_new_umat.json \
  --write-expanded umat_oti_workspace/my_new_umat.expanded.json
```

6. Attempt the transform on that one config.

```bash
/usr/bin/python3.12 new_user_umat_starter/scripts/run_from_json.py \
  --config json_files/my_new_umat.json \
  --out umat_oti_workspace/my_new_umat
```

If you want the shortest command from the bundle root, use:

```bash
/usr/bin/python3.12 transform_from_json.py json_files/my_new_umat.json --out umat_oti_workspace/my_new_umat
```

If you do not want command-line arguments at all, edit `JSON_INPUT_PATH` and
`OUTPUT_DIRECTORY` at the top of `run_json_pipeline.py`, then run:

```bash
/usr/bin/python3.12 run_json_pipeline.py
```

That script only consumes user-authored JSON files. It does not generate JSON
for the user. By default it also runs the validation/compare stage after
transformation so the transformed UMAT is checked against the original UMAT.

If you install the bundle with `python3 -m pip install -e .`, the same path is available as:

```bash
umat-oti-config --config json_files/my_new_umat.json --out umat_oti_workspace/my_new_umat
```

7. If you prefer the GUI, run Streamlit and load the same JSON file.

```bash
streamlit run app.py
```

If you upload JSON through the browser, use an absolute `source` path on the local machine running the app. Relative paths are safest when the JSON is loaded directly from disk on that same machine.

## Which File To Start From

- Start from `templates/new_umat_minimal_template.json` for a standard tangent-only UMAT.
- Start from `templates/new_umat_constitutive_template.json` only if you also need advanced `constitutive_jacobians` or `helper_surfaces` sections.
- Use `examples/elastic_minimal.json` as the smallest real reference.
- Use `examples/hin_reference.json` as a larger real reference.

## What Each Script Is For

`scan_source.py`

- Prints detected UMAT routines, candidate regions, and a compact source summary.

`show_source_lines.py`

- Prints a source range with 1-based line numbers.
- Use it to identify the old DDSDDE block for the `replace` list.

`check_config.py`

- Confirms the compact JSON can be loaded.
- Resolves the source path.
- Applies completed anchors when possible.
- Prints anchor completion status and key transform settings.

`run_from_json.py`

- Runs the same single-config transform path used by the current batch tooling.
- Writes the generated source and transform report into the chosen output directory.

## Known Limits

- The compact JSON layer is working and the completed config set has been rewritten to this simple format.
- The standalone bundle successfully transforms the current 19 completed benchmark configs.
- A transform warning about minimal local pyoti templates is expected in the standalone bundle and is not itself a failure.

## First Files To Read

- `new_user_umat_starter/JSON_REFERENCE.md`
- `new_user_umat_starter/TROUBLESHOOTING.md`
- `new_user_umat_starter/examples/elastic_minimal.json`
