# UMAT-OTI Standalone Package

This directory is a standalone source bundle that contains the full UMAT-OTI
transformation runtime plus the files a new user needs to try the workflow on
their own UMAT.

It is meant to be copied, zipped, or shared as its own folder.

The bundled compact-JSON workflow has been smoke-tested inside this directory,
and the completed 19-case benchmark set transforms successfully from here.

Start with `START_HERE.md` if you are a new user.

## What Is Included

- `src/umat_oti/`: the full transformation runtime code.
- `app.py`: Streamlit GUI entry point.
- `START_HERE.md`: shortest instructions for a new user.
- `templates/`: root-level JSON templates to copy and edit.
- `examples/`: root-level example JSON files that already work.
- `user_jsons/`: suggested place to keep your own JSON contracts.
- `UMATs/`: bundled sample UMAT source files used by the example configs.
- `json_files_completed/`: completed compact JSON contracts in the current simple format.
- `json_files/`: general JSON-contract directory for user-authored configs.
- `new_user_umat_starter/`: starter docs, templates, examples, and helper scripts.
- `tools/run_completed_json_batch.py`: batch transform runner.
- `vendor/_otilib_upstream/`: bundled pyoti template files used to generate complete OTI Fortran modules inside the standalone package.

## Intended Usage Model

Use this bundle from source.

That means:

1. copy or clone this folder to a machine,
2. change into this folder,
3. optionally install it in editable mode,
4. run the GUI, starter scripts, or CLI from this folder.

The runtime currently relies on this source-tree layout for paths such as
`json_files_completed/`, `UMATs/`, and `umat_oti_workspace/`.

## Install

From this directory:

```bash
python3 -m pip install -e .
```

On ARC, use `/usr/bin/python3.12` instead of the default `python3`.

You do not need to install the package just to run the bundled starter scripts.
They work directly from this folder.

## Fastest Way For A New User

Use the root-level files first:

1. Read `START_HERE.md`.
2. Copy a JSON template from `templates/` into `user_jsons/` and edit it.
3. Edit the settings at the top of `run_json_pipeline.py`.
4. Run `/usr/bin/python3.12 run_json_pipeline.py`.

The runner defaults to `examples/`, so if you run it without edits it executes
the bundled known-good example JSONs first.

`run_json_pipeline.py` now defaults to `RUN_VALIDATION = False`, so a new user
can test the transformation path without Abaqus.

Set `RUN_VALIDATION = True` only on a machine where Abaqus is installed and the
module/load environment is already configured.

If you want one single JSON from the command line instead, use:

```bash
/usr/bin/python3.12 transform_from_json.py user_jsons/my_new_umat.json --out umat_oti_workspace/my_new_umat
```

If you want the deeper helper scripts and reference material, see `new_user_umat_starter/README.md`.

## GUI

Run the GUI from this directory with:

```bash
streamlit run app.py
```

The GUI can load bundled contracts from `json_files_completed/` or an uploaded
JSON file.

## CLI

The package exposes:

```bash
umat-oti transform path/to/umat.for --out generated/case_name
umat-oti-config --config json_files/my_new_umat.json --out umat_oti_workspace/my_new_umat
umat-oti-batch --config-dir json_files_completed --batch-dir umat_oti_workspace/completed_json_batch
```

Without installation, the same JSON-contract path is:

```bash
PYTHONPATH=src /usr/bin/python3.12 -m umat_oti.cli_json --config json_files/my_new_umat.json --out umat_oti_workspace/my_new_umat
```

The top-level wrapper script above is just a simpler front door for the same transformation path.

You can also run the bundled script directly:

```bash
/usr/bin/python3.12 tools/run_completed_json_batch.py --config-dir json_files_completed --batch-dir umat_oti_workspace/completed_json_batch
```

## Recommended First Files To Read

- `new_user_umat_starter/README.md`
- `new_user_umat_starter/JSON_REFERENCE.md`
- `new_user_umat_starter/TROUBLESHOOTING.md`
- `new_user_umat_starter/examples/elastic_minimal.json`

## Known Limits

- The compact JSON layer is working and the completed config set is in the simple top-level format.
- The standalone bundle transforms the current 19 completed benchmark configs successfully.
- The standalone no-argument runner now also supports original-vs-transformed validation using the bundled pyoti templates and Abaqus validation pipeline.
- The bundle now includes the pyoti template files needed for complete OTI module generation, so the minimal-template fallback warning should not appear in normal runs.