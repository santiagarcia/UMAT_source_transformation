# User JSONs

Put your own JSON contract files in this folder if you want a clean place to
keep them inside the standalone package.

Recommended flow:

1. Copy a template from `../templates/`
2. Edit it for your UMAT
3. Point `JSON_INPUT_PATH` in `../run_json_pipeline.py` to this folder

Example setting in `run_json_pipeline.py`:

```python
JSON_INPUT_PATH = ROOT / "user_jsons"
OUTPUT_DIRECTORY = ROOT / "umat_oti_workspace" / "my_run"
RUN_VALIDATION = True
```