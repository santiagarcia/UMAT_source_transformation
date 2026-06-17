# Start Here

This standalone package runs.

It has already been tested in this folder with:

- `examples/elastic_minimal.json`
- `examples/hin_reference.json`

Both passed transformation and original-vs-transformed validation.

## Fastest User Path

1. Put your UMAT source file somewhere accessible on this machine.

2. Copy one JSON template from `templates/` into `user_jsons/` and edit it.

Example:

```bash
cp templates/new_umat_minimal_template.json user_jsons/my_umat.json
```

3. Edit `user_jsons/my_umat.json`.

You must fill in your own:

- `source`
- `promote`
- `replace`
- `ntens`

`constant` and `real` are optional. If you leave them out, the tool infers
each remaining variable's role automatically. Add them only to override a
specific variable that the tool classified incorrectly.

4. Open `run_json_pipeline.py` and set:

```python
JSON_INPUT_PATH = ROOT / "user_jsons"
OUTPUT_DIRECTORY = ROOT / "umat_oti_workspace" / "my_run"
RUN_VALIDATION = False
```

Leave `RUN_VALIDATION = False` if you only want the transformed UMAT and
reports. Set it to `True` only on a machine with Abaqus configured.

5. Run the pipeline:

```bash
/usr/bin/python3.12 run_json_pipeline.py
```

## What You Get Back

For each JSON, the script writes:

- the transformed UMAT source
- the transform report
- a validation workspace
- the comparison report against the original UMAT

The overall run summary is written to:

- `umat_oti_workspace/.../pipeline_status.json`

Each case also gets its own:

- `transform_report.json`
- `validation/comparison_report.json`
- `validation/validation_report.json`

## Files You Should Look At

- `run_json_pipeline.py`
- `templates/new_umat_minimal_template.json`
- `templates/new_umat_constitutive_template.json`
- `examples/elastic_minimal.json`
- `user_jsons/`

## Important Rule

This package does not create JSON configs for the user.

The user must write or edit the JSON contract themselves.