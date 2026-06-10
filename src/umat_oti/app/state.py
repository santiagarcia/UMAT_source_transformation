from __future__ import annotations

from pathlib import Path

from umat_oti.validation.compare_results import DEFAULT_DDSDDE_ABS_TOLERANCE, DEFAULT_DDSDDE_REL_TOLERANCE, DEFAULT_STRESS_ABS_TOLERANCE, DEFAULT_STRESS_REL_TOLERANCE


DEFAULT_VARIABLE_ROLES_KEY = "variable_roles"
DEFAULT_ROUTINE_ROLES_KEY = "routine_roles"


def initialize_session_state(st_module) -> None:
    defaults = {
        "accept_unknown_routine_warnings": False,
        "analysis": None,
        "config_paths": None,
        "file_metadata": None,
        "findings_log": [],
        "metadata": {
            "model_family": "unknown",
            "primary_kinematic_driver": "unknown",
            "strain_regime": "unknown",
            "stress_update_style": "unknown",
        },
        "mappings": {},
        "project": None,
        "project_description": "",
        "project_description_input": "",
        "project_name_input": "umat_oti_project",
        "project_name": "umat_oti_project",
        "region_classifications": [],
        "routine_roles": [],
        "selected_umat": "",
        "source_path": None,
        "source_text": "",
        "summary_path": None,
        "transformation_ntens_input": "",
        "transformation_order_input": 1,
        "transformation_output_dir": "",
        "transformation_result": None,
        "transformation_review": {},
        "transformation_run_log_path": "",
        "transformation_settings": {},
        "validation_abaqus_command": "abaqus",
        "validation_abaqus_modules": "abaqus/2024 intel/oneapi/2024.2.0.634",
        "validation_abs_tolerance": DEFAULT_STRESS_ABS_TOLERANCE,
        "validation_ddsdde_abs_tolerance": DEFAULT_DDSDDE_ABS_TOLERANCE,
        "validation_ddsdde_rel_tolerance": DEFAULT_DDSDDE_REL_TOLERANCE,
        "validation_generated_otilib_dir": "",
        "validation_material_test_mode": "single element tension",
        "validation_original_umat_path": "",
        "validation_rel_tolerance": DEFAULT_STRESS_REL_TOLERANCE,
        "validation_result": {},
        "validation_run_prefix": "srun --partition=compute1 --ntasks=1 --cpus-per-task=2 --time=00-00:30:00",
        "validation_transformed_umat_path": "",
        "validation_work_dir": "",
        "variable_roles": [],
        "workdir_input": str(Path.cwd() / "umat_oti_workspace"),
        "workdir": str(Path.cwd() / "umat_oti_workspace"),
    }
    for key, value in defaults.items():
        if key not in st_module.session_state:
            st_module.session_state[key] = value


def reset_analysis_state(st_module) -> None:
    for key in (
        "analysis",
        "config_paths",
        "file_metadata",
        "findings_log",
        "mappings",
        "routine_roles",
        "region_classifications",
        "selected_umat",
        "source_path",
        "source_text",
        "summary_path",
        "transformation_ntens_input",
        "transformation_order_input",
        "transformation_output_dir",
        "transformation_result",
        "transformation_review",
        "transformation_run_log_path",
        "transformation_settings",
        "validation_generated_otilib_dir",
        "validation_original_umat_path",
        "validation_result",
        "validation_transformed_umat_path",
        "validation_work_dir",
        "variable_roles",
    ):
        if key in st_module.session_state:
            st_module.session_state[key] = [] if key.endswith("roles") or key in {"findings_log", "region_classifications"} else "" if key in {"selected_umat", "source_text"} else None
    st_module.session_state["mappings"] = {}
    st_module.session_state["transformation_ntens_input"] = ""
    st_module.session_state["transformation_order_input"] = 1
    st_module.session_state["transformation_output_dir"] = ""
    st_module.session_state["transformation_run_log_path"] = ""
    st_module.session_state["transformation_settings"] = {}
    st_module.session_state["validation_abaqus_command"] = "abaqus"
    st_module.session_state["validation_abaqus_modules"] = "abaqus/2024 intel/oneapi/2024.2.0.634"
    st_module.session_state["validation_abs_tolerance"] = DEFAULT_STRESS_ABS_TOLERANCE
    st_module.session_state["validation_rel_tolerance"] = DEFAULT_STRESS_REL_TOLERANCE
    st_module.session_state["validation_ddsdde_abs_tolerance"] = DEFAULT_DDSDDE_ABS_TOLERANCE
    st_module.session_state["validation_ddsdde_rel_tolerance"] = DEFAULT_DDSDDE_REL_TOLERANCE
    st_module.session_state["validation_material_test_mode"] = "single element tension"
    st_module.session_state["validation_run_prefix"] = "srun --partition=compute1 --ntasks=1 --cpus-per-task=2 --time=00-00:30:00"
