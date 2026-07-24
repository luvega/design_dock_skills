#!/usr/bin/env python3
"""Clean-room molecular-docking validation, packaging, and safe execution helpers."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


DOCKING_ROUTE = "molecular-docking-screen"
DOCKING_SCHEMA_VERSION = "1.1"
DIFFDOCK_HOSTED_ENDPOINT = (
    "https://health.api.nvidia.com/v1/molecular-docking/diffdock/generate"
)
ALLOWED_OBJECTIVES = {"pose-prediction", "redocking", "target-focused-screen"}
ALLOWED_ENGINES = {
    "autodock-vina",
    "diffdock-nim-hosted",
    "diffdock-nim-self-hosted",
}
DOCKING_CANDIDATE_FIELDS = [
    "run_id",
    "ligand_id",
    "chemical_state_id",
    "pose_id",
    "pose_rank_within_ligand",
    "engine",
    "engine_version",
    "receptor_state_id",
    "seed",
    "metric_name",
    "metric_value",
    "metric_unit",
    "metric_role",
    "rank_scope",
    "input_sha256",
    "raw_output_sha256",
    "output_path",
    "evidence_status",
    "experimental_status",
    "notes",
]

MODEL_TOOL_FIELDS = (
    "repository",
    "repo_path",
    "commit",
    "license",
    "checkpoint",
    "checkpoint_sha256",
    "command_template",
)
EXECUTABLE_TOOL_FIELDS = ("repository", "executable", "version", "license")
HOSTED_TOOL_FIELDS = (
    "endpoint",
    "service_version",
    "terms_url",
    "license",
    "auth_env",
)
SELF_HOSTED_TOOL_FIELDS = ("endpoint", "repository", "version", "license")
TOKEN_PATTERNS = (
    re.compile(r"\bnvapi-[A-Za-z0-9_-]{8,}\b", re.I),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{10,}\b", re.I),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{10,}\b", re.I),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}\b", re.I),
    re.compile(r"\b(?:Bearer|Basic)\s+\S+", re.I),
)
SENSITIVE_KEY_RE = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|token|secret|password|authorization|credential)(?:$|[_-])",
    re.I,
)
SENSITIVE_KEY_ALLOWLIST = {
    "auth_env",
    "credential_rotation_acknowledged",
    "data_classification",
}
SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
WINDOWS_DEVICE_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.I)
PLACEHOLDER_RE = re.compile(
    r"(?:\breplace_with(?:_[a-z0-9_]*)?\b|"
    r"\b(?:todo|tbd|changeme|unresolved|unknown|planning_only)\b)",
    re.I,
)
POSITIVE_PREDICTION_CLAIM_RE = re.compile(
    r"\b(?:affinity|free[\s-]+energy|active|selective|inhibitor|hit|lead|binder|validated)\b",
    re.I,
)
VINA_RESULT_RE = re.compile(
    r"^\s*REMARK\s+VINA\s+RESULT:\s+"
    r"(?P<score>[+-]?(?:\d+(?:\.\d*)?|\.\d+))"
    r"(?:\s+(?P<rmsd_lb>[+-]?(?:\d+(?:\.\d*)?|\.\d+)))?"
    r"(?:\s+(?P<rmsd_ub>[+-]?(?:\d+(?:\.\d*)?|\.\d+)))?",
    re.I,
)


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        text = value.strip()
        return not text or bool(PLACEHOLDER_RE.search(text))
    return False


def _valid_required_text(value: Any) -> bool:
    return isinstance(value, str) and not _is_missing(value)


def _contains_positive_prediction_claim(value: Any) -> bool:
    text = str(value or "").casefold()
    for match in POSITIVE_PREDICTION_CLAIM_RE.finditer(text):
        sentence_start = max(
            text.rfind(".", 0, match.start()),
            text.rfind(";", 0, match.start()),
            text.rfind("!", 0, match.start()),
            text.rfind("?", 0, match.start()),
        )
        prefix = text[sentence_start + 1 : match.start()]
        contrast = max(
            prefix.rfind(" but "),
            prefix.rfind(" however "),
            prefix.rfind(" although "),
        )
        negations = tuple(
            item.start()
            for item in re.finditer(
                r"\b(?:not|no|non|neither|never|without|cannot|can't|does\s+not|do\s+not|"
                r"did\s+not|fails?\s+to)\b",
                prefix,
            )
        )
        if not negations or max(negations) < contrast:
            return True
    return False


def _contains_secret(value: Any, *, parent_key: str = "") -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            normalized = key_text.casefold()
            if (
                normalized not in SENSITIVE_KEY_ALLOWLIST
                and SENSITIVE_KEY_RE.search(normalized)
                and item not in (None, "", False, [], {})
            ):
                return True
            if _contains_secret(item, parent_key=key_text):
                return True
        return False
    if isinstance(value, (list, tuple, set)):
        return any(_contains_secret(item, parent_key=parent_key) for item in value)
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in TOKEN_PATTERNS)
    return False


def reject_plaintext_credentials(value: Any) -> None:
    """Reject credential-like strings recursively without echoing the secret."""
    if _contains_secret(value):
        raise ValueError(
            "Request or manifest contains a credential-like plaintext token; "
            "use an environment-variable reference and rotate the exposed credential"
        )


def validate_stable_id(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value
    if (
        text in {".", ".."}
        or text.endswith((".", " "))
        or not SAFE_ID_RE.fullmatch(text)
    ):
        return False
    basename = text.split(".", 1)[0].upper()
    return basename not in WINDOWS_DEVICE_NAMES


def canonical_stable_id(value: Any) -> str:
    if not validate_stable_id(value):
        raise ValueError("Unsafe stable ID")
    return str(value).casefold()


def safe_output_path(root: Path, *parts: str) -> Path:
    resolved_root = root.resolve()
    candidate = resolved_root.joinpath(*parts).resolve()
    if not candidate.is_relative_to(resolved_root):
        raise ValueError("Output path escapes the package output root")
    return candidate


def argv_sha256(argv: Sequence[str]) -> str:
    return hashlib.sha256(
        json.dumps(list(argv), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def argv_plan_sha256(commands: Sequence[Mapping[str, Any]]) -> str:
    digests = [str(command.get("argv_sha256") or "") for command in commands]
    return hashlib.sha256(
        json.dumps(digests, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def required_tools(request_or_manifest: Mapping[str, Any]) -> list[str]:
    docking = _as_mapping(request_or_manifest.get("docking"))
    if not docking:
        parameters = _as_mapping(request_or_manifest.get("parameters"))
        docking = _as_mapping(parameters.get("docking"))
    engine = (
        str(docking.get("engine") or "").strip()
        if isinstance(docking.get("engine"), str)
        else ""
    )
    tools: list[str] = []
    if engine == "autodock-vina":
        tools.append("autodock_vina")
    if engine == "diffdock-nim-hosted":
        tools.append("diffdock_hosted")
    if engine == "diffdock-nim-self-hosted":
        tools.append("diffdock_self_hosted")
    if bool(_as_mapping(docking.get("preparation")).get("use_meeko")):
        tools.append("meeko")
    if bool(_as_mapping(docking.get("analysis")).get("plip")):
        tools.append("plip")
    if bool(_as_mapping(docking.get("visualization")).get("pymol")):
        tools.append("pymol")
    return tools


def tool_required_fields(tool_name: str) -> tuple[str, ...]:
    if tool_name in {"autodock_vina", "meeko", "plip", "pymol"}:
        return EXECUTABLE_TOOL_FIELDS
    if tool_name == "diffdock_hosted":
        return HOSTED_TOOL_FIELDS
    if tool_name == "diffdock_self_hosted":
        return SELF_HOSTED_TOOL_FIELDS
    return MODEL_TOOL_FIELDS


def _valid_vector(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 3
        and all(
            isinstance(item, (int, float))
            and not isinstance(item, bool)
            and math.isfinite(float(item))
            for item in value
        )
    )


def _chemical_state_gaps(ligand: Mapping[str, Any], prefix: str) -> list[str]:
    gaps: list[str] = []
    if _is_missing(ligand.get("ligand_id")):
        gaps.append(f"{prefix}.ligand_id")
    elif not validate_stable_id(ligand.get("ligand_id")):
        gaps.append(f"{prefix}.ligand_id-unsafe")
    state = ligand.get("chemical_state")
    if isinstance(state, Mapping):
        if not _valid_required_text(state.get("strategy")):
            gaps.append(f"{prefix}.chemical_state.strategy")
        if not _valid_required_text(state.get("chemical_state_id")):
            gaps.append(f"{prefix}.chemical_state.chemical_state_id")
        elif not validate_stable_id(state.get("chemical_state_id")):
            gaps.append(f"{prefix}.chemical_state.chemical_state_id-unsafe")
    else:
        gaps.append(f"{prefix}.chemical_state")
        if not _valid_required_text(state):
            gaps.append(f"{prefix}.chemical_state.strategy")
        if _is_missing(ligand.get("chemical_state_id")):
            gaps.append(f"{prefix}.chemical_state_id")
        elif not validate_stable_id(ligand.get("chemical_state_id")):
            gaps.append(f"{prefix}.chemical_state_id-unsafe")
    return gaps


def _library_gaps(library: Mapping[str, Any]) -> list[str]:
    gaps: list[str] = []
    manifest_file = library.get("manifest_file")
    if _is_missing(manifest_file):
        return ["ligand_library.manifest_file"]
    if _is_missing(library.get("chemical_state_strategy")):
        gaps.append("ligand_library.chemical_state_strategy")
    path = Path(str(manifest_file)).expanduser()
    if not path.is_file():
        gaps.append("ligand_library.manifest_file-unresolved")
        return gaps
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or [])
            for field in ("ligand_id", "chemical_state_id"):
                if field not in fields:
                    gaps.append(f"ligand_library.manifest_file.column:{field}")
            if not ({"file", "ligand_file"} & fields):
                gaps.append("ligand_library.manifest_file.column:file-or-ligand_file")
            seen: set[tuple[str, str]] = set()
            row_count = 0
            for line, row in enumerate(reader, start=2):
                row_count += 1
                ligand_id = str(row.get("ligand_id") or "").strip()
                chemical_state_id = str(row.get("chemical_state_id") or "").strip()
                ligand_file = str(row.get("file") or row.get("ligand_file") or "").strip()
                if not ligand_id:
                    gaps.append(f"ligand_library.line-{line}.ligand_id")
                elif not validate_stable_id(ligand_id):
                    gaps.append(f"ligand_library.line-{line}.ligand_id-unsafe")
                if not chemical_state_id:
                    gaps.append(f"ligand_library.line-{line}.chemical_state_id")
                elif not validate_stable_id(chemical_state_id):
                    gaps.append(
                        f"ligand_library.line-{line}.chemical_state_id-unsafe"
                    )
                identity = (
                    ligand_id.casefold(),
                    chemical_state_id.casefold(),
                )
                if all(identity) and identity in seen:
                    gaps.append(
                        f"ligand_library.line-{line}.canonical-id-collision"
                    )
                seen.add(identity)
                if not ligand_file:
                    gaps.append(f"ligand_library.line-{line}.file")
            if row_count == 0:
                gaps.append("ligand_library.empty")
    except (OSError, csv.Error, UnicodeError):
        gaps.append("ligand_library.manifest_file-unreadable")
    return gaps


def docking_gaps(
    request: Mapping[str, Any],
    *,
    strict_run: bool = False,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """Return deterministic docking contract gaps; reject plaintext tokens first."""
    reject_plaintext_credentials(request)
    gaps: list[str] = []
    raw_target = request.get("target")
    raw_docking = request.get("docking")
    raw_tools = request.get("tools")
    if not isinstance(raw_target, Mapping):
        gaps.append("target")
    if not isinstance(raw_docking, Mapping):
        gaps.append("docking")
    if raw_tools is not None and not isinstance(raw_tools, Mapping):
        gaps.append("tools")
    target = _as_mapping(raw_target)
    docking = _as_mapping(raw_docking)
    objective = (
        str(docking.get("objective")).strip()
        if isinstance(docking.get("objective"), str)
        else ""
    )
    engine = (
        str(docking.get("engine")).strip()
        if isinstance(docking.get("engine"), str)
        else ""
    )
    if objective not in ALLOWED_OBJECTIVES:
        gaps.append("docking.objective")
    if engine not in ALLOWED_ENGINES:
        gaps.append("docking.engine")

    for field in ("structure_file", "biological_state"):
        if not _valid_required_text(target.get(field)):
            gaps.append(f"target.{field}")
    chains = target.get("chains")
    if (
        not isinstance(chains, list)
        or not chains
        or not all(_valid_required_text(chain) for chain in chains)
    ):
        gaps.append("target.chains")
    raw_preparation = target.get("receptor_preparation")
    if not isinstance(raw_preparation, Mapping):
        gaps.append("target.receptor_preparation")
    preparation = _as_mapping(raw_preparation)
    for field in (
        "protonation",
        "missing_residues",
        "alternate_locations",
        "hetatm_policy",
    ):
        if not _valid_required_text(preparation.get(field)):
            gaps.append(f"target.receptor_preparation.{field}")
    hetatm_policy = (
        str(preparation.get("hetatm_policy") or "").strip().casefold()
        if isinstance(preparation.get("hetatm_policy"), str)
        else ""
    )
    if hetatm_policy.replace("-", "_").replace(" ", "_") == "remove_all":
        gaps.append("target.receptor_preparation.hetatm_policy-remove_all-forbidden")
    for field in ("assembly", "mutation_state"):
        if (strict_run or field in target) and not _valid_required_text(
            target.get(field)
        ):
            gaps.append(f"target.{field}")
    if (
        "receptor_state_id" in target
        and not validate_stable_id(target.get("receptor_state_id"))
    ):
        gaps.append("target.receptor_state_id-unsafe")
    source_structure = target.get("source_structure_file")
    source_sha256 = target.get("source_structure_sha256")
    if source_structure is not None:
        if not _valid_required_text(source_structure):
            gaps.append("target.source_structure_file")
        elif not Path(str(source_structure)).expanduser().is_file():
            gaps.append("target.source_structure_file-missing")
    if source_sha256 is not None:
        if not isinstance(source_sha256, str) or not SHA256_RE.fullmatch(
            source_sha256.strip()
        ):
            gaps.append("target.source_structure_sha256")
        elif not _valid_required_text(source_structure):
            gaps.append("target.source_structure_file")
        else:
            source_path = Path(str(source_structure)).expanduser()
            if not source_path.is_file():
                gaps.append("target.source_structure_file-missing")
            else:
                try:
                    if _sha256_file(source_path) != source_sha256.strip().casefold():
                        gaps.append(
                            "target.source_structure_sha256-mismatch"
                        )
                except OSError:
                    gaps.append("target.source_structure_file-unreadable")

    ligand = request.get("ligand")
    library = request.get("ligand_library")
    if bool(ligand) == bool(library):
        gaps.append("ligand-xor-ligand_library")
    elif isinstance(ligand, Mapping):
        if not _valid_required_text(ligand.get("file")):
            gaps.append("ligand.file")
        gaps.extend(_chemical_state_gaps(ligand, "ligand"))
    elif isinstance(library, Mapping):
        gaps.extend(_library_gaps(library))
    elif ligand:
        gaps.append("ligand")
    elif library:
        gaps.append("ligand_library")

    if engine == "autodock-vina":
        raw_site = docking.get("binding_site")
        if not isinstance(raw_site, Mapping):
            gaps.append("docking.binding_site")
        site = _as_mapping(raw_site)
        if not _valid_required_text(site.get("source")):
            gaps.append("docking.binding_site.source")
        if not _valid_vector(site.get("center")):
            gaps.append("docking.binding_site.center")
        if not _valid_vector(site.get("size")) or (
            _valid_vector(site.get("size")) and any(float(item) <= 0 for item in site["size"])
        ):
            gaps.append("docking.binding_site.size")
        has_seed_key = "seed" in docking
        has_seeds_key = "seeds" in docking
        if has_seed_key and has_seeds_key:
            gaps.append("docking.seed-xor-seeds")
        has_seed = (
            "seed" in docking
            and isinstance(docking.get("seed"), int)
            and not isinstance(docking.get("seed"), bool)
        )
        seeds = docking.get("seeds")
        has_seeds = (
            isinstance(seeds, list)
            and bool(seeds)
            and all(isinstance(seed, int) and not isinstance(seed, bool) for seed in seeds)
        )
        if not (has_seed or has_seeds):
            gaps.append("docking.seed-or-seeds")
        if has_seeds and len(set(seeds)) != len(seeds):
            gaps.append("docking.seeds-duplicate")
        if (
            not isinstance(docking.get("exhaustiveness"), int)
            or isinstance(docking.get("exhaustiveness"), bool)
            or int(docking.get("exhaustiveness") or 0) <= 0
        ):
            gaps.append("docking.exhaustiveness")

    max_poses = 100 if engine == "diffdock-nim-hosted" else None
    if (
        not isinstance(docking.get("num_poses"), int)
        or isinstance(docking.get("num_poses"), bool)
        or int(docking.get("num_poses") or 0) <= 0
        or (max_poses is not None and int(docking.get("num_poses") or 0) > max_poses)
    ):
        gaps.append("docking.num_poses")
    if engine == "diffdock-nim-hosted":
        time_divisions = docking.get("time_divisions", 10)
        steps = docking.get("steps", 18)
        if (
            not isinstance(time_divisions, int)
            or isinstance(time_divisions, bool)
            or not 1 <= time_divisions <= 20
        ):
            gaps.append("docking.time_divisions")
        if (
            not isinstance(steps, int)
            or isinstance(steps, bool)
            or not 1 <= steps <= 18
        ):
            gaps.append("docking.steps")
        for field in ("save_trajectory", "skip_gen_conformer", "is_staged"):
            if field in docking and not isinstance(docking[field], bool):
                gaps.append(f"docking.{field}")
    raw_validation = docking.get("validation")
    if raw_validation is not None and not isinstance(raw_validation, Mapping):
        gaps.append("docking.validation")
    validation = _as_mapping(raw_validation)
    if objective == "target-focused-screen":
        controls = validation.get("controls")
        if (
            not isinstance(controls, list)
            or not controls
            or not all(_valid_required_text(control) for control in controls)
        ):
            gaps.append("docking.validation.controls")
    if objective == "redocking":
        redocking_text_fields = (
            "reference_pose",
            "symmetry_handling",
            "receptor_alignment",
            "heavy_atom_rule",
            "rmsd_tool",
            "rmsd_tool_version",
            "pose_selection",
        )
        for field in redocking_text_fields:
            if (
                strict_run or field in validation
            ) and not _valid_required_text(validation.get(field)):
                gaps.append(f"docking.validation.{field}")
        top_n = validation.get("top_n")
        if (
            strict_run or "top_n" in validation
        ) and (
            not isinstance(top_n, int)
            or isinstance(top_n, bool)
            or top_n <= 0
        ):
            gaps.append("docking.validation.top_n")
        reference_pose = validation.get("reference_pose")
        reference_sha256 = validation.get("reference_pose_sha256")
        if (
            strict_run
            or "reference_pose" in validation
            or "reference_pose_sha256" in validation
        ):
            if not isinstance(reference_sha256, str) or not SHA256_RE.fullmatch(
                reference_sha256.strip()
            ):
                gaps.append("docking.validation.reference_pose_sha256")
            elif _valid_required_text(reference_pose):
                reference_path = Path(str(reference_pose)).expanduser()
                if not reference_path.is_file():
                    gaps.append("docking.validation.reference_pose-missing")
                else:
                    try:
                        if (
                            _sha256_file(reference_path)
                            != reference_sha256.strip().casefold()
                        ):
                            gaps.append(
                                "docking.validation."
                                "reference_pose_sha256-mismatch"
                            )
                    except OSError:
                        gaps.append(
                            "docking.validation.reference_pose-unreadable"
                        )
    if strict_run and objective == "redocking":
        atom_mapping = validation.get("atom_mapping")
        if not isinstance(atom_mapping, Mapping):
            gaps.append("docking.validation.atom_mapping")
        else:
            mapping_path = atom_mapping.get("file")
            if not _valid_required_text(mapping_path):
                gaps.append("docking.validation.atom_mapping.file")
            mapping_sha256 = atom_mapping.get("sha256")
            if not isinstance(mapping_sha256, str) or not SHA256_RE.fullmatch(
                mapping_sha256.strip()
            ):
                gaps.append("docking.validation.atom_mapping.sha256")
            if _valid_required_text(mapping_path):
                mapping_file = Path(str(mapping_path)).expanduser()
                if not mapping_file.is_file():
                    gaps.append("docking.validation.atom_mapping.file-missing")
                elif (
                    isinstance(mapping_sha256, str)
                    and SHA256_RE.fullmatch(mapping_sha256.strip())
                ):
                    try:
                        if _sha256_file(mapping_file) != mapping_sha256.strip().casefold():
                            gaps.append(
                                "docking.validation.atom_mapping.sha256-mismatch"
                            )
                    except OSError:
                        gaps.append(
                            "docking.validation.atom_mapping.file-unreadable"
                        )
        reference_pose = validation.get("reference_pose")
        if _valid_required_text(reference_pose):
            if not Path(str(reference_pose)).expanduser().is_file():
                gaps.append("docking.validation.reference_pose-missing")
    if engine == "diffdock-nim-hosted":
        raw_external = request.get("external_service")
        if not isinstance(raw_external, Mapping):
            gaps.append("external_service")
        external = _as_mapping(raw_external)
        if external.get("authorized") is not True:
            gaps.append("external_service.authorized")
        if external.get("credential_rotation_acknowledged") is not True:
            gaps.append("external_service.credential_rotation_acknowledged")
        if external.get("data_classification") not in {"public", "non-sensitive"}:
            gaps.append("external_service.data_classification")
        if external.get("auth_env") != "NVIDIA_API_KEY":
            gaps.append("external_service.auth_env")
        if external.get("endpoint") != DIFFDOCK_HOSTED_ENDPOINT:
            gaps.append("external_service.endpoint")
        if strict_run:
            environment = os.environ if environ is None else environ
            if not environment.get("NVIDIA_API_KEY"):
                gaps.append("environment:NVIDIA_API_KEY")
    target_file_text = str(target.get("structure_file") or "")
    if target_file_text:
        target_extension = Path(target_file_text).suffix.casefold()
        if engine == "autodock-vina":
            use_meeko = bool(_as_mapping(docking.get("preparation")).get("use_meeko"))
            if target_extension != ".pdbqt":
                if not use_meeko:
                    gaps.append("target.structure_file-format:pdbqt")
                elif not _as_mapping(docking.get("preparation")).get("receptor_pdbqt"):
                    gaps.append("docking.preparation.receptor_pdbqt")
        elif engine == "diffdock-nim-hosted" and target_extension != ".pdb":
            gaps.append("target.structure_file-format:pdb")
    try:
        format_records = ligand_records(request)
    except (OSError, csv.Error, UnicodeError):
        format_records = []
    use_meeko = bool(_as_mapping(docking.get("preparation")).get("use_meeko"))
    single_ligand = bool(request.get("ligand"))
    for record in format_records:
        extension = Path(record.get("file") or "").suffix.casefold()
        label = (
            "ligand"
            if single_ligand
            else (
                f"ligand_library:{record.get('ligand_id') or 'unresolved'}:"
                f"{record.get('chemical_state_id') or 'unresolved'}"
            )
        )
        if engine == "autodock-vina" and extension != ".pdbqt":
            if not use_meeko:
                gaps.append(f"{label}.file-format:pdbqt")
            elif not record.get("prepared_file"):
                gaps.append(f"{label}.prepared_file")
        if engine == "diffdock-nim-hosted" and extension not in {".sdf", ".mol2"}:
            gaps.append(f"{label}.file-format:sdf-or-mol2")
    if strict_run:
        if bool(_as_mapping(docking.get("analysis")).get("plip")):
            gaps.append("docking.analysis.plip adapter-not-implemented")
        if bool(_as_mapping(docking.get("visualization")).get("pymol")):
            gaps.append("docking.visualization.pymol adapter-not-implemented")
        target_path = Path(str(target.get("structure_file") or "")).expanduser()
        if not target_path.is_file():
            gaps.append("target.structure_file-missing")
        elif engine == "autodock-vina":
            use_meeko = bool(_as_mapping(docking.get("preparation")).get("use_meeko"))
            if target_path.suffix.casefold() != ".pdbqt":
                if not use_meeko:
                    gaps.append("target.structure_file-format:pdbqt")
                else:
                    receptor_pdbqt = Path(
                        str(
                            _as_mapping(docking.get("preparation")).get(
                                "receptor_pdbqt"
                            )
                            or ""
                        )
                    ).expanduser()
                    if not str(receptor_pdbqt) or str(receptor_pdbqt) == ".":
                        gaps.append("docking.preparation.receptor_pdbqt")
                    elif (
                        not receptor_pdbqt.is_file()
                        or receptor_pdbqt.suffix.casefold() != ".pdbqt"
                    ):
                        gaps.append(
                            "docking.preparation.receptor_pdbqt-missing-or-invalid"
                        )
        elif engine == "diffdock-nim-hosted" and target_path.suffix.casefold() != ".pdb":
            gaps.append("target.structure_file-format:pdb")

        records: list[dict[str, str]] = []
        try:
            records = ligand_records(request)
        except (OSError, csv.Error, UnicodeError):
            gaps.append("ligand-inputs-unreadable")
        use_meeko = bool(_as_mapping(docking.get("preparation")).get("use_meeko"))
        single_ligand = bool(request.get("ligand"))
        for record in records:
            ligand_path = Path(record["file"]).expanduser()
            label = (
                "ligand"
                if single_ligand
                else (
                    f"ligand_library:{record.get('ligand_id') or 'unresolved'}:"
                    f"{record.get('chemical_state_id') or 'unresolved'}"
                )
            )
            if not ligand_path.is_file():
                gaps.append(f"{label}.file-missing")
                continue
            extension = ligand_path.suffix.casefold()
            if engine == "autodock-vina" and extension != ".pdbqt":
                if not use_meeko:
                    gaps.append(f"{label}.file-format:pdbqt")
                else:
                    prepared_path = Path(
                        str(record.get("prepared_file") or "")
                    ).expanduser()
                    if not str(record.get("prepared_file") or ""):
                        gaps.append(f"{label}.prepared_file")
                    elif (
                        not prepared_path.is_file()
                        or prepared_path.suffix.casefold() != ".pdbqt"
                    ):
                        gaps.append(f"{label}.prepared_file-missing-or-invalid")
            if engine == "diffdock-nim-hosted" and extension not in {".sdf", ".mol2"}:
                gaps.append(f"{label}.file-format:sdf-or-mol2")
    return sorted(set(gaps))


def stable_receptor_state_id(target: Mapping[str, Any]) -> str:
    configured = str(target.get("receptor_state_id") or "").strip()
    if configured:
        if not validate_stable_id(configured):
            raise ValueError("Explicit receptor_state_id is not a canonical stable ID")
        return configured
    identity = {
        "structure_file": str(target.get("structure_file") or ""),
        "chains": target.get("chains") or [],
        "biological_state": target.get("biological_state"),
        "receptor_preparation": target.get("receptor_preparation") or {},
    }
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return "receptor-state-" + digest[:16]


def validate_docking_tools(
    request_or_manifest: Mapping[str, Any],
) -> list[str]:
    gaps: list[str] = []
    raw_configured = request_or_manifest.get("tools")
    if raw_configured is not None and not isinstance(raw_configured, Mapping):
        gaps.append("tools")
    configured = _as_mapping(raw_configured)
    for tool_name in required_tools(request_or_manifest):
        raw_tool = configured.get(tool_name)
        if not isinstance(raw_tool, Mapping):
            gaps.append(f"tools.{tool_name}")
        tool = _as_mapping(raw_tool)
        for field in tool_required_fields(tool_name):
            if not _valid_required_text(tool.get(field)):
                gaps.append(f"tools.{tool_name}.{field}")
        if tool_name == "diffdock_hosted":
            if tool.get("endpoint") not in {None, DIFFDOCK_HOSTED_ENDPOINT}:
                gaps.append("tools.diffdock_hosted.endpoint")
            if tool.get("auth_env") not in {None, "NVIDIA_API_KEY"}:
                gaps.append("tools.diffdock_hosted.auth_env")
    return sorted(set(gaps))


def preflight_docking(
    request: Mapping[str, Any],
    *,
    strict_run: bool,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    issues = docking_gaps(request, strict_run=strict_run, environ=environ)
    tool_gaps = validate_docking_tools(request)
    issues.extend(tool_gaps)
    docking = _as_mapping(request.get("docking"))
    engine = (
        str(docking.get("engine") or "").strip()
        if isinstance(docking.get("engine"), str)
        else ""
    )
    if engine == "autodock-vina":
        tool = _as_mapping(_as_mapping(request.get("tools")).get("autodock_vina"))
        executable = str(tool.get("executable") or "").strip()
        resolved = (
            str(Path(executable).expanduser().resolve())
            if executable and Path(executable).expanduser().is_file()
            else shutil.which(executable) if executable else None
        )
        if not resolved:
            issues.append("tools.autodock_vina.executable is not available on this machine")
    return {
        "checked_at": _utc_now(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "engine": engine,
        "strict_run": strict_run,
        "ready_for_local_execution": not issues,
        "issues": sorted(set(issues)),
    }


def ligand_records(request: Mapping[str, Any]) -> list[dict[str, str]]:
    ligand = request.get("ligand")
    if isinstance(ligand, Mapping) and ligand:
        state = ligand.get("chemical_state")
        state_id = (
            str(state.get("chemical_state_id") or "")
            if isinstance(state, Mapping)
            else str(ligand.get("chemical_state_id") or "")
        )
        return [
            {
                "ligand_id": str(ligand.get("ligand_id") or ""),
                "chemical_state_id": state_id,
                "file": str(ligand.get("file") or ""),
                "prepared_file": str(ligand.get("prepared_file") or ""),
            }
        ]
    library = _as_mapping(request.get("ligand_library"))
    if not library:
        return []
    path = Path(str(library.get("manifest_file") or "")).expanduser()
    records: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            records.append(
                {
                    "ligand_id": str(row.get("ligand_id") or "").strip(),
                    "chemical_state_id": str(row.get("chemical_state_id") or "").strip(),
                    "file": str(row.get("file") or row.get("ligand_file") or "").strip(),
                    "prepared_file": str(row.get("prepared_file") or "").strip(),
                }
            )
    return records


def _seeds(docking: Mapping[str, Any]) -> list[int]:
    if isinstance(docking.get("seeds"), list) and docking["seeds"]:
        return [int(seed) for seed in docking["seeds"]]
    return [int(docking["seed"])]


def build_vina_argv_commands(
    request: Mapping[str, Any], output_dir: Path
) -> list[dict[str, Any]]:
    gaps = docking_gaps(request)
    if gaps:
        raise ValueError("Invalid docking request: " + ", ".join(gaps))
    docking = request["docking"]
    if docking["engine"] != "autodock-vina":
        raise ValueError("Vina argv can only be built for engine=autodock-vina")
    site = docking["binding_site"]
    target = request["target"]
    target_path = Path(str(target["structure_file"])).expanduser()
    receptor_for_vina = (
        str(target_path)
        if target_path.suffix.casefold() == ".pdbqt"
        else str((docking.get("preparation") or {}).get("receptor_pdbqt") or "")
    )
    executable = str(
        ((request.get("tools") or {}).get("autodock_vina") or {}).get("executable")
        or ""
    )
    commands: list[dict[str, Any]] = []
    records = ligand_records(request)
    seed_values = _seeds(docking)
    multi_seed = len(seed_values) > 1
    for ligand in records:
        ligand_path = Path(ligand["file"]).expanduser()
        ligand_for_vina = (
            str(ligand_path)
            if ligand_path.suffix.casefold() == ".pdbqt"
            else str(ligand.get("prepared_file") or "")
        )
        vina_ligand_path = Path(ligand_for_vina).expanduser()
        input_hash = (
            _sha256_file(vina_ligand_path) if vina_ligand_path.is_file() else ""
        )
        for seed in seed_values:
            destination = safe_output_path(
                output_dir,
                "docking_outputs",
                canonical_stable_id(ligand["ligand_id"]),
                canonical_stable_id(ligand["chemical_state_id"]),
                f"seed_{seed}",
                "poses.pdbqt",
            )
            argv = [
                executable,
                "--receptor",
                receptor_for_vina,
                "--ligand",
                ligand_for_vina,
                "--center_x",
                str(site["center"][0]),
                "--center_y",
                str(site["center"][1]),
                "--center_z",
                str(site["center"][2]),
                "--size_x",
                str(site["size"][0]),
                "--size_y",
                str(site["size"][1]),
                "--size_z",
                str(site["size"][2]),
                "--seed",
                str(seed),
                "--exhaustiveness",
                str(docking["exhaustiveness"]),
                "--num_modes",
                str(docking["num_poses"]),
                "--out",
                str(destination),
            ]
            commands.append(
                {
                    "argv": argv,
                    "ligand_id": ligand["ligand_id"],
                    "chemical_state_id": ligand["chemical_state_id"],
                    "seed": seed,
                    "input_sha256": input_hash,
                    "output_path": str(destination),
                    "rank_scope": (
                        "within-ligand-and-seed" if multi_seed else "within-ligand"
                    ),
                    "argv_sha256": argv_sha256(argv),
                }
            )
    return commands


def argv_to_powershell(commands: Sequence[Mapping[str, Any]]) -> str:
    def quote(value: Any) -> str:
        text = str(value).replace("'", "''")
        return "'" + text + "'"

    lines = [
        "# Generated review surface only. Execution uses audited argv with shell=False.",
        "$ErrorActionPreference = 'Stop'",
        "",
    ]
    for command in commands:
        lines.append("& " + " ".join(quote(item) for item in command.get("argv") or []))
    return "\n".join(lines).rstrip() + "\n"


HOSTED_PAYLOAD_FIELDS = {
    "protein",
    "ligand",
    "ligand_file_type",
    "num_poses",
    "time_divisions",
    "steps",
    "save_trajectory",
    "skip_gen_conformer",
    "is_staged",
}


def build_hosted_requests(request: Mapping[str, Any]) -> list[dict[str, Any]]:
    gaps = docking_gaps(request)
    if gaps:
        raise ValueError("Invalid hosted docking request: " + ", ".join(gaps))
    docking = request.get("docking") or {}
    if docking.get("engine") != "diffdock-nim-hosted":
        raise ValueError("Hosted requests require engine=diffdock-nim-hosted")
    protein_path = Path(
        str((request.get("target") or {}).get("structure_file") or "")
    ).expanduser()
    if not protein_path.is_file() or protein_path.suffix.casefold() != ".pdb":
        raise ValueError("Hosted DiffDock requires a readable PDB protein file")
    protein_text = protein_path.read_text(encoding="utf-8")
    requests: list[dict[str, Any]] = []
    for ligand in ligand_records(request):
        ligand_path = Path(ligand["file"]).expanduser()
        extension = ligand_path.suffix.casefold()
        if not ligand_path.is_file() or extension not in {".sdf", ".mol2"}:
            raise ValueError("Hosted DiffDock requires readable SDF or MOL2 ligand files")
        payload = {
            "protein": protein_text,
            "ligand": ligand_path.read_text(encoding="utf-8"),
            "ligand_file_type": extension.removeprefix("."),
            "num_poses": int(docking["num_poses"]),
            "time_divisions": int(docking.get("time_divisions", 10)),
            "steps": int(docking.get("steps", 18)),
            "save_trajectory": bool(docking.get("save_trajectory", False)),
            "skip_gen_conformer": bool(docking.get("skip_gen_conformer", False)),
            "is_staged": bool(docking.get("is_staged", False)),
        }
        requests.append(
            {
                "ligand_id": ligand["ligand_id"],
                "chemical_state_id": ligand["chemical_state_id"],
                "ligand_path": str(ligand_path.resolve()),
                "input_sha256": _sha256_file(ligand_path),
                "payload": payload,
            }
        )
    return requests


def validate_hosted_payload(payload: Mapping[str, Any]) -> None:
    if set(payload) != HOSTED_PAYLOAD_FIELDS:
        raise ValueError("Hosted DiffDock payload does not match the approved schema")
    if not isinstance(payload["protein"], str) or not payload["protein"]:
        raise ValueError("Hosted DiffDock protein must be non-empty PDB text")
    if not isinstance(payload["ligand"], str) or not payload["ligand"]:
        raise ValueError("Hosted DiffDock ligand must be non-empty file text")
    if payload["ligand_file_type"] not in {"sdf", "mol2"}:
        raise ValueError("Hosted DiffDock ligand_file_type must be sdf or mol2")
    if (
        not isinstance(payload["num_poses"], int)
        or isinstance(payload["num_poses"], bool)
        or not 1 <= payload["num_poses"] <= 100
    ):
        raise ValueError("Hosted DiffDock num_poses must be between 1 and 100")
    if (
        not isinstance(payload["time_divisions"], int)
        or isinstance(payload["time_divisions"], bool)
        or not 1 <= payload["time_divisions"] <= 20
    ):
        raise ValueError("Hosted DiffDock time_divisions must be between 1 and 20")
    if (
        not isinstance(payload["steps"], int)
        or isinstance(payload["steps"], bool)
        or not 1 <= payload["steps"] <= 18
    ):
        raise ValueError("Hosted DiffDock steps must be between 1 and 18")
    for field in ("save_trajectory", "skip_gen_conformer", "is_staged"):
        if not isinstance(payload[field], bool):
            raise ValueError(f"Hosted DiffDock {field} must be boolean")


def materialize_diffdock_response(
    response: Mapping[str, Any],
    request_record: Mapping[str, Any],
    output_root: Path,
    *,
    run_id: str = "",
    engine_version: str = "",
    receptor_state_id: str = "",
    write_outputs: bool = True,
) -> list[dict[str, Any]]:
    if str(response.get("status") or "").casefold() not in {"success", "completed"}:
        raise ValueError("Hosted DiffDock response did not report success")
    positions = response.get("ligand_positions")
    confidences = response.get("position_confidence")
    if (
        not isinstance(positions, list)
        or not positions
        or not isinstance(confidences, list)
        or len(confidences) != len(positions)
    ):
        raise ValueError("Hosted DiffDock response contains zero or inconsistent poses")
    ligand_id = str(request_record.get("ligand_id") or "")
    chemical_state_id = str(request_record.get("chemical_state_id") or "")
    if not validate_stable_id(ligand_id) or not validate_stable_id(chemical_state_id):
        raise ValueError("Hosted DiffDock response target IDs are unsafe")
    rows: list[dict[str, Any]] = []
    for rank, (sdf_text, confidence) in enumerate(
        zip(positions, confidences, strict=True), start=1
    ):
        if not isinstance(sdf_text, str) or not sdf_text.strip():
            raise ValueError("Hosted DiffDock pose is not non-empty SDF text")
        if (
            not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
            or not math.isfinite(float(confidence))
        ):
            raise ValueError("Hosted DiffDock confidence must be numeric and finite")
        pose_bytes = sdf_text.encode("utf-8")
        raw_output_sha256 = hashlib.sha256(pose_bytes).hexdigest()
        destination = safe_output_path(
            output_root,
            "docking_outputs",
            canonical_stable_id(ligand_id),
            canonical_stable_id(chemical_state_id),
            "hosted",
            f"pose_{rank}.sdf",
        )
        if write_outputs:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(sdf_text, encoding="utf-8", newline="\n")
        rows.append(
            {
                "run_id": run_id,
                "ligand_id": ligand_id,
                "chemical_state_id": chemical_state_id,
                "pose_id": f"{ligand_id}-{chemical_state_id}-pose-{rank}",
                "pose_rank_within_ligand": rank,
                "engine": "diffdock-nim-hosted",
                "engine_version": engine_version,
                "receptor_state_id": receptor_state_id,
                "seed": "",
                "metric_name": "diffdock_confidence",
                "metric_value": float(confidence),
                "metric_unit": "unitless",
                "metric_role": "pose-confidence",
                "rank_scope": "within-ligand",
                "input_sha256": str(request_record.get("input_sha256") or ""),
                "raw_output_sha256": raw_output_sha256,
                "output_path": str(destination),
                "evidence_status": "computational_prediction",
                "experimental_status": "not-tested",
                "notes": (
                    "DiffDock confidence ranks predicted poses within a ligand; "
                    "it is not an across-ligand affinity score."
                ),
            }
        )
    return rows


def parse_vina_pdbqt(path: Path) -> list[dict[str, Any]]:
    poses: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = VINA_RESULT_RE.match(line)
            if not match:
                continue
            rank = len(poses) + 1
            poses.append(
                {
                    "pose_id": f"pose-{rank}",
                    "pose_rank_within_ligand": rank,
                    "metric_name": "vina_score",
                    "metric_value": float(match.group("score")),
                    "metric_unit": "kcal/mol",
                    "metric_role": "protocol-specific-ranking-score",
                    "rank_scope": "within-ligand",
                    "rmsd_lb": (
                        float(match.group("rmsd_lb"))
                        if match.group("rmsd_lb") is not None
                        else None
                    ),
                    "rmsd_ub": (
                        float(match.group("rmsd_ub"))
                        if match.group("rmsd_ub") is not None
                        else None
                    ),
                }
            )
    return poses


def write_docking_candidate_header(path: Path, *, reset: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not reset:
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.DictWriter(handle, fieldnames=DOCKING_CANDIDATE_FIELDS).writeheader()


def append_vina_candidates(
    path: Path,
    command: Mapping[str, Any],
    *,
    run_id: str,
    engine_version: str,
    receptor_state_id: str,
    raw_output_sha256: str | None = None,
) -> None:
    rows = vina_candidate_rows(
        command,
        run_id=run_id,
        engine_version=engine_version,
        receptor_state_id=receptor_state_id,
        raw_output_sha256=raw_output_sha256,
    )
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=DOCKING_CANDIDATE_FIELDS)
        writer.writerows(rows)


def vina_candidate_rows(
    command: Mapping[str, Any],
    *,
    run_id: str,
    engine_version: str,
    receptor_state_id: str,
    raw_output_sha256: str | None = None,
) -> list[dict[str, Any]]:
    output_path = Path(str(command["output_path"]))
    poses = parse_vina_pdbqt(output_path)
    output_hash = raw_output_sha256 or _sha256_file(output_path)
    rows: list[dict[str, Any]] = []
    for pose in poses:
        rows.append(
            {
                "run_id": run_id,
                "ligand_id": command["ligand_id"],
                "chemical_state_id": command["chemical_state_id"],
                "pose_id": (
                    f"{command['ligand_id']}-{command['chemical_state_id']}-"
                    f"seed-{command['seed']}-"
                    f"{pose['pose_id']}"
                ),
                "pose_rank_within_ligand": pose["pose_rank_within_ligand"],
                "engine": "autodock-vina",
                "engine_version": engine_version,
                "receptor_state_id": receptor_state_id,
                "seed": command["seed"],
                "metric_name": pose["metric_name"],
                "metric_value": pose["metric_value"],
                "metric_unit": pose["metric_unit"],
                "metric_role": pose["metric_role"],
                "rank_scope": command.get("rank_scope") or pose["rank_scope"],
                "input_sha256": command.get("input_sha256") or "",
                "raw_output_sha256": output_hash,
                "output_path": str(output_path),
                "evidence_status": "computational_prediction",
                "experimental_status": "not-tested",
                "notes": (
                    "Vina score is a protocol-specific computational ranking score, "
                    "not an experimental binding free energy."
                ),
            }
        )
    return rows


def append_docking_rows(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    write_docking_candidate_header(path)
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=DOCKING_CANDIDATE_FIELDS)
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in DOCKING_CANDIDATE_FIELDS})


def _audit_docking_candidates_unchecked(
    path: Path,
    *,
    expected_run_id: str | None = None,
    execution_status: str | None = None,
    output_root: Path | None = None,
) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        return [f"Docking candidate table not found: {path}"]
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, strict=True)
        missing = [
            field for field in DOCKING_CANDIDATE_FIELDS if field not in (reader.fieldnames or [])
        ]
        if missing:
            return ["Docking candidate table missing columns: " + ", ".join(missing)]
        current_run_rows = 0
        row_count = 0
        for line, row in enumerate(reader, start=2):
            row_count += 1
            if expected_run_id and row.get("run_id") == expected_run_id:
                current_run_rows += 1
            required_text_fields = (
                "run_id",
                "ligand_id",
                "chemical_state_id",
                "pose_id",
                "engine",
                "engine_version",
                "receptor_state_id",
                "metric_name",
                "metric_unit",
                "metric_role",
                "rank_scope",
                "output_path",
            )
            for field in required_text_fields:
                if not str(row.get(field) or "").strip():
                    errors.append(f"Line {line}: required field {field} is empty")
            for field in ("run_id", "ligand_id", "chemical_state_id"):
                value = str(row.get(field) or "")
                if value and not validate_stable_id(value):
                    errors.append(f"Line {line}: field {field} is not a safe stable ID")
            rank_text = str(row.get("pose_rank_within_ligand") or "").strip()
            try:
                rank = int(rank_text)
            except ValueError:
                rank = 0
            if rank <= 0 or str(rank) != rank_text:
                errors.append(
                    f"Line {line}: pose_rank_within_ligand must be a positive integer"
                )
            metric_text = str(row.get("metric_value") or "").strip()
            try:
                metric_value = float(metric_text)
            except ValueError:
                metric_value = math.nan
            if not metric_text or not math.isfinite(metric_value):
                errors.append(f"Line {line}: metric_value must be numeric and finite")
            engine = str(row.get("engine") or "").casefold()
            role = str(row.get("metric_role") or "").casefold()
            scope = str(row.get("rank_scope") or "").casefold()
            experimental = str(row.get("experimental_status") or "").casefold()
            evidence = str(row.get("evidence_status") or "").casefold()
            notes = str(row.get("notes") or "").casefold()
            metric_name = str(row.get("metric_name") or "").casefold()
            metric_unit = str(row.get("metric_unit") or "").casefold()
            seed_text = str(row.get("seed") or "").strip()
            if engine not in {"autodock-vina", "diffdock-nim-hosted"}:
                errors.append(f"Line {line}: engine is unsupported: {engine or 'empty'}")
            if engine == "autodock-vina":
                try:
                    int(seed_text)
                    seed_valid = bool(seed_text)
                except ValueError:
                    seed_valid = False
                if not seed_valid:
                    errors.append(f"Line {line}: Vina seed must be an integer")
                if metric_name != "vina_score":
                    errors.append(f"Line {line}: Vina metric_name must be vina_score")
                if metric_unit != "kcal/mol":
                    errors.append(f"Line {line}: Vina metric_unit must be kcal/mol")
            if engine == "autodock-vina" and role != "protocol-specific-ranking-score":
                errors.append(
                    f"Line {line}: Vina score is not experimental free energy; "
                    "metric_role must be protocol-specific-ranking-score"
                )
            if engine == "diffdock-nim-hosted":
                if seed_text:
                    errors.append(f"Line {line}: DiffDock seed must be empty")
                if metric_name != "diffdock_confidence":
                    errors.append(
                        f"Line {line}: DiffDock metric_name must be diffdock_confidence"
                    )
                if metric_unit != "unitless":
                    errors.append(
                        f"Line {line}: DiffDock metric_unit must be unitless"
                    )
            if engine == "diffdock-nim-hosted" and (
                role != "pose-confidence" or scope != "within-ligand"
            ):
                errors.append(
                    f"Line {line}: DiffDock confidence is pose-confidence within-ligand "
                    "and cannot rank affinity across ligands"
                )
            if ("plip" in engine or "plip" in metric_name) and role != "geometry-annotation":
                errors.append(f"Line {line}: PLIP output is geometry annotation only")
            prediction_only = evidence in {
                "prediction_only",
                "computational_prediction",
                "planning_only",
            }
            success_words = (
                "validated",
                "active binder",
                "experimentally confirmed",
                "experimental success",
            )
            if prediction_only and any(word in experimental for word in success_words):
                errors.append(
                    f"Line {line}: prediction_only docking result cannot claim experimental success"
                )
            if prediction_only and _contains_positive_prediction_claim(notes):
                errors.append(
                    f"Line {line}: prediction-only notes contain a positive "
                    "biological or affinity claim"
                )
            if evidence != "computational_prediction":
                errors.append(
                    f"Line {line}: docking evidence_status must be computational_prediction"
                )
            if experimental != "not-tested":
                errors.append(
                    f"Line {line}: docking experimental_status must be not-tested"
                )
            if engine == "autodock-vina" and scope not in {
                "within-ligand",
                "within-ligand-and-seed",
            }:
                errors.append(
                    f"Line {line}: Vina rank_scope must be within-ligand or within-ligand-and-seed"
                )
            if not SHA256_RE.fullmatch(str(row.get("input_sha256") or "")):
                errors.append(f"Line {line}: input_sha256 is invalid")
            if not SHA256_RE.fullmatch(str(row.get("raw_output_sha256") or "")):
                errors.append(f"Line {line}: raw_output_sha256 is invalid")
            output_path_text = str(row.get("output_path") or "").strip()
            if not output_path_text or not Path(output_path_text).is_file():
                errors.append(f"Line {line}: output_path does not exist")
            if output_root is not None:
                output_path = Path(str(row.get("output_path") or "")).resolve()
                if not output_path.is_relative_to(output_root.resolve()):
                    errors.append(f"Line {line}: output_path escapes package root")
                elif (
                    execution_status == "completed-computational-only"
                    and not output_path.is_file()
                ):
                    errors.append(f"Line {line}: completed pose output is missing")
        if execution_status == "completed-computational-only":
            if row_count == 0:
                errors.append("Completed docking run contains zero poses")
            if expected_run_id and current_run_rows == 0:
                errors.append("Completed docking run has no candidates from the current run")
    return sorted(set(errors))


def audit_docking_candidates(
    path: Path,
    *,
    expected_run_id: str | None = None,
    execution_status: str | None = None,
    output_root: Path | None = None,
) -> list[str]:
    try:
        return _audit_docking_candidates_unchecked(
            path,
            expected_run_id=expected_run_id,
            execution_status=execution_status,
            output_root=output_root,
        )
    except (OSError, UnicodeError, csv.Error):
        return [
            "Docking candidate table is unreadable, undecodable, or malformed"
        ]


PROVENANCE_FIELDS = (
    "run_id",
    "ligand_id",
    "chemical_state_id",
    "pose_id",
    "pose_rank_within_ligand",
    "engine",
    "engine_version",
    "receptor_state_id",
    "seed",
    "metric_name",
    "metric_value",
    "metric_unit",
    "metric_role",
    "rank_scope",
    "input_sha256",
    "raw_output_sha256",
    "output_path",
    "evidence_status",
    "experimental_status",
    "notes",
)


def _audit_completed_docking_provenance_unchecked(
    manifest: Mapping[str, Any],
    candidates_path: Path,
    package_root: Path,
) -> list[str]:
    execution = manifest.get("execution") or {}
    if execution.get("status") != "completed-computational-only":
        return []
    errors: list[str] = []
    root = package_root.resolve()
    docking = manifest.get("docking") or {}
    engine = str(docking.get("engine") or "")
    run_id = str(manifest.get("run_id") or "")
    parameters = manifest.get("parameters") or {}
    receptor_state_id = stable_receptor_state_id(parameters.get("target") or {})
    tools = manifest.get("tools") or {}
    expected_rows: list[dict[str, Any]] = []

    if engine == "autodock-vina":
        engine_version = str((tools.get("autodock_vina") or {}).get("version") or "")
        commands = execution.get("argv_commands") or []
        results = execution.get("results") or []
        if len(results) != len(commands):
            errors.append(
                "Completed provenance Vina result count differs from reviewed argv commands"
            )
        seen_result_indices: set[int] = set()
        for position, result in enumerate(results, start=1):
            if not isinstance(result, Mapping):
                errors.append(
                    f"Completed provenance Vina result {position} is not a mapping"
                )
                continue
            result_index = result.get("index")
            if (
                not isinstance(result_index, int)
                or isinstance(result_index, bool)
                or result_index != position
                or result_index in seen_result_indices
            ):
                errors.append(
                    f"Completed provenance Vina result {position} index is invalid, "
                    "duplicated, or out of order"
                )
            else:
                seen_result_indices.add(result_index)
            returncode = result.get("returncode")
            if (
                not isinstance(returncode, int)
                or isinstance(returncode, bool)
                or returncode != 0
            ):
                errors.append(
                    f"Completed provenance Vina result {position} returncode "
                    "must be integer zero"
                )
            if position <= len(commands):
                command = commands[position - 1]
                expected_argv = (
                    command.get("argv") if isinstance(command, Mapping) else None
                )
                if result.get("argv") != expected_argv:
                    errors.append(
                        f"Completed provenance Vina result {position} argv differs "
                        "from reviewed argv"
                    )
        for index, command in enumerate(commands, start=1):
            if not isinstance(command, Mapping):
                errors.append(
                    f"Completed provenance Vina command {index} is not a mapping"
                )
                continue
            output_path = Path(str(command.get("output_path") or "")).resolve()
            if not output_path.is_relative_to(root):
                errors.append(
                    f"Completed provenance Vina output {index} escapes package root"
                )
                continue
            if not output_path.is_file():
                errors.append(
                    f"Completed provenance Vina output {index} does not exist"
                )
                continue
            result = results[index - 1] if index <= len(results) else {}
            if not isinstance(result, Mapping):
                result = {}
            stored_output_path = Path(
                str(result.get("output_path") or "")
            ).resolve()
            if stored_output_path != output_path:
                errors.append(
                    f"Completed provenance Vina output {index} result path mismatch"
                )
            output_sha256 = str(result.get("output_sha256") or "")
            if (
                not SHA256_RE.fullmatch(output_sha256)
                or _sha256_file(output_path) != output_sha256
            ):
                errors.append(
                    f"Completed provenance Vina output {index} hash mismatch"
                )
            output_bytes = result.get("output_bytes")
            if (
                not isinstance(output_bytes, int)
                or isinstance(output_bytes, bool)
                or output_bytes != output_path.stat().st_size
            ):
                errors.append(
                    f"Completed provenance Vina output {index} byte count mismatch"
                )
            try:
                rows = vina_candidate_rows(
                    command,
                    run_id=run_id,
                    engine_version=engine_version,
                    receptor_state_id=receptor_state_id,
                    raw_output_sha256=output_sha256,
                )
                pose_count = result.get("pose_count")
                if (
                    not isinstance(pose_count, int)
                    or isinstance(pose_count, bool)
                    or pose_count != len(rows)
                ):
                    errors.append(
                        f"Completed provenance Vina output {index} pose count mismatch"
                    )
                expected_rows.extend(rows)
            except (OSError, UnicodeError, ValueError):
                errors.append(
                    f"Completed provenance Vina output {index} cannot be read or parsed"
                )
    elif engine == "diffdock-nim-hosted":
        request = {
            "target": parameters.get("target") or {},
            "ligand": parameters.get("ligand"),
            "ligand_library": manifest.get("ligand_library"),
            "docking": docking,
            "external_service": manifest.get("external_service") or {},
            "tools": tools,
        }
        try:
            request_records = build_hosted_requests(request)
        except (OSError, ValueError, UnicodeError, csv.Error):
            errors.append(
                "Completed provenance hosted inputs cannot be read or rebuilt"
            )
            request_records = []
        record_map = {
            (
                str(record.get("ligand_id") or ""),
                str(record.get("chemical_state_id") or ""),
            ): record
            for record in request_records
        }
        results = execution.get("results") or []
        if len(results) != len(request_records):
            errors.append(
                "Completed provenance hosted result count differs from ligand inputs"
            )
        seen: set[tuple[str, str]] = set()
        engine_version = str((tools.get("diffdock_hosted") or {}).get("service_version") or "")
        for index, result in enumerate(results, start=1):
            if not isinstance(result, Mapping):
                errors.append(
                    f"Completed provenance hosted result {index} is not a mapping"
                )
                continue
            if result.get("adapter") != "diffdock-nim-hosted":
                errors.append(
                    f"Completed provenance hosted result {index} adapter mismatch"
                )
            if str(result.get("expected_service_version") or "") != engine_version:
                errors.append(
                    f"Completed provenance hosted result {index} expected service "
                    "version mismatch"
                )
            if not _valid_required_text(result.get("observed_service_version")):
                errors.append(
                    f"Completed provenance hosted result {index} observed service "
                    "version is missing"
                )
            observed_headers = result.get("observed_headers")
            if not isinstance(observed_headers, Mapping) or any(
                str(key).casefold() not in SAFE_HOSTED_RESPONSE_HEADERS
                for key in observed_headers
            ):
                errors.append(
                    f"Completed provenance hosted result {index} contains unsafe "
                    "or invalid response headers"
                )
            identity = (
                str(result.get("ligand_id") or ""),
                str(result.get("chemical_state_id") or ""),
            )
            request_record = record_map.get(identity)
            if request_record is None or identity in seen:
                errors.append(
                    f"Completed provenance hosted result {index} has unmatched IDs"
                )
                continue
            seen.add(identity)
            response_path = Path(str(result.get("response_path") or "")).resolve()
            expected_response_path = safe_output_path(
                root,
                "docking_outputs",
                canonical_stable_id(identity[0]),
                canonical_stable_id(identity[1]),
                "hosted",
                "response.json",
            )
            if response_path != expected_response_path:
                errors.append(
                    f"Completed provenance hosted response {index} path mismatch"
                )
                continue
            if not response_path.is_relative_to(root):
                errors.append(
                    f"Completed provenance hosted response {index} escapes package root"
                )
                continue
            if not response_path.is_file():
                errors.append(
                    f"Completed provenance hosted response {index} does not exist"
                )
                continue
            response_hash = str(result.get("response_sha256") or "")
            if (
                not SHA256_RE.fullmatch(response_hash)
                or _sha256_file(response_path) != response_hash
            ):
                errors.append(
                    f"Completed provenance hosted response {index} hash mismatch"
                )
                continue
            response_bytes = result.get("response_bytes")
            if (
                not isinstance(response_bytes, int)
                or isinstance(response_bytes, bool)
                or response_bytes != response_path.stat().st_size
            ):
                errors.append(
                    f"Completed provenance hosted response {index} byte count mismatch"
                )
            if str(result.get("input_sha256") or "") != str(
                request_record.get("input_sha256") or ""
            ):
                errors.append(
                    f"Completed provenance hosted result {index} input hash mismatch"
                )
            try:
                response = json.loads(response_path.read_bytes().decode("utf-8"))
                if not isinstance(response, Mapping):
                    raise ValueError("response is not a mapping")
                rows = materialize_diffdock_response(
                    response,
                    request_record,
                    root,
                    run_id=run_id,
                    engine_version=engine_version,
                    receptor_state_id=receptor_state_id,
                    write_outputs=False,
                )
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                errors.append(
                    f"Completed provenance hosted response {index} cannot be "
                    "decoded or rebuilt"
                )
                continue
            if int(result.get("pose_count") or -1) != len(rows):
                errors.append(
                    f"Completed provenance hosted result {index} pose count mismatch"
                )
            pose_outputs = result.get("pose_outputs")
            if not isinstance(pose_outputs, list) or len(pose_outputs) != len(rows):
                errors.append(
                    f"Completed provenance hosted result {index} pose anchors mismatch"
                )
                pose_outputs = []
            positions = response.get("ligand_positions") or []
            for pose_index, (row, sdf_text) in enumerate(
                zip(rows, positions), start=1
            ):
                pose_path = Path(str(row.get("output_path") or "")).resolve()
                if not pose_path.is_relative_to(root) or not pose_path.is_file():
                    errors.append(
                        f"Completed provenance hosted pose {index}:{pose_index} "
                        "is missing or outside package root"
                    )
                elif pose_path.read_bytes().decode("utf-8") != str(sdf_text):
                    errors.append(
                        f"Completed provenance hosted pose {index}:{pose_index} "
                        "content mismatch"
                    )
                anchor = (
                    pose_outputs[pose_index - 1]
                    if pose_index <= len(pose_outputs)
                    else {}
                )
                if not isinstance(anchor, Mapping):
                    anchor = {}
                anchor_path = Path(str(anchor.get("path") or "")).resolve()
                anchor_sha256 = str(anchor.get("sha256") or "")
                anchor_bytes = anchor.get("bytes")
                if anchor_path != pose_path:
                    errors.append(
                        f"Completed provenance hosted pose {index}:{pose_index} "
                        "anchor path mismatch"
                    )
                if (
                    not SHA256_RE.fullmatch(anchor_sha256)
                    or not pose_path.is_file()
                    or _sha256_file(pose_path) != anchor_sha256
                    or str(row.get("raw_output_sha256") or "") != anchor_sha256
                ):
                    errors.append(
                        f"Completed provenance hosted pose {index}:{pose_index} "
                        "anchor hash mismatch"
                    )
                if (
                    not isinstance(anchor_bytes, int)
                    or isinstance(anchor_bytes, bool)
                    or not pose_path.is_file()
                    or pose_path.stat().st_size != anchor_bytes
                ):
                    errors.append(
                        f"Completed provenance hosted pose {index}:{pose_index} "
                        "anchor byte count mismatch"
                    )
                if str(anchor.get("pose_id") or "") != str(row.get("pose_id") or ""):
                    errors.append(
                        f"Completed provenance hosted pose {index}:{pose_index} "
                        "anchor pose ID mismatch"
                    )
            expected_rows.extend(rows)
    else:
        errors.append(f"Completed provenance engine is unsupported: {engine}")

    if not candidates_path.is_file():
        errors.append("Completed provenance candidate table does not exist")
        return sorted(set(errors))
    with candidates_path.open("r", encoding="utf-8-sig", newline="") as handle:
        actual_rows = list(csv.DictReader(handle, strict=True))
    if len(actual_rows) != len(expected_rows):
        errors.append(
            "Completed provenance candidate row count differs from raw outputs"
        )
    for line, (actual, expected) in enumerate(
        zip(actual_rows, expected_rows), start=2
    ):
        for field in PROVENANCE_FIELDS:
            if str(actual.get(field) or "") != str(expected.get(field) or ""):
                errors.append(
                    f"Completed provenance line {line} field {field} mismatch"
                )
    return sorted(set(errors))


def audit_completed_docking_provenance(
    manifest: Mapping[str, Any],
    candidates_path: Path,
    package_root: Path,
) -> list[str]:
    try:
        return _audit_completed_docking_provenance_unchecked(
            manifest,
            candidates_path,
            package_root,
        )
    except (
        OSError,
        UnicodeError,
        csv.Error,
        json.JSONDecodeError,
        ValueError,
    ):
        return [
            "Completed docking provenance contains unreadable, undecodable, "
            "malformed, or missing raw evidence"
        ]


def docking_report_text(
    request: Mapping[str, Any],
    preflight: Mapping[str, Any],
    gaps: Sequence[str],
    *,
    execution_status: str = "not-started",
    evidence_status: str = "planning_only",
    audit_passed: bool | None = None,
) -> str:
    docking = _as_mapping(request.get("docking"))
    engine = docking.get("engine") or "unresolved"
    use_meeko = bool(
        _as_mapping(docking.get("preparation")).get("use_meeko")
    )
    preparation_note = (
        "Meeko mode means receptor and ligand were preprocessed externally; "
        "this workflow requires the declared prepared PDBQT files and does not "
        "install or run Meeko automatically."
        if use_meeko
        else "No automatic receptor or ligand preparation is performed."
    )
    issues = "\n".join(f"- `{item}`" for item in gaps) or "- None"
    return f"""# Docking report

- Engine: `{engine}`
- Execution status: `{execution_status}`
- Evidence status: `{evidence_status}`
- Ready for local execution: `{str(bool(preflight.get('ready_for_local_execution'))).lower()}`
- Terminal audit passed: `{str(audit_passed).lower() if audit_passed is not None else 'not-run'}`

## Blocking inputs and environment findings

{issues}

## Interpretation boundary

{preparation_note}

Docking poses and scores are computational predictions. AutoDock Vina scores are
protocol-specific ranking scores, not experimental binding free energies.
DiffDock confidence ranks poses within a ligand and is not an affinity score
across ligands. PLIP annotations describe predicted geometry. No row in this
package establishes experimental binding, activity, selectivity, or efficacy.
"""


def execute_vina_commands(
    commands: Sequence[Mapping[str, Any]], *, cwd: Path
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, command in enumerate(commands, start=1):
        argv = command.get("argv")
        if (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(item, str) and item for item in argv)
        ):
            raise ValueError("Execution requires a non-empty reviewed argv list")
        output = Path(str(command.get("output_path") or ""))
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            argv,
            shell=False,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        result = {
            "index": index,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "argv": argv,
            "output_path": str(output),
        }
        if completed.returncode == 0 and output.is_file():
            result.update(
                {
                    "output_sha256": _sha256_file(output),
                    "output_bytes": output.stat().st_size,
                    "pose_count": len(parse_vina_pdbqt(output)),
                }
            )
        results.append(result)
        if completed.returncode != 0:
            break
    return results


class HostedAPIResponse(dict[str, Any]):
    """Parsed hosted response plus exact transport bytes and safe version headers."""

    def __init__(
        self,
        payload: Mapping[str, Any],
        *,
        raw_bytes: bytes,
        observed_headers: Mapping[str, str],
    ) -> None:
        super().__init__(payload)
        self.raw_bytes = raw_bytes
        self.observed_headers = dict(observed_headers)


SAFE_HOSTED_RESPONSE_HEADERS = {
    "x-nim-version",
    "x-nvidia-service-version",
    "x-service-version",
}


class NoRedirectHTTPHandler(urllib.request.HTTPRedirectHandler):
    """Fail closed on every HTTP redirect; never construct a second request."""

    def redirect_request(
        self,
        req: Any,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _open_no_redirect(request: Any, *, timeout: int) -> Any:
    opener = urllib.request.build_opener(NoRedirectHTTPHandler())
    return opener.open(request, timeout=timeout)


def execute_hosted_diffdock(
    request_payload: Mapping[str, Any],
    *,
    external_service: Mapping[str, Any],
    environ: Mapping[str, str] | None = None,
    opener: Any | None = None,
) -> HostedAPIResponse:
    """Call the one approved hosted endpoint after strict, caller-side audit."""
    reject_plaintext_credentials(request_payload)
    validate_hosted_payload(request_payload)
    environment = os.environ if environ is None else environ
    if external_service.get("endpoint") != DIFFDOCK_HOSTED_ENDPOINT:
        raise ValueError("Hosted DiffDock endpoint is not the approved endpoint")
    if external_service.get("authorized") is not True:
        raise ValueError("Hosted DiffDock use is not authorized")
    if external_service.get("credential_rotation_acknowledged") is not True:
        raise ValueError("Credential rotation acknowledgement is required")
    if external_service.get("data_classification") not in {"public", "non-sensitive"}:
        raise ValueError("Hosted input data must be public or non-sensitive")
    if external_service.get("auth_env") != "NVIDIA_API_KEY":
        raise ValueError("Hosted DiffDock auth_env must be NVIDIA_API_KEY")
    token = environment.get("NVIDIA_API_KEY")
    if not token:
        raise ValueError("Environment variable NVIDIA_API_KEY is not set")
    request = urllib.request.Request(
        DIFFDOCK_HOSTED_ENDPOINT,
        data=json.dumps(request_payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    if opener is None:
        open_call = _open_no_redirect
    else:
        open_call = opener.open if hasattr(opener, "open") else opener
    with open_call(request, timeout=120) as response:
        raw_bytes = response.read()
        parsed = json.loads(raw_bytes.decode("utf-8"))
        if not isinstance(parsed, Mapping):
            raise ValueError("Hosted DiffDock response must be a JSON mapping")
        reject_plaintext_credentials(parsed)
        response_headers = getattr(response, "headers", {})
        safe_headers = {
            str(key).casefold(): str(value)
            for key, value in response_headers.items()
            if str(key).casefold() in SAFE_HOSTED_RESPONSE_HEADERS
        }
        reject_plaintext_credentials(safe_headers)
        return HostedAPIResponse(
            parsed,
            raw_bytes=raw_bytes,
            observed_headers=safe_headers,
        )


def shell_quote_for_log(argv: Sequence[str]) -> str:
    """Human-readable only; never used for execution."""
    return shlex.join(argv)
