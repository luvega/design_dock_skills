#!/usr/bin/env python3
"""Create and audit evidence-linked Baker protein-design run packages."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

from docking_workflow import (
    DOCKING_CANDIDATE_FIELDS,
    DOCKING_ROUTE,
    DOCKING_SCHEMA_VERSION,
    append_vina_candidates,
    append_docking_rows,
    argv_sha256,
    argv_plan_sha256,
    argv_to_powershell,
    audit_docking_candidates,
    audit_completed_docking_provenance,
    build_vina_argv_commands,
    build_hosted_requests,
    canonical_stable_id,
    docking_gaps,
    docking_report_text,
    execute_hosted_diffdock,
    execute_vina_commands,
    ligand_records,
    materialize_diffdock_response,
    preflight_docking,
    reject_plaintext_credentials,
    required_tools as docking_required_tools,
    safe_output_path,
    stable_receptor_state_id,
    tool_required_fields,
    validate_docking_tools,
    write_docking_candidate_header,
)


ROUTE_TOOLS = {
    "folded-target-binder": ["rfdiffusion", "proteinmpnn", "structure_predictor"],
    "peptide-idr-binder": ["rfdiffusion", "proteinmpnn", "structure_predictor"],
    "small-molecule-enzyme": ["rfdiffusion_all_atom", "ligandmpnn", "placer"],
    "multistate-oligomer": ["protein_generator", "structure_predictor"],
    "allosteric-switch": ["receptor_design", "structure_predictor"],
    DOCKING_ROUTE: [],
}

ROUTE_STAGES = {
    "folded-target-binder": [
        "target and functional-epitope definition",
        "target-conditioned backbone generation",
        "sequence design",
        "monomer and complex structure prediction",
        "source-linked interface and negative-target filtering",
        "expression, binding, specificity, structure, and function experiments",
    ],
    "peptide-idr-binder": [
        "target ensemble or sequence representation",
        "flexible-target or joint target-binder generation",
        "sequence design",
        "ensemble-aware prediction and filtering",
        "scrambled, homologous, mutant, and all-by-all counter-screens",
        "binding and context-preserving functional experiments",
    ],
    "small-molecule-enzyme": [
        "ligand and reaction-state definition",
        "ligand-aware or active-site-conditioned backbone generation",
        "context-aware sequence design",
        "whole-structure and local-geometry prediction",
        "apo, alternate-pose, negative-ligand, and strain checks",
        "binding, kinetic, structural, or sensor experiments",
    ],
    "multistate-oligomer": [
        "positive and negative state definition",
        "symmetry or multistate generation",
        "sequence design across states",
        "state-specific prediction and unintended-assembly checks",
        "assembly, structure, and function experiments",
    ],
    "allosteric-switch": [
        "receptor and apo/holo hypothesis",
        "output-domain and dynamic-range definition",
        "insertion, permutation, fusion, linker, or orientation library",
        "OFF/ON computational triage",
        "dual-state experimental selection and coupling validation",
    ],
    "molecular-docking-screen": [
        "receptor and chemical-state definition",
        "binding-site or docking-protocol definition",
        "pose prediction or target-focused screening",
        "protocol-specific pose ranking",
        "geometry annotation and computational audit",
        "independent experimental validation",
    ],
}

CANDIDATE_FIELDS = [
    "candidate_id",
    "stage",
    "source_tool",
    "seed",
    "status",
    "prediction_only",
    "evidence_status",
    "experimental_status",
    "experimental_sample_id",
    "raw_data_pointer",
    "notes",
]

PLACEHOLDER = re.compile(
    r"(?:\breplace_with(?:_[a-z0-9_]*)?\b|"
    r"\b(?:todo|tbd|changeme|unresolved|unknown|planning_only)\b)",
    re.I,
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.I)
SUPPORTED_SCHEMA_VERSIONS = {"1.0", "1.1"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle) or {}
    if not isinstance(value, dict):
        raise ValueError(f"Expected a YAML mapping: {path}")
    return value


def write_yaml(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(value, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.replace("\r\n", "\n"), encoding="utf-8", newline="\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_placeholder(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    return not text or bool(PLACEHOLDER.search(text))


def as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def request_schema_version(request: Mapping[str, Any]) -> str:
    if "schema_version" not in request or request.get("schema_version") is None:
        return DOCKING_SCHEMA_VERSION
    value = request.get("schema_version")
    if not isinstance(value, str) or value not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError("schema_version must be 1.0 or 1.1")
    return value


def route_request(request: dict[str, Any]) -> str:
    explicit = str(request.get("route") or "").strip()
    if explicit in ROUTE_TOOLS:
        return explicit
    workflow_kind = str(as_mapping(request.get("workflow")).get("kind") or "").casefold()
    goal = " ".join(
        str(request.get(key) or "") for key in ("design_goal", "mechanism")
    ).casefold()
    if (
        workflow_kind == "molecular-docking"
        or "docking" in request
        or any(
            token in goal
            for token in (
                "docking",
                "virtual screening",
                "redocking",
                "分子对接",
            )
        )
    ):
        return "molecular-docking-screen"
    target = as_mapping(request.get("target"))
    target_type = str(target.get("type") or "").casefold()
    if any(token in goal for token in ("alloster", "switch", "sensor", "reporter")):
        return "allosteric-switch"
    if request.get("ligand") or any(
        token in target_type for token in ("ligand", "small-molecule", "enzyme", "catal")
    ):
        return "small-molecule-enzyme"
    if request.get("symmetry") or any(
        token in target_type for token in ("oligomer", "multistate", "assembly", "symmetr")
    ):
        return "multistate-oligomer"
    if any(
        token in target_type
        for token in (
            "peptide",
            "idr",
            "idp",
            "flexible",
            "intrinsically-disordered",
            "disordered-region",
        )
    ):
        return "peptide-idr-binder"
    return "folded-target-binder"


def biological_gaps(request: dict[str, Any], route: str) -> list[str]:
    if route == DOCKING_ROUTE:
        return docking_gaps(request)
    gaps: list[str] = []
    target = request.get("target") or {}
    if is_placeholder(request.get("design_goal")):
        gaps.append("design_goal")
    if is_placeholder(target.get("type")):
        gaps.append("target.type")
    if not target.get("structure_file") and not target.get("sequence"):
        gaps.append("target.structure_file-or-sequence")
    if route == "folded-target-binder":
        if not target.get("chains"):
            gaps.append("target.chains")
        if not target.get("hotspots") and not request.get("motif"):
            gaps.append("target.hotspots-or-motif")
        function = request.get("function") or {}
        if not function.get("valency"):
            gaps.append("function.valency")
    if route == "peptide-idr-binder":
        controls = {str(x).casefold() for x in (request.get("controls") or [])}
        for name in ("scrambled", "homolog", "mutant", "cross"):
            if not any(name in control for control in controls):
                gaps.append(f"control:{name}")
    if route == "small-molecule-enzyme":
        ligand = request.get("ligand") or {}
        for key in ("file", "chemical_state"):
            if not ligand.get(key):
                gaps.append(f"ligand.{key}")
    if route == "allosteric-switch":
        readouts = (request.get("function") or {}).get("readouts") or []
        if not readouts:
            gaps.append("function.readouts-for-OFF-and-ON-states")
    if not request.get("negative_targets"):
        gaps.append("negative_targets")
    return gaps


def input_record(label: str, value: Any, configured_hash: Any = None) -> dict[str, Any]:
    configured_text = (
        str(configured_hash).strip().casefold()
        if configured_hash is not None
        else ""
    )
    configured_valid = bool(SHA256_RE.fullmatch(configured_text))
    if not value:
        return {
            "label": label,
            "path": None,
            "sha256": None,
            "status": "not-provided",
            "declared_sha256": configured_text or None,
            "declared_sha256_valid": configured_valid if configured_text else None,
            "declared_sha256_matches": None,
        }
    path = Path(str(value)).expanduser()
    if path.is_file():
        observed = sha256_file(path)
        return {
            "label": label,
            "path": str(path.resolve()),
            "bytes": path.stat().st_size,
            "sha256": observed,
            "status": "hashed-local-file",
            "declared_sha256": configured_text or None,
            "declared_sha256_valid": configured_valid if configured_text else None,
            "declared_sha256_matches": (
                observed == configured_text if configured_valid else False
            )
            if configured_text
            else None,
        }
    return {
        "label": label,
        "path": str(value),
        "sha256": configured_text if configured_valid else None,
        "status": (
            "hash-provided-for-external-file"
            if configured_valid
            else "unresolved-external-file"
        ),
        "declared_sha256": configured_text or None,
        "declared_sha256_valid": configured_valid if configured_text else None,
        "declared_sha256_matches": None,
    }


def collect_input_hashes(request: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    target = as_mapping(request.get("target"))
    records["target_structure"] = input_record(
        "target_structure", target.get("structure_file"), target.get("sha256")
    )
    if target.get("source_structure_file"):
        records["target_source_structure"] = input_record(
            "target_source_structure",
            target.get("source_structure_file"),
            target.get("source_structure_sha256"),
        )
    docking = as_mapping(request.get("docking"))
    preparation = as_mapping(docking.get("preparation"))
    if preparation.get("receptor_pdbqt"):
        records["prepared_receptor_pdbqt"] = input_record(
            "prepared_receptor_pdbqt", preparation.get("receptor_pdbqt")
        )
    motif = request.get("motif") or {}
    if isinstance(motif, dict) and motif.get("file"):
        records["motif"] = input_record("motif", motif.get("file"), motif.get("sha256"))
    ligand = as_mapping(request.get("ligand"))
    if isinstance(ligand, dict) and ligand.get("file"):
        records["ligand"] = input_record("ligand", ligand.get("file"), ligand.get("sha256"))
        if ligand.get("prepared_file"):
            records["prepared_ligand"] = input_record(
                "prepared_ligand", ligand.get("prepared_file")
            )
    validation = as_mapping(docking.get("validation"))
    if validation.get("reference_pose"):
        records["docking_reference_pose"] = input_record(
            "docking_reference_pose",
            validation.get("reference_pose"),
            validation.get("reference_pose_sha256"),
        )
    atom_mapping = validation.get("atom_mapping")
    if isinstance(atom_mapping, Mapping):
        mapping_file = atom_mapping.get("file")
        mapping_hash = atom_mapping.get("sha256") or atom_mapping.get(
            "atom_mapping_sha256"
        )
    else:
        mapping_file = atom_mapping if Path(str(atom_mapping or "")).is_file() else None
        mapping_hash = validation.get("atom_mapping_sha256")
    if mapping_file:
        records["docking_atom_mapping"] = input_record(
            "docking_atom_mapping", mapping_file, mapping_hash
        )
    ligand_library = as_mapping(request.get("ligand_library"))
    if isinstance(ligand_library, dict) and ligand_library.get("manifest_file"):
        records["ligand_library_manifest"] = input_record(
            "ligand_library_manifest",
            ligand_library.get("manifest_file"),
            ligand_library.get("sha256"),
        )
        try:
            for ligand_record in ligand_records(request):
                ligand_id = ligand_record.get("ligand_id") or "unresolved"
                state_id = ligand_record.get("chemical_state_id") or "unresolved"
                key = f"ligand_library_member:{ligand_id}:{state_id}"
                records[key] = input_record(key, ligand_record.get("file"))
                if ligand_record.get("prepared_file"):
                    prepared_key = key + ":prepared"
                    records[prepared_key] = input_record(
                        prepared_key, ligand_record.get("prepared_file")
                    )
        except (OSError, csv.Error, UnicodeError):
            pass
    for index, negative in enumerate(request.get("negative_targets") or [], start=1):
        if isinstance(negative, dict) and negative.get("structure_file"):
            key = f"negative_target_{index}"
            records[key] = input_record(key, negative.get("structure_file"), negative.get("sha256"))
    return records


def detect_gpu_vram_gb() -> float | None:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return None
    completed = subprocess.run(
        [executable, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
        shell=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if completed.returncode != 0:
        return None
    try:
        values = [float(line.strip()) / 1024 for line in completed.stdout.splitlines() if line.strip()]
        return max(values) if values else None
    except ValueError:
        return None


def probe_environment(request: dict[str, Any], *, strict_run: bool) -> dict[str, Any]:
    request_schema_version(request)
    if route_request(request) == DOCKING_ROUTE:
        return preflight_docking(request, strict_run=strict_run)
    backend = request.get("backend") or {}
    kind = str(backend.get("kind") or "unspecified").casefold()
    detected_vram = detect_gpu_vram_gb()
    declared_vram = backend.get("gpu_vram_gb")
    vram = float(declared_vram) if declared_vram is not None else detected_vram
    issues: list[str] = []
    external = kind in {"hpc", "remote", "remote-gpu", "colab", "linux-gpu-remote"}
    if external:
        issues.append("External backend cannot be executed or verified from this local session")
    else:
        if platform.system() != "Linux":
            issues.append("Heavy-model local run requires a configured Linux GPU backend")
        if vram is None:
            issues.append("GPU VRAM could not be established")
        elif vram < 12:
            issues.append(
                f"Declared/detected VRAM is {vram:.1f} GiB; downgrade to planning or a remote backend"
            )
    return {
        "checked_at": utc_now(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "backend_kind": kind,
        "declared_gpu_vram_gb": declared_vram,
        "detected_gpu_vram_gb": detected_vram,
        "effective_gpu_vram_gb": vram,
        "external_backend": external,
        "ready_for_local_execution": not issues,
        "strict_run": strict_run,
        "issues": issues,
    }


def preflight_environment(
    request: dict[str, Any], *, strict_run: bool
) -> dict[str, Any]:
    """Versioned preflight entry point retained alongside the compatibility alias."""
    return probe_environment(request, strict_run=strict_run)


class FormatContext(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def build_command_context(
    request: dict[str, Any], output_dir: Path, run_manifest_path: Path, tool: dict[str, Any]
) -> FormatContext:
    target = request.get("target") or {}
    ligand = request.get("ligand") or {}
    reproducibility = request.get("reproducibility") or {}
    hotspots = target.get("hotspots") or []
    return FormatContext(
        repo_path=tool.get("repo_path") or "REPLACE_WITH_REPO_PATH",
        checkpoint=tool.get("checkpoint") or "REPLACE_WITH_CHECKPOINT",
        target_structure=target.get("structure_file") or "REPLACE_WITH_TARGET",
        ligand_file=ligand.get("file") or "REPLACE_WITH_LIGAND",
        hotspots_csv=",".join(str(value) for value in hotspots),
        chains_csv=",".join(str(value) for value in (target.get("chains") or [])),
        seed=reproducibility.get("seed") if reproducibility.get("seed") is not None else "REPLACE_WITH_SEED",
        candidate_count=reproducibility.get("candidate_count") or "REPLACE_WITH_COUNT",
        output_dir=str(output_dir),
        run_manifest=str(run_manifest_path),
    )


def render_commands(
    request: dict[str, Any], route: str, output_dir: Path, run_manifest_path: Path
) -> tuple[list[str], list[str]]:
    commands: list[str] = []
    problems: list[str] = []
    configured = request.get("tools") or {}
    for tool_name in ROUTE_TOOLS[route]:
        tool = configured.get(tool_name) or {}
        template = tool.get("command_template")
        if not template:
            commands.append(f"# BLOCKED: configure tools.{tool_name}.command_template")
            problems.append(f"tools.{tool_name}.command_template")
            continue
        templates = template if isinstance(template, list) else [template]
        context = build_command_context(request, output_dir, run_manifest_path, tool)
        for item in templates:
            rendered = str(item).format_map(context)
            if "REPLACE_WITH_" in rendered or re.search(r"\{[A-Za-z_][A-Za-z0-9_]*\}", rendered):
                problems.append(f"tools.{tool_name}.command_template-unresolved")
                rendered = "# BLOCKED: unresolved command variables for " + tool_name + "\n# " + rendered
            commands.append(rendered)
    return commands, sorted(set(problems))


def render_argv_commands(
    request: dict[str, Any], route: str, output_dir: Path, run_manifest_path: Path
) -> tuple[list[dict[str, Any]], list[str]]:
    """Render explicitly configured argv arrays; strings are never accepted for execution."""
    rendered_commands: list[dict[str, Any]] = []
    problems: list[str] = []
    configured = request.get("tools") or {}
    for tool_name in ROUTE_TOOLS[route]:
        tool = configured.get(tool_name) or {}
        template = tool.get("command_argv_template")
        if template is None:
            problems.append(f"tools.{tool_name}.command_argv_template")
            continue
        templates = (
            template
            if isinstance(template, list)
            and template
            and all(isinstance(item, list) for item in template)
            else [template]
        )
        context = build_command_context(request, output_dir, run_manifest_path, tool)
        for argv_template in templates:
            if not isinstance(argv_template, list) or not argv_template:
                problems.append(f"tools.{tool_name}.command_argv_template-invalid")
                continue
            argv = [str(item).format_map(context) for item in argv_template]
            if any(
                "REPLACE_WITH_" in item
                or re.search(r"\{[A-Za-z_][A-Za-z0-9_]*\}", item)
                for item in argv
            ):
                problems.append(f"tools.{tool_name}.command_argv_template-unresolved")
                continue
            rendered_commands.append({"tool": tool_name, "argv": argv})
    return rendered_commands, sorted(set(problems))


def make_target_manifest(request: dict[str, Any], route: str, hashes: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": request_schema_version(request),
        "generated_at": utc_now(),
        "route": route,
        "design_goal": request.get("design_goal"),
        "mechanism": request.get("mechanism"),
        "target": request.get("target") or {},
        "motif": request.get("motif"),
        "ligand": request.get("ligand"),
        "ligand_library": request.get("ligand_library"),
        "docking": request.get("docking"),
        "external_service": request.get("external_service"),
        "fixed_positions": request.get("fixed_positions") or [],
        "length": request.get("length"),
        "symmetry": request.get("symmetry"),
        "negative_targets": request.get("negative_targets") or [],
        "function": request.get("function") or {},
        "controls": request.get("controls") or [],
        "input_hashes": hashes,
    }


def make_design_brief(request: dict[str, Any], route: str, gaps: list[str]) -> str:
    stages = "\n".join(f"{index}. {stage}" for index, stage in enumerate(ROUTE_STAGES[route], start=1))
    negatives = request.get("negative_targets") or []
    controls = request.get("controls") or []
    if route == DOCKING_ROUTE:
        docking_controls = as_mapping(
            as_mapping(request.get("docking")).get("validation")
        ).get("controls")
        if isinstance(docking_controls, list):
            controls = docking_controls
    unresolved = "\n".join(f"- `{gap}`" for gap in gaps) or "- None recorded"
    return f"""# Design brief

## Decision

- Route: `{route}`
- Goal: {request.get('design_goal') or 'unresolved'}
- Mechanism: {request.get('mechanism') or 'unresolved'}
- Evidence status: `planning_only`

## Staged workflow

{stages}

## Selectivity and controls

- Negative targets: {len(negatives)} declared
- Controls: {', '.join(str(value) for value in controls) if controls else 'unresolved'}

## Unresolved inputs

{unresolved}

## Interpretation boundary

Backbone generation, sequence design, structure prediction, and experiment are separate stages. Confidence, PAE, RMSD, pLDDT, CMS, BSA, likelihood, and docking metrics remain computational filters. A candidate is not an experimentally verified binder, catalyst, agonist, antagonist, sensor, or switch until the corresponding assay is recorded.
"""


def make_run_manifest(
    request: dict[str, Any],
    route: str,
    mode: str,
    output_dir: Path,
    hashes: dict[str, Any],
    preflight: dict[str, Any],
    commands: list[str],
    command_problems: list[str],
) -> dict[str, Any]:
    reproducibility = as_mapping(request.get("reproducibility"))
    tools = as_mapping(request.get("tools"))
    selected_tools = (
        docking_required_tools(request) if route == DOCKING_ROUTE else ROUTE_TOOLS[route]
    )
    run_id = "run-" + hashlib.sha256(
        f"{utc_now()}|{output_dir.resolve()}".encode("utf-8")
    ).hexdigest()[:20]
    return {
        "schema_version": request_schema_version(request),
        "generated_at": utc_now(),
        "run_id": run_id,
        "mode": mode,
        "route": route,
        "evidence_status": "planning_only",
        "input_hashes": hashes,
        "tools": {name: tools.get(name) or {} for name in selected_tools},
        "parameters": {
            "target": request.get("target") or {},
            "motif": request.get("motif"),
            "ligand": request.get("ligand"),
            "ligand_library": request.get("ligand_library"),
            "docking": request.get("docking"),
            "external_service": request.get("external_service"),
            "fixed_positions": request.get("fixed_positions") or [],
            "length": request.get("length"),
            "symmetry": request.get("symmetry"),
            "negative_targets": request.get("negative_targets") or [],
            "function": request.get("function") or {},
        },
        "random_seed": reproducibility.get("seed"),
        "candidate_count": reproducibility.get("candidate_count"),
        "filters": request.get("filters") or [],
        "backend": request.get("backend") or {},
        "preflight": preflight,
        "outputs": {
            "root": str(output_dir),
            "design_brief": str(output_dir / "design_brief.md"),
            "target_manifest": str(output_dir / "target_manifest.yaml"),
            "run_manifest": str(output_dir / "run_manifest.yaml"),
            "commands": str(output_dir / "commands.sh"),
            "commands_ps1": str(output_dir / "commands.ps1"),
            "candidates": str(output_dir / "candidates.csv"),
            "docking_candidates": str(output_dir / "docking_candidates.csv"),
            "docking_report": str(output_dir / "docking_report.md"),
        },
        "execution": {
            "status": "not-started",
            "command_problems": command_problems,
            "commands": commands,
            "argv_commands": [],
            "argv_plan_sha256": argv_plan_sha256([]),
            "results": [],
        },
        "docking": request.get("docking"),
        "ligand_library": request.get("ligand_library"),
        "external_service": request.get("external_service"),
    }


def write_candidate_header(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CANDIDATE_FIELDS)
        writer.writeheader()


def prepare_package(request: dict[str, Any], mode: str, output_dir: Path) -> dict[str, Any]:
    reject_plaintext_credentials(request)
    request_schema_version(request)
    route = route_request(request)
    gaps = biological_gaps(request, route)
    hashes = collect_input_hashes(request)
    output_dir.mkdir(parents=True, exist_ok=True)
    run_manifest_path = output_dir / "run_manifest.yaml"
    commands, command_problems = render_commands(request, route, output_dir, run_manifest_path)
    preflight = probe_environment(request, strict_run=mode == "run")
    target_manifest = make_target_manifest(request, route, hashes)
    manifest = make_run_manifest(
        request, route, mode, output_dir, hashes, preflight, commands, command_problems
    )
    argv_commands: list[dict[str, Any]] = []
    if route == DOCKING_ROUTE:
        docking = as_mapping(request.get("docking"))
        if docking.get("engine") == "autodock-vina" and not gaps:
            argv_commands = build_vina_argv_commands(request, output_dir)
        elif docking.get("engine") == "diffdock-nim-self-hosted":
            configured_argv = docking.get("argv_commands") or []
            if (
                isinstance(configured_argv, list)
                and configured_argv
                and all(
                    isinstance(argv, list)
                    and argv
                    and all(isinstance(item, str) and item for item in argv)
                    for argv in configured_argv
                )
            ):
                argv_commands = [
                    {"engine": "diffdock-nim-self-hosted", "argv": list(argv)}
                    for argv in configured_argv
                ]
            elif configured_argv:
                gaps.append("docking.argv_commands-invalid")
        manifest["execution"]["argv_commands"] = argv_commands
        manifest["execution"]["argv_plan_sha256"] = argv_plan_sha256(argv_commands)
        manifest["execution"]["command_problems"] = sorted(
            set(manifest["execution"]["command_problems"] + gaps)
        )
    else:
        argv_commands, argv_problems = render_argv_commands(
            request, route, output_dir, run_manifest_path
        )
        manifest["execution"]["argv_commands"] = argv_commands
        manifest["execution"]["argv_plan_sha256"] = argv_plan_sha256(argv_commands)
        if mode == "run":
            manifest["execution"]["command_problems"] = sorted(
                set(manifest["execution"]["command_problems"] + argv_problems)
            )
    write_text(output_dir / "design_brief.md", make_design_brief(request, route, gaps))
    write_yaml(output_dir / "target_manifest.yaml", target_manifest)
    write_yaml(run_manifest_path, manifest)
    write_text(
        output_dir / "commands.sh",
        "#!/usr/bin/env bash\nset -euo pipefail\n\n" + "\n\n".join(commands) + "\n",
    )
    write_candidate_header(output_dir / "candidates.csv")
    if route == DOCKING_ROUTE:
        write_text(output_dir / "commands.ps1", argv_to_powershell(argv_commands))
        write_docking_candidate_header(
            output_dir / "docking_candidates.csv", reset=True
        )
        write_text(
            output_dir / "docking_report.md",
            docking_report_text(request, preflight, sorted(set(gaps + preflight["issues"]))),
        )
    return manifest


def _audit_candidates_unchecked(path: Path) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        return [f"Candidate table not found: {path}"]
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, strict=True)
        missing = [field for field in CANDIDATE_FIELDS if field not in (reader.fieldnames or [])]
        if missing:
            errors.append("Candidate table missing columns: " + ", ".join(missing))
            return errors
        for line, row in enumerate(reader, start=2):
            prediction_only = str(row.get("prediction_only") or "").casefold() in {"1", "true", "yes"}
            experimental = str(row.get("experimental_status") or "").strip().casefold()
            status = str(row.get("status") or "").strip().casefold()
            evidence = str(row.get("evidence_status") or "").strip().casefold()
            if prediction_only and experimental not in {"", "not-tested", "not_tested", "unknown", "not-applicable"}:
                errors.append(
                    f"Line {line}: prediction_only candidate cannot claim experimental_status={experimental}"
                )
            if prediction_only and any(word in status for word in ("validated", "active", "binder", "agonist", "switch")):
                errors.append(f"Line {line}: computational candidate uses experimental-sounding status={status}")
            if prediction_only and evidence == "current_experimental":
                errors.append(f"Line {line}: prediction_only conflicts with current_experimental")
    return errors


def audit_candidates(path: Path) -> list[str]:
    try:
        return _audit_candidates_unchecked(path)
    except (OSError, UnicodeError, csv.Error):
        return ["Candidate table is unreadable, undecodable, or malformed"]


def audit_manifest(
    manifest: dict[str, Any],
    candidates: Path | None = None,
    *,
    package_root: Path | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        reject_plaintext_credentials(manifest)
    except ValueError:
        errors.append(
            "Manifest contains a credential-like plaintext token; use an environment-variable reference"
        )
    schema_version = manifest.get("schema_version")
    if (
        not isinstance(schema_version, str)
        or schema_version not in SUPPORTED_SCHEMA_VERSIONS
    ):
        errors.append("schema_version must be 1.0 or 1.1")
    route = manifest.get("route")
    if route not in ROUTE_TOOLS:
        errors.append(f"Unknown route: {route}")
        required_tools: list[str] = []
    elif route == DOCKING_ROUTE:
        required_tools = docking_required_tools(manifest)
    else:
        required_tools = ROUTE_TOOLS[route]
    if route != DOCKING_ROUTE:
        if manifest.get("random_seed") is None:
            errors.append("Missing random_seed")
        if not isinstance(manifest.get("candidate_count"), int) or int(
            manifest.get("candidate_count") or 0
        ) <= 0:
            errors.append("candidate_count must be a positive integer")

    for label, record_value in as_mapping(manifest.get("input_hashes")).items():
        if not isinstance(record_value, Mapping):
            errors.append(f"Input {label} hash record is not a mapping")
            continue
        record = record_value
        if record.get("path") and not SHA256_RE.fullmatch(str(record.get("sha256") or "")):
            errors.append(f"Input {label} lacks a valid SHA-256")
        if record.get("declared_sha256") and record.get(
            "declared_sha256_valid"
        ) is not True:
            errors.append(f"Input {label} declared SHA-256 is invalid")
        if record.get("declared_sha256_matches") is False:
            errors.append(
                f"Input {label} declared SHA-256 does not match the local file"
            )
        if manifest.get("mode") == "run" and record.get("path"):
            path = Path(str(record.get("path"))).expanduser()
            if not path.is_file():
                errors.append(f"Input {label} is missing at run audit")
            elif SHA256_RE.fullmatch(str(record.get("sha256") or "")):
                if sha256_file(path) != str(record.get("sha256")).casefold():
                    errors.append(f"Input {label} hash mismatch after package preparation")

    configured = as_mapping(manifest.get("tools"))
    for tool_name in required_tools:
        tool = as_mapping(configured.get(tool_name))
        fields = (
            tool_required_fields(tool_name)
            if route == DOCKING_ROUTE
            else (
                "repository",
                "repo_path",
                "commit",
                "license",
                "checkpoint",
                "checkpoint_sha256",
                "command_template",
            )
        )
        for field in fields:
            if is_placeholder(tool.get(field)):
                message = f"Tool {tool_name} missing or placeholder field: {field}"
                if route == DOCKING_ROUTE and manifest.get("mode") != "run":
                    warnings.append(message)
                else:
                    errors.append(message)
        if route != DOCKING_ROUTE:
            checkpoint_hash = str(tool.get("checkpoint_sha256") or "").strip().lower()
            if checkpoint_hash and not is_placeholder(checkpoint_hash) and not SHA256_RE.fullmatch(checkpoint_hash):
                errors.append(f"Tool {tool_name} checkpoint_sha256 is not 64 hexadecimal characters")
            checkpoint = Path(str(tool.get("checkpoint") or ""))
            if checkpoint.is_file() and SHA256_RE.fullmatch(checkpoint_hash):
                observed = sha256_file(checkpoint)
                if observed != checkpoint_hash:
                    errors.append(f"Tool {tool_name} checkpoint hash mismatch")

    for index, recipe_filter in enumerate(manifest.get("filters") or [], start=1):
        if not isinstance(recipe_filter, dict):
            errors.append(f"Filter {index} is not a mapping")
            continue
        for field in ("name", "stage", "source_doi", "source_locator"):
            if is_placeholder(recipe_filter.get(field)):
                errors.append(f"Filter {index} missing source field: {field}")

    execution = as_mapping(manifest.get("execution"))
    for problem in execution.get("command_problems") or []:
        message = f"Unresolved command input: {problem}"
        if route == DOCKING_ROUTE and manifest.get("mode") != "run":
            warnings.append(message)
        else:
            errors.append(message)
    if route != DOCKING_ROUTE:
        for command in execution.get("commands") or []:
            if "# BLOCKED:" in str(command) or "REPLACE_WITH_" in str(command):
                errors.append("Command list contains blocked or placeholder commands")
                break

    if route == DOCKING_ROUTE:
        parameters = as_mapping(manifest.get("parameters"))
        docking_request = {
            "target": parameters.get("target"),
            "ligand": parameters.get("ligand"),
            "ligand_library": manifest.get("ligand_library")
            or parameters.get("ligand_library"),
            "docking": manifest.get("docking")
            if manifest.get("docking") is not None
            else parameters.get("docking"),
            "external_service": manifest.get("external_service")
            or parameters.get("external_service"),
            "tools": configured,
        }
        output_root = Path(
            str(as_mapping(manifest.get("outputs")).get("root") or "")
        ).resolve()
        if package_root is not None and output_root != package_root.resolve():
            errors.append("Manifest output root does not match the package root")
        try:
            gaps = docking_gaps(
                docking_request,
                strict_run=manifest.get("mode") == "run",
            )
        except ValueError:
            gaps = ["credential-like plaintext token"]
        for gap in gaps:
            message = f"Docking preflight: {gap}"
            if manifest.get("mode") == "run":
                errors.append(message)
            else:
                warnings.append(message)
        if manifest.get("mode") == "run":
            live_preflight = preflight_docking(
                docking_request,
                strict_run=True,
            )
            errors.extend(
                f"Live docking preflight: {issue}"
                for issue in live_preflight.get("issues") or []
            )
            current_inputs = collect_input_hashes(docking_request)
            recorded_inputs = as_mapping(manifest.get("input_hashes"))
            for label, current in current_inputs.items():
                recorded = recorded_inputs.get(label)
                if not isinstance(recorded, dict):
                    errors.append(f"Input hash record missing: {label}")
                    continue
                if str(recorded.get("path") or "") != str(current.get("path") or ""):
                    errors.append(f"Input {label} path differs from prepared manifest")
                if str(recorded.get("sha256") or "") != str(current.get("sha256") or ""):
                    errors.append(f"Input {label} hash mismatch after package preparation")
        engine = str(
            as_mapping(docking_request.get("docking")).get("engine") or ""
        )
        argv_commands = execution.get("argv_commands") or []
        if manifest.get("mode") == "run" and engine == "diffdock-nim-self-hosted":
            errors.append("DiffDock self-hosted adapter-not-implemented")
        if manifest.get("mode") == "run" and engine == "autodock-vina":
            if not argv_commands:
                errors.append("Run requires reviewed execution.argv_commands")
            for command in argv_commands:
                argv = command.get("argv") if isinstance(command, dict) else None
                if (
                    not isinstance(argv, list)
                    or not argv
                    or not all(isinstance(item, str) and item for item in argv)
                ):
                    errors.append("Run contains an invalid argv command")
                    break
            if not gaps:
                try:
                    expected_commands = build_vina_argv_commands(
                        docking_request, output_root
                    )
                except ValueError as error:
                    errors.append(f"Could not rebuild Vina argv: {error}")
                    expected_commands = []
                if len(argv_commands) != len(expected_commands):
                    errors.append("Vina argv command count differs from rebuilt plan")
                recorded_plan_digest = str(
                    execution.get("argv_plan_sha256") or ""
                )
                if recorded_plan_digest != argv_plan_sha256(argv_commands):
                    errors.append("Vina argv plan digest mismatch")
                if recorded_plan_digest != argv_plan_sha256(expected_commands):
                    errors.append("Vina argv plan digest differs from rebuilt plan")
                for index, (actual, expected) in enumerate(
                    zip(argv_commands, expected_commands), start=1
                ):
                    if actual.get("argv") != expected.get("argv"):
                        errors.append(
                            f"Vina argv command {index} differs from rebuilt expected argv"
                        )
                    actual_digest = str(actual.get("argv_sha256") or "")
                    if actual_digest != argv_sha256(actual.get("argv") or []):
                        errors.append(f"Vina argv command {index} digest mismatch")
                    if actual_digest != expected.get("argv_sha256"):
                        errors.append(
                            f"Vina argv command {index} digest differs from expected plan"
                        )
                    for field in (
                        "ligand_id",
                        "chemical_state_id",
                        "seed",
                        "input_sha256",
                        "output_path",
                        "rank_scope",
                    ):
                        if actual.get(field) != expected.get(field):
                            errors.append(
                                f"Vina argv command {index} metadata field {field} "
                                "differs from rebuilt expected plan"
                            )
                    try:
                        output_path = Path(
                            str(actual.get("output_path") or "")
                        ).resolve()
                        if not output_path.is_relative_to(output_root):
                            errors.append(
                                f"Vina argv command {index} output escapes package root"
                            )
                    except (OSError, ValueError):
                        errors.append(f"Vina argv command {index} output path is invalid")

    preflight = as_mapping(manifest.get("preflight"))
    if manifest.get("mode") == "run" and not preflight.get("ready_for_local_execution"):
        errors.extend(f"Preflight: {issue}" for issue in preflight.get("issues") or ["not ready"])
    elif preflight.get("issues"):
        warnings.extend(f"Preflight: {issue}" for issue in preflight.get("issues") or [])

    if route == DOCKING_ROUTE:
        evidence_status = str(manifest.get("evidence_status") or "")
        if evidence_status not in {
            "planning_only",
            "computational_prediction",
        }:
            errors.append(
                "Docking manifest evidence_status must be planning_only or "
                "computational_prediction"
            )
        if (
            execution.get("status") == "completed-computational-only"
            and evidence_status != "computational_prediction"
        ):
            errors.append(
                "Completed docking manifest must use computational_prediction "
                "evidence_status"
            )
        if str(execution.get("status") or "").startswith(("blocked", "failed")):
            errors.append(
                f"Docking execution ended with terminal status "
                f"{execution.get('status')}"
            )
    elif manifest.get("evidence_status") == "current_experimental":
        warnings.append("Manifest-level current_experimental requires assay-level provenance; review candidate rows")
    if candidates is not None:
        if route == DOCKING_ROUTE:
            resolved_package_root = (
                package_root
                if package_root is not None
                else Path(str(as_mapping(manifest.get("outputs")).get("root") or ""))
            )
            errors.extend(
                audit_docking_candidates(
                    candidates,
                    expected_run_id=str(manifest.get("run_id") or "") or None,
                    execution_status=str(execution.get("status") or ""),
                    output_root=resolved_package_root,
                )
            )
            errors.extend(
                audit_completed_docking_provenance(
                    manifest,
                    candidates,
                    resolved_package_root,
                )
            )
        else:
            errors.extend(audit_candidates(candidates))
    return {
        "audited_at": utc_now(),
        "passed": not errors,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
    }


def audit_markdown(result: dict[str, Any], manifest_path: Path) -> str:
    errors = "\n".join(f"- {value}" for value in result["errors"]) or "- None"
    warnings = "\n".join(f"- {value}" for value in result["warnings"]) or "- None"
    return f"""# Run audit

- Manifest: `{manifest_path}`
- Passed: `{str(result['passed']).lower()}`
- Audited at: `{result['audited_at']}`

## Errors

{errors}

## Warnings

{warnings}

## Evidence boundary

A passed computational audit establishes provenance and schema consistency only. It does not establish affinity, expression, catalysis, specificity, signaling, sensor dynamic range, or any other experimental outcome.
"""


def docking_request_from_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    parameters = as_mapping(manifest.get("parameters"))
    return {
        "target": parameters.get("target"),
        "ligand": parameters.get("ligand"),
        "ligand_library": manifest.get("ligand_library")
        if manifest.get("ligand_library") is not None
        else parameters.get("ligand_library"),
        "docking": manifest.get("docking")
        if manifest.get("docking") is not None
        else parameters.get("docking"),
        "external_service": manifest.get("external_service")
        if manifest.get("external_service") is not None
        else parameters.get("external_service"),
        "tools": manifest.get("tools"),
    }


def persist_terminal_state(
    manifest_path: Path,
    manifest: dict[str, Any],
    candidate_path: Path,
    *,
    audit_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a terminal manifest and regenerate both reader-facing reports."""
    write_yaml(manifest_path, manifest)
    result = audit_result or audit_manifest(
        manifest,
        candidate_path,
        package_root=manifest_path.parent,
    )
    result = {
        **result,
        "errors": sorted(
            set(
                list(result.get("errors") or [])
                + list(
                    as_mapping(manifest.get("execution")).get(
                        "audit_errors"
                    )
                    or []
                )
            )
        ),
    }
    result["passed"] = not result["errors"]
    write_text(
        manifest_path.parent / "audit_report.md",
        audit_markdown(result, manifest_path),
    )
    if manifest.get("route") == DOCKING_ROUTE:
        request = docking_request_from_manifest(manifest)
        preflight = as_mapping(manifest.get("preflight"))
        execution = as_mapping(manifest.get("execution"))
        findings = sorted(
            set(
                list(result.get("errors") or [])
                + list(execution.get("audit_errors") or [])
                + list(preflight.get("issues") or [])
            )
        )
        write_text(
            manifest_path.parent / "docking_report.md",
            docking_report_text(
                request,
                preflight,
                findings,
                execution_status=str(execution.get("status") or "unknown"),
                evidence_status=str(
                    manifest.get("evidence_status") or "planning_only"
                ),
                audit_passed=bool(result.get("passed")),
            ),
        )
    return result


def execute_manifest(manifest_path: Path, manifest: dict[str, Any]) -> int:
    try:
        reject_plaintext_credentials(manifest)
    except ValueError:
        return 2
    candidate_path = (
        manifest_path.parent / "docking_candidates.csv"
        if manifest.get("route") == DOCKING_ROUTE
        else manifest_path.parent / "candidates.csv"
    )
    if manifest.get("mode") != "run":
        manifest.setdefault("execution", {})["status"] = "blocked-not-run-mode"
        persist_terminal_state(manifest_path, manifest, candidate_path)
        return 2
    execution = as_mapping(manifest.get("execution"))
    commands = list(execution.get("argv_commands") or [])
    pre_audit = audit_manifest(
        manifest,
        candidate_path,
        package_root=manifest_path.parent,
    )
    if not pre_audit["passed"]:
        manifest["execution"]["status"] = "blocked-by-execution-audit"
        manifest["execution"]["audit_errors"] = pre_audit["errors"]
        persist_terminal_state(
            manifest_path, manifest, candidate_path, audit_result=pre_audit
        )
        return 2
    if manifest.get("route") == DOCKING_ROUTE:
        write_docking_candidate_header(candidate_path, reset=True)
    if manifest.get("route") == DOCKING_ROUTE:
        docking = manifest.get("docking") or {}
        engine = str(docking.get("engine") or "")
        if engine == "diffdock-nim-self-hosted":
            manifest["execution"]["status"] = "blocked-adapter-not-implemented"
            manifest["execution"]["audit_errors"] = [
                "DiffDock self-hosted adapter-not-implemented"
            ]
            persist_terminal_state(manifest_path, manifest, candidate_path)
            return 2
        if engine == "diffdock-nim-hosted":
            parameters = as_mapping(manifest.get("parameters"))
            request = {
                "target": parameters.get("target"),
                "ligand": parameters.get("ligand"),
                "ligand_library": manifest.get("ligand_library"),
                "docking": docking,
                "external_service": manifest.get("external_service"),
                "tools": manifest.get("tools"),
            }
            hosted_tool = as_mapping(
                as_mapping(manifest.get("tools")).get("diffdock_hosted")
            )
            all_rows: list[dict[str, Any]] = []
            results: list[dict[str, Any]] = []
            try:
                hosted_requests = build_hosted_requests(request)
                for request_record in hosted_requests:
                    response = execute_hosted_diffdock(
                        request_record["payload"],
                        external_service=as_mapping(
                            manifest.get("external_service")
                        ),
                    )
                    response_path = safe_output_path(
                        manifest_path.parent,
                        "docking_outputs",
                        canonical_stable_id(request_record["ligand_id"]),
                        canonical_stable_id(request_record["chemical_state_id"]),
                        "hosted",
                        "response.json",
                    )
                    response_path.parent.mkdir(parents=True, exist_ok=True)
                    response_path.write_bytes(response.raw_bytes)
                    rows = materialize_diffdock_response(
                        response,
                        request_record,
                        manifest_path.parent,
                        run_id=str(manifest.get("run_id") or ""),
                        engine_version=str(
                            hosted_tool.get("service_version") or ""
                        ),
                        receptor_state_id=stable_receptor_state_id(
                            as_mapping(parameters.get("target"))
                        ),
                    )
                    all_rows.extend(rows)
                    version_headers = response.observed_headers
                    observed_service_version = next(
                        (
                            version_headers[key]
                            for key in (
                                "x-nim-version",
                                "x-nvidia-service-version",
                                "x-service-version",
                            )
                            if key in version_headers
                        ),
                        "unreported",
                    )
                    pose_outputs = [
                        {
                            "pose_id": row["pose_id"],
                            "path": row["output_path"],
                            "sha256": sha256_file(Path(row["output_path"])),
                            "bytes": Path(row["output_path"]).stat().st_size,
                        }
                        for row in rows
                    ]
                    results.append(
                        {
                            "adapter": "diffdock-nim-hosted",
                            "ligand_id": request_record["ligand_id"],
                            "chemical_state_id": request_record[
                                "chemical_state_id"
                            ],
                            "response_path": str(response_path),
                            "response_sha256": sha256_file(response_path),
                            "response_bytes": response_path.stat().st_size,
                            "input_sha256": request_record["input_sha256"],
                            "pose_count": len(rows),
                            "pose_outputs": pose_outputs,
                            "expected_service_version": str(
                                hosted_tool.get("service_version") or ""
                            ),
                            "observed_service_version": observed_service_version,
                            "observed_headers": dict(version_headers),
                        }
                    )
            except Exception:
                manifest["execution"]["status"] = "failed-hosted-adapter"
                manifest["execution"]["results"] = [
                    {
                        "adapter": "diffdock-nim-hosted",
                        "status": "failed",
                        "error": "hosted request failed",
                    }
                ]
                manifest["execution"]["audit_errors"] = [
                    "hosted request failed"
                ]
                persist_terminal_state(manifest_path, manifest, candidate_path)
                return 2
            append_docking_rows(candidate_path, all_rows)
            manifest["execution"]["results"] = results
            manifest["evidence_status"] = "computational_prediction"
        elif engine == "autodock-vina":
            results = execute_vina_commands(commands, cwd=manifest_path.parent)
            manifest["execution"]["results"] = results
            if any(int(result.get("returncode") or 0) != 0 for result in results):
                manifest["execution"]["status"] = "failed"
                persist_terminal_state(manifest_path, manifest, candidate_path)
                return next(
                    int(result["returncode"])
                    for result in results
                    if int(result.get("returncode") or 0) != 0
                )
            missing_output_errors = [
                f"Vina output missing or unanchored for command {index}"
                for index, result in enumerate(results, start=1)
                if not Path(str(result.get("output_path") or "")).is_file()
                or not SHA256_RE.fullmatch(
                    str(result.get("output_sha256") or "")
                )
            ]
            if missing_output_errors:
                manifest["execution"]["status"] = "failed-post-audit"
                manifest["execution"]["audit_errors"] = missing_output_errors
                persist_terminal_state(manifest_path, manifest, candidate_path)
                return 2
            tools = as_mapping(manifest.get("tools"))
            engine_version = str(
                as_mapping(tools.get("autodock_vina")).get("version") or ""
            )
            receptor_state_id = stable_receptor_state_id(
                as_mapping(as_mapping(manifest.get("parameters")).get("target"))
            )
            for command, result in zip(commands, results, strict=True):
                append_vina_candidates(
                    candidate_path,
                    command,
                    run_id=str(manifest.get("run_id") or ""),
                    engine_version=engine_version,
                    receptor_state_id=receptor_state_id,
                    raw_output_sha256=str(result.get("output_sha256") or ""),
                )
            manifest["evidence_status"] = "computational_prediction"
        else:
            manifest["execution"]["status"] = "blocked-unknown-docking-engine"
            persist_terminal_state(manifest_path, manifest, candidate_path)
            return 2

        post_errors = audit_docking_candidates(
            candidate_path,
            expected_run_id=str(manifest.get("run_id") or "") or None,
            execution_status="completed-computational-only",
            output_root=manifest_path.parent,
        )
        if post_errors:
            manifest["execution"]["status"] = "failed-post-audit"
            manifest["execution"]["audit_errors"] = post_errors
            persist_terminal_state(manifest_path, manifest, candidate_path)
            return 2
        manifest["execution"]["status"] = "completed-computational-only"
        final_audit = audit_manifest(
            manifest,
            candidate_path,
            package_root=manifest_path.parent,
        )
        if not final_audit["passed"]:
            manifest["execution"]["status"] = "failed-post-audit"
            manifest["execution"]["audit_errors"] = final_audit["errors"]
            persist_terminal_state(manifest_path, manifest, candidate_path)
            return 2
        persist_terminal_state(
            manifest_path,
            manifest,
            candidate_path,
            audit_result=final_audit,
        )
        return 0

    if not commands:
        manifest["execution"]["status"] = "blocked-no-reviewed-argv"
        manifest["execution"]["audit_errors"] = [
            "Execution requires tools.*.command_argv_template; command_template is display-only"
        ]
        write_yaml(manifest_path, manifest)
        return 2
    results: list[dict[str, Any]] = []
    manifest["execution"]["status"] = "running"
    write_yaml(manifest_path, manifest)
    for index, command in enumerate(commands, start=1):
        argv = command.get("argv") if isinstance(command, dict) else None
        if (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(item, str) and item for item in argv)
        ):
            manifest["execution"]["status"] = "blocked-invalid-argv"
            write_yaml(manifest_path, manifest)
            return 2
        completed = subprocess.run(argv, shell=False, cwd=manifest_path.parent)
        results.append({"index": index, "argv": argv, "returncode": completed.returncode})
        manifest["execution"]["results"] = results
        if completed.returncode != 0:
            manifest["execution"]["status"] = "failed"
            write_yaml(manifest_path, manifest)
            return completed.returncode or 1
        write_yaml(manifest_path, manifest)
    manifest["execution"]["status"] = "completed-computational-only"
    manifest["evidence_status"] = "computational_prediction"
    write_yaml(manifest_path, manifest)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["route", "plan", "prepare", "run", "audit"])
    parser.add_argument("--request", type=Path)
    parser.add_argument("--output", type=Path, default=Path("baker_design_run"))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--candidates", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    if args.mode == "audit":
        if not args.manifest:
            parser.error("audit requires --manifest")
        manifest_path = args.manifest.resolve()
        manifest = load_yaml(manifest_path)
        default_candidates = (
            "docking_candidates.csv"
            if manifest.get("route") == DOCKING_ROUTE
            else "candidates.csv"
        )
        candidates = (
            args.candidates.resolve()
            if args.candidates
            else manifest_path.parent / default_candidates
        )
        result = audit_manifest(
            manifest, candidates, package_root=manifest_path.parent
        )
        write_text(manifest_path.parent / "audit_report.md", audit_markdown(result, manifest_path))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["passed"] else 2

    if not args.request:
        parser.error(f"{args.mode} requires --request")
    request = load_yaml(args.request.resolve())
    output = args.output.resolve()
    try:
        manifest = prepare_package(request, args.mode, output)
    except ValueError as error:
        print(str(error))
        return 2
    manifest_path = output / "run_manifest.yaml"
    if args.mode == "run":
        candidate_file = (
            output / "docking_candidates.csv"
            if manifest.get("route") == DOCKING_ROUTE
            else output / "candidates.csv"
        )
        result = audit_manifest(
            manifest, candidate_file, package_root=manifest_path.parent
        )
        write_text(output / "audit_report.md", audit_markdown(result, manifest_path))
        if not result["passed"]:
            manifest["execution"]["status"] = "blocked-by-audit"
            manifest["execution"]["audit_errors"] = result["errors"]
            persist_terminal_state(
                manifest_path, manifest, candidate_file, audit_result=result
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 2
        if not args.execute:
            manifest["execution"]["status"] = "ready-not-executed"
            persist_terminal_state(manifest_path, manifest, candidate_file)
            print("Run package is ready; no commands executed without --execute.")
            return 0
        return execute_manifest(manifest_path, manifest)

    summary = {
        "mode": args.mode,
        "route": manifest["route"],
        "output": str(output),
        "preflight_ready": manifest["preflight"]["ready_for_local_execution"],
        "command_problems": manifest["execution"]["command_problems"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
