# JSON Reference

This is the current compact JSON schema used by the GUI loader and the compact-config helpers.

## Minimal shape

```json
{
  "name": "my_new_umat",
  "source": "/absolute/path/to/your_umat.for",
  "jacobian": {
    "seed": "DSTRAN",
    "output": "STRESS",
    "target": "DDSDDE"
  },
  "promote": ["STRESS"],
  "constant": ["PROPS", "STATEV"],
  "real": ["DDSDDE", "NTENS"],
  "replace": ["120-132"],
  "ntens": 6,
  "order": 1
}
```

## Required fields

`name`

- Case label used in reports and output directories.

`source`

- Path to the UMAT source file.
- Absolute paths are recommended.
- Relative paths are resolved relative to the JSON file when the JSON is loaded from disk with its path.

`jacobian.seed`

- Independent variable being seeded with OTI directions.
- For the standard UMAT tangent this is normally `DSTRAN`.

`jacobian.output`

- Output variable whose derivatives are extracted.
- For the standard UMAT tangent this is normally `STRESS`.

`jacobian.target`

- Real target array that receives the extracted derivatives.
- For the standard UMAT tangent this is normally `DDSDDE`.

`promote`

- Variables that should be promoted to OTI values.
- These are usually the evolving stress-update quantities.

`constant`

- Variables that should stay constant during differentiation.
- Material properties and fixed inputs usually belong here.

`real`

- Variables that must stay real, not OTI.
- `DDSDDE` almost always belongs here because it receives extracted derivatives, not propagated OTI values.

`replace`

- Inclusive 1-based line ranges for the old DDSDDE block to replace.
- Example: `"83-87"` means line 83 through line 87 inclusive.
- Use `show_source_lines.py` to find these ranges.

`ntens`

- Tangent size used for `DSTRAN(1:NTENS)` seeding.
- Typical values are 4 for plane problems and 6 for 3D stress/strain.

`order`

- OTI order. The current workflow is first-order, so this is normally `1`.

## Optional fields

`description`

- Free-text case description.

`umat`

- Optional entry routine name when the main routine is not the default detected `UMAT`.

`validation`

- Optional overrides for validation settings.
- Example:

```json
{
  "validation": {
    "compare": ["STRESS", "STATEV", "CONVERGENCE"],
    "absolute_tolerance": 0.0001
  }
}
```

## Variable-role rules

- `promote`, `constant`, and `real` must be disjoint.
- If the same variable appears in more than one role list, config loading will fail.
- Use uppercase names to match the rest of the repository and the scanner output.

## Advanced fields

`constitutive_jacobians`

- Optional advanced contracts for additional constitutive Jacobian extraction beyond the main `DDSDDE` path.
- Use this only if you are intentionally lifting helper-level constitutive outputs.
- Compact example:

```json
{
  "constitutive_jacobians": [
    {
      "id": "fjac_from_oti",
      "description": "Example constitutive Jacobian contract.",
      "seed": "G1",
      "seed_shape": "scalar",
      "seed_directions": 1,
      "output": "G1JAC",
      "output_shape": "scalar",
      "loop": {
        "top": 250,
        "reseed_after": 260
      },
      "extract_after": 265,
      "replace_variable": "G1JAC"
    }
  ]
}
```

Supported compact fields include:

- `id`
- `description`
- `selected_umat`
- `seed`
- `seed_shape`
- `seed_directions`
- `seed_operating_point`
- `output`
- `output_shape`
- `loop`
- `extract_after`
- `extract_kind`
- `replace_variable`
- `replace_lines`
- `additional_extractions`
- `post_loop_restore`
- `debug_dump`

`helper_surfaces`

- Optional mappings that tell the loader how helper-local outputs map back into caller variables.
- Compact example:

```json
{
  "helper_surfaces": [
    {
      "helper": "KCONSTITU",
      "target": "G1JAC",
      "source": "G1"
    }
  ]
}
```

You only need this section for advanced helper-surface extraction cases.

## Source-path recommendation

- Use an absolute `source` path when sharing a JSON between machines.
- Use a relative `source` path only when the JSON lives on the same machine and you control its location on disk.

## Practical authoring advice

- Start from `examples/elastic_minimal.json` or `templates/new_umat_minimal_template.json`.
- Keep the first attempt small.
- Only add `validation`, `constitutive_jacobians`, or `helper_surfaces` after the base JSON contract is stable.