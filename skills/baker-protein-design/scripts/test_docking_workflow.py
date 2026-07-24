from __future__ import annotations

import csv
import ast
import copy
import io
import math
import os
import json
import socket
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

import docking_workflow
import baker_design
import environment_check
from baker_design import (
    audit_manifest,
    execute_manifest,
    prepare_package,
    probe_environment,
    write_yaml,
)
from docking_workflow import (
    DIFFDOCK_HOSTED_ENDPOINT,
    DOCKING_CANDIDATE_FIELDS,
    argv_sha256,
    audit_docking_candidates,
    build_hosted_requests,
    build_vina_argv_commands,
    docking_gaps,
    execute_hosted_diffdock,
    materialize_diffdock_response,
    parse_vina_pdbqt,
    required_tools,
    validate_stable_id,
)


class DockingWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._real_open_no_redirect = docking_workflow._open_no_redirect
        self._network_guard_patcher = patch(
            "docking_workflow._open_no_redirect",
            side_effect=AssertionError(
                "NetworkDisabled: explicit hosted opener mock required"
            ),
        )
        self.network_guard = self._network_guard_patcher.start()
        self.addCleanup(self._network_guard_patcher.stop)
        self._socket_create_guard_patcher = patch(
            "socket.create_connection",
            side_effect=AssertionError(
                "NetworkDisabled: socket connections are disabled in tests"
            ),
        )
        self.socket_create_guard = self._socket_create_guard_patcher.start()
        self.addCleanup(self._socket_create_guard_patcher.stop)
        self._socket_connect_guard_patcher = patch(
            "socket.socket.connect",
            side_effect=AssertionError(
                "NetworkDisabled: socket connections are disabled in tests"
            ),
        )
        self.socket_connect_guard = self._socket_connect_guard_patcher.start()
        self.addCleanup(self._socket_connect_guard_patcher.stop)

    def make_files(self, root: Path) -> tuple[Path, Path]:
        receptor = root / "receptor.pdbqt"
        ligand = root / "ligand.pdbqt"
        receptor.write_text("ATOM\n", encoding="utf-8")
        ligand.write_text("MODEL 1\nENDMDL\n", encoding="utf-8")
        return receptor, ligand

    def vina_tools(self, executable: str = "C:/missing/vina.exe") -> dict:
        common = {
            "repository": "https://example.org/tool",
            "executable": executable,
            "version": "1.2.7",
            "license": "Apache-2.0",
        }
        return {
            "autodock_vina": dict(common),
            "meeko": dict(common),
            "plip": dict(common),
            "pymol": dict(common),
        }

    def base_vina_request(self, receptor: Path, ligand: Path) -> dict:
        atom_mapping = receptor.parent / "atom-mapping.csv"
        atom_mapping.write_text(
            "reference_atom,predicted_atom\nC1,C1\n", encoding="utf-8"
        )
        return {
            "route": "molecular-docking-screen",
            "design_goal": "redocking protocol validation",
            "target": {
                "type": "folded-protein",
                "structure_file": str(receptor),
                "chains": ["A"],
                "biological_state": "biological assembly 1",
                "assembly": "biological assembly 1",
                "mutation_state": "wild-type",
                "receptor_state_id": "rec-state-1",
                "receptor_preparation": {
                    "protonation": "pH 7.4",
                    "missing_residues": "retain missing positions and document",
                    "alternate_locations": "highest occupancy",
                    "hetatm_policy": "retain named cofactor; remove crystallization additives",
                },
            },
            "ligand": {
                "file": str(ligand),
                "ligand_id": "lig-001",
                "chemical_state": {
                    "strategy": "enumerated protonation and tautomer state",
                    "chemical_state_id": "lig-001-state-01",
                },
            },
            "docking": {
                "objective": "redocking",
                "engine": "autodock-vina",
                "binding_site": {
                    "source": "co-crystal ligand",
                    "center": [0, 0.0, -1.5],
                    "size": [20, 20, 22],
                },
                "seeds": [0, 11],
                "num_poses": 3,
                "exhaustiveness": 8,
                "validation": {
                    "reference_pose": str(ligand),
                    "reference_pose_sha256": docking_workflow._sha256_file(
                        ligand
                    ),
                    "atom_mapping": {
                        "file": str(atom_mapping),
                        "sha256": docking_workflow._sha256_file(atom_mapping),
                    },
                    "symmetry_handling": "minimum symmetry-corrected RMSD",
                    "receptor_alignment": "align receptor backbone before RMSD",
                    "heavy_atom_rule": "exclude hydrogens",
                    "rmsd_tool": "test-rmsd-tool",
                    "rmsd_tool_version": "1.0",
                    "pose_selection": "evaluate top-N ranked poses",
                    "top_n": 5,
                },
            },
            "tools": self.vina_tools(),
        }

    def test_missing_receptor_state_grid_seed_and_screen_controls_are_blocked(self) -> None:
        request = {
            "target": {
                "structure_file": "r.pdbqt",
                "chains": ["A"],
                "receptor_preparation": {
                    "protonation": "set",
                    "missing_residues": "document",
                    "alternate_locations": "highest occupancy",
                    "hetatm_policy": "retain cofactors",
                },
            },
            "ligand": {
                "file": "l.pdbqt",
                "ligand_id": "l1",
                "chemical_state": {"strategy": "enumerate", "chemical_state_id": "l1-s1"},
            },
            "docking": {
                "objective": "target-focused-screen",
                "engine": "autodock-vina",
                "num_poses": 5,
                "exhaustiveness": 8,
            },
        }
        joined = "\n".join(docking_gaps(request))
        self.assertIn("target.biological_state", joined)
        self.assertIn("docking.binding_site", joined)
        self.assertIn("docking.seed-or-seeds", joined)
        self.assertIn("docking.validation.controls", joined)

    def test_explicit_zero_grid_coordinates_are_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            receptor, ligand = self.make_files(Path(temp))
            request = self.base_vina_request(receptor, ligand)
            request["target"].pop("receptor_state_id")
            self.assertEqual(docking_gaps(request), [])

    def test_remove_all_hetatm_policy_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            receptor, ligand = self.make_files(Path(temp))
            request = self.base_vina_request(receptor, ligand)
            request["target"]["receptor_preparation"]["hetatm_policy"] = "remove_all"
            self.assertIn(
                "target.receptor_preparation.hetatm_policy-remove_all-forbidden",
                docking_gaps(request),
            )

    def test_redocking_strict_run_requires_complete_pose_recovery_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            receptor, ligand = self.make_files(Path(temp))
            request = self.base_vina_request(receptor, ligand)
            required = (
                "reference_pose",
                "reference_pose_sha256",
                "atom_mapping",
                "symmetry_handling",
                "receptor_alignment",
                "heavy_atom_rule",
                "rmsd_tool",
                "rmsd_tool_version",
                "pose_selection",
                "top_n",
            )
            for field in required:
                with self.subTest(field=field):
                    candidate = copy.deepcopy(request)
                    candidate["docking"]["validation"].pop(field)
                    gaps = docking_gaps(candidate, strict_run=True)
                    self.assertIn(f"docking.validation.{field}", gaps)
            planning = copy.deepcopy(request)
            planning["docking"].pop("validation")
            planning_gaps = docking_gaps(planning, strict_run=False)
            self.assertFalse(
                any(gap.startswith("docking.validation.") for gap in planning_gaps)
            )

    def test_strict_redocking_requires_hashed_atom_mapping_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            fake_vina = root / "vina.exe"
            fake_vina.write_text("not executed", encoding="utf-8")
            request = self.base_vina_request(receptor, ligand)
            request["tools"] = self.vina_tools(str(fake_vina))
            self.assertTrue(
                docking_workflow.preflight_docking(
                    request, strict_run=True
                )["ready_for_local_execution"]
            )
            manifest = prepare_package(request, "run", root / "valid")
            mapping_record = manifest["input_hashes"]["docking_atom_mapping"]
            self.assertEqual(
                mapping_record["sha256"],
                request["docking"]["validation"]["atom_mapping"]["sha256"],
            )

            prose = copy.deepcopy(request)
            prose["docking"]["validation"]["atom_mapping"] = (
                "explicit heavy-atom name mapping"
            )
            self.assertNotIn(
                "docking.validation.atom_mapping",
                docking_gaps(prose, strict_run=False),
            )
            self.assertIn(
                "docking.validation.atom_mapping",
                docking_gaps(prose, strict_run=True),
            )
            self.assertFalse(
                docking_workflow.preflight_docking(
                    prose, strict_run=True
                )["ready_for_local_execution"]
            )

            mapping_file = Path(
                request["docking"]["validation"]["atom_mapping"]["file"]
            )
            cases = (
                (
                    {"file": str(mapping_file)},
                    "docking.validation.atom_mapping.sha256",
                ),
                (
                    {"sha256": docking_workflow._sha256_file(mapping_file)},
                    "docking.validation.atom_mapping.file",
                ),
                (
                    {
                        "path": str(mapping_file),
                        "sha256": docking_workflow._sha256_file(mapping_file),
                    },
                    "docking.validation.atom_mapping.file",
                ),
                (
                    {"file": str(mapping_file), "sha256": "b" * 64},
                    "docking.validation.atom_mapping.sha256-mismatch",
                ),
                (
                    {
                        "file": str(root / "missing-mapping.csv"),
                        "sha256": "a" * 64,
                    },
                    "docking.validation.atom_mapping.file-missing",
                ),
            )
            for mapping, expected in cases:
                with self.subTest(expected=expected):
                    candidate = copy.deepcopy(request)
                    candidate["docking"]["validation"][
                        "atom_mapping"
                    ] = mapping
                    self.assertIn(
                        expected,
                        docking_gaps(candidate, strict_run=True),
                    )

    def test_redocking_reference_pose_requires_matching_declared_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            fake_vina = root / "vina.exe"
            fake_vina.write_text("not executed", encoding="utf-8")
            request = self.base_vina_request(receptor, ligand)
            request["tools"] = self.vina_tools(str(fake_vina))
            self.assertTrue(
                docking_workflow.preflight_docking(
                    request, strict_run=True
                )["ready_for_local_execution"]
            )
            manifest = prepare_package(request, "run", root / "valid")
            self.assertEqual(
                manifest["input_hashes"]["docking_reference_pose"]["sha256"],
                request["docking"]["validation"][
                    "reference_pose_sha256"
                ],
            )

            missing = copy.deepcopy(request)
            missing["docking"]["validation"].pop("reference_pose_sha256")
            self.assertIn(
                "docking.validation.reference_pose_sha256",
                docking_gaps(missing, strict_run=True),
            )
            self.assertIn(
                "docking.validation.reference_pose_sha256",
                docking_gaps(missing, strict_run=False),
            )
            planning = prepare_package(
                missing, "prepare", root / "missing-hash-planning"
            )
            self.assertIn(
                "docking.validation.reference_pose_sha256",
                planning["execution"]["command_problems"],
            )

            invalid = copy.deepcopy(request)
            invalid["docking"]["validation"][
                "reference_pose_sha256"
            ] = "not-a-sha256"
            self.assertIn(
                "docking.validation.reference_pose_sha256",
                docking_gaps(invalid, strict_run=True),
            )

            mismatch = copy.deepcopy(request)
            mismatch["docking"]["validation"][
                "reference_pose_sha256"
            ] = "b" * 64
            self.assertIn(
                "docking.validation.reference_pose_sha256-mismatch",
                docking_gaps(mismatch, strict_run=True),
            )
            self.assertFalse(
                docking_workflow.preflight_docking(
                    mismatch, strict_run=True
                )["ready_for_local_execution"]
            )
            missing_file = copy.deepcopy(request)
            missing_file["docking"]["validation"]["reference_pose"] = str(
                root / "missing-reference.pdbqt"
            )
            missing_file["docking"]["validation"][
                "reference_pose_sha256"
            ] = "a" * 64
            self.assertIn(
                "docking.validation.reference_pose-missing",
                docking_gaps(missing_file, strict_run=False),
            )

    def test_declared_source_structure_sha256_is_validated_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            source = root / "source-structure.cif"
            source.write_text("data_source\n", encoding="utf-8")
            request = self.base_vina_request(receptor, ligand)
            request["target"]["source_structure_file"] = str(source)
            self.assertFalse(
                any(
                    gap.startswith("target.source_structure_sha256")
                    for gap in docking_gaps(request, strict_run=True)
                )
            )
            request["target"]["source_structure_sha256"] = (
                docking_workflow._sha256_file(source)
            )
            self.assertFalse(
                any(
                    gap.startswith("target.source_structure_sha256")
                    for gap in docking_gaps(request, strict_run=True)
                )
            )
            request["target"]["source_structure_sha256"] = "invalid"
            self.assertIn(
                "target.source_structure_sha256",
                docking_gaps(request, strict_run=True),
            )
            request["target"]["source_structure_sha256"] = "b" * 64
            self.assertIn(
                "target.source_structure_sha256-mismatch",
                docking_gaps(request, strict_run=True),
            )
            request["target"].pop("source_structure_sha256")
            request["target"]["source_structure_file"] = str(
                root / "missing-source.cif"
            )
            self.assertIn(
                "target.source_structure_file-missing",
                docking_gaps(request, strict_run=True),
            )

    def test_strict_preflight_rejects_placeholders_and_missing_target_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            receptor, ligand = self.make_files(Path(temp))
            request = self.base_vina_request(receptor, ligand)
            placeholder_fields = {
                ("target", "biological_state"): "  unresolved  ",
                ("target", "assembly"): "TODO",
                ("target", "mutation_state"): "UNKNOWN",
            }
            for (section, field), value in placeholder_fields.items():
                request[section][field] = value
            preparation = request["target"]["receptor_preparation"]
            preparation["protonation"] = "REPLACE_WITH_PROTONATION"
            preparation["missing_residues"] = "TBD"
            preparation["alternate_locations"] = "changeme"
            preparation["hetatm_policy"] = "unknown"
            request["ligand"]["chemical_state"]["strategy"] = " TODO "
            request["ligand"]["chemical_state"]["chemical_state_id"] = "UNKNOWN"
            request["docking"]["binding_site"]["source"] = "unresolved"
            request["tools"]["autodock_vina"].update(
                {
                    "repository": "TODO",
                    "executable": "REPLACE_WITH_VINA",
                    "version": " unresolved ",
                    "license": "TBD",
                }
            )
            gaps = docking_gaps(request, strict_run=True)
            expected = (
                "target.biological_state",
                "target.assembly",
                "target.mutation_state",
                "target.receptor_preparation.protonation",
                "target.receptor_preparation.missing_residues",
                "target.receptor_preparation.alternate_locations",
                "target.receptor_preparation.hetatm_policy",
                "ligand.chemical_state.strategy",
                "ligand.chemical_state.chemical_state_id",
                "docking.binding_site.source",
            )
            for gap in expected:
                with self.subTest(gap=gap):
                    self.assertIn(gap, gaps)
            preflight = probe_environment(request, strict_run=True)
            joined = "\n".join(preflight["issues"])
            for field in ("repository", "executable", "version", "license"):
                self.assertIn(f"tools.autodock_vina.{field}", joined)
            package = prepare_package(request, "prepare", Path(temp) / "planning")
            self.assertTrue(audit_manifest(package)["passed"])
            self.assertTrue(audit_manifest(package)["warnings"])

    def test_embedded_placeholder_markers_are_semantic_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            cases = (
                (
                    ("target", "biological_state"),
                    "planning_only: REPLACE_WITH_BIOLOGICAL_STATE",
                    "target.biological_state",
                ),
                (
                    ("target", "receptor_preparation", "protonation"),
                    "pH value TODO",
                    "target.receptor_preparation.protonation",
                ),
                (
                    ("target", "receptor_preparation", "missing_residues"),
                    "status is unresolved",
                    "target.receptor_preparation.missing_residues",
                ),
                (
                    ("target", "receptor_preparation", "alternate_locations"),
                    "selection TBD",
                    "target.receptor_preparation.alternate_locations",
                ),
                (
                    ("target", "assembly"),
                    "assembly value TODO",
                    "target.assembly",
                ),
                (
                    ("target", "mutation_state"),
                    "planning_only mutation state",
                    "target.mutation_state",
                ),
                (
                    ("ligand", "chemical_state", "strategy"),
                    "enumeration CHANGEME",
                    "ligand.chemical_state.strategy",
                ),
                (
                    ("docking", "binding_site", "source"),
                    "binding pocket unknown",
                    "docking.binding_site.source",
                ),
                (
                    ("docking", "validation", "rmsd_tool_version"),
                    "version TBD",
                    "docking.validation.rmsd_tool_version",
                ),
            )
            for path, value, expected in cases:
                with self.subTest(path=path):
                    request = self.base_vina_request(receptor, ligand)
                    cursor = request
                    for key in path[:-1]:
                        cursor = cursor[key]
                    cursor[path[-1]] = value
                    self.assertIn(expected, docking_gaps(request))
                    self.assertIn(
                        expected, docking_gaps(request, strict_run=True)
                    )

            request = self.base_vina_request(receptor, ligand)
            request["tools"]["autodock_vina"]["version"] = (
                "planning_only: REPLACE_WITH_VERSION"
            )
            preflight = docking_workflow.preflight_docking(
                request, strict_run=True
            )
            self.assertIn(
                "tools.autodock_vina.version", preflight["issues"]
            )
            package = prepare_package(request, "prepare", root / "planning")
            audit = audit_manifest(package)
            self.assertTrue(audit["passed"])
            self.assertIn(
                "tools.autodock_vina.version",
                "\n".join(audit["warnings"]),
            )

    def test_strict_run_requires_assembly_and_mutation_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            receptor, ligand = self.make_files(Path(temp))
            request = self.base_vina_request(receptor, ligand)
            for field in ("assembly", "mutation_state"):
                with self.subTest(field=field):
                    candidate = copy.deepcopy(request)
                    candidate["target"].pop(field)
                    self.assertIn(
                        f"target.{field}",
                        docking_gaps(candidate, strict_run=True),
                    )

    def test_nonfinite_vina_vectors_and_scores_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            for field in ("center", "size"):
                for value in (math.nan, math.inf, -math.inf):
                    with self.subTest(field=field, value=value):
                        request = self.base_vina_request(receptor, ligand)
                        request["docking"]["binding_site"][field][0] = value
                        gaps = docking_gaps(request)
                        self.assertIn(f"docking.binding_site.{field}", gaps)
                        with self.assertRaises(ValueError):
                            build_vina_argv_commands(request, root / "out")
            output = root / "nonfinite.pdbqt"
            output.write_text(
                "MODEL 1\nREMARK VINA RESULT: nan inf -inf\nENDMDL\n",
                encoding="utf-8",
            )
            self.assertEqual(parse_vina_pdbqt(output), [])

    def test_hosted_nonfinite_confidence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            record = {
                "ligand_id": "lig-1",
                "chemical_state_id": "state-1",
                "input_sha256": "a" * 64,
            }
            for confidence in (math.nan, math.inf, -math.inf):
                with self.subTest(confidence=confidence):
                    response = {
                        "status": "success",
                        "ligand_positions": ["pose\n$$$$\n"],
                        "position_confidence": [confidence],
                    }
                    with self.assertRaises(ValueError):
                        materialize_diffdock_response(response, record, root)

    def test_engine_and_objective_must_use_canonical_lowercase_enums(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            receptor, ligand = self.make_files(Path(temp))
            request = self.base_vina_request(receptor, ligand)
            request["docking"]["engine"] = "AutoDock-Vina"
            request["docking"]["objective"] = "Redocking"
            gaps = docking_gaps(request, strict_run=True)
            self.assertIn("docking.engine", gaps)
            self.assertIn("docking.objective", gaps)
            preflight = probe_environment(request, strict_run=True)
            self.assertFalse(preflight["ready_for_local_execution"])
            manifest = prepare_package(request, "prepare", Path(temp) / "package")
            self.assertEqual(manifest["execution"]["argv_commands"], [])

    def test_vina_parser_extracts_pose_scores(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "poses.pdbqt"
            output.write_text(
                "MODEL 1\nREMARK VINA RESULT: -8.2 0.000 0.000\nENDMDL\n"
                "MODEL 2\nREMARK VINA RESULT: -7.4 1.2 2.1\nENDMDL\n",
                encoding="utf-8",
            )
            poses = parse_vina_pdbqt(output)
            self.assertEqual([pose["pose_id"] for pose in poses], ["pose-1", "pose-2"])
            self.assertEqual([pose["metric_value"] for pose in poses], [-8.2, -7.4])

    def test_vina_builds_one_shell_free_argv_per_seed_with_grid(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            request = self.base_vina_request(receptor, ligand)
            commands = build_vina_argv_commands(request, root / "out")
            self.assertEqual(len(commands), 2)
            for command in commands:
                argv = command["argv"]
                self.assertIsInstance(argv, list)
                self.assertIn("--center_x", argv)
                self.assertIn("0", argv)
                self.assertIn("--exhaustiveness", argv)
                self.assertIn("--num_modes", argv)
            self.assertEqual({command["seed"] for command in commands}, {0, 11})

    def test_csv_library_requires_and_preserves_stable_ligand_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            manifest_file = root / "ligands.csv"
            with manifest_file.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=["ligand_id", "chemical_state_id", "file"]
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "ligand_id": "stable-42",
                        "chemical_state_id": "stable-42-state-a",
                        "file": str(ligand),
                    }
                )
            request = self.base_vina_request(receptor, ligand)
            request.pop("ligand")
            request["ligand_library"] = {
                "manifest_file": str(manifest_file),
                "chemical_state_strategy": "pre-enumerated states",
            }
            request["docking"]["seeds"] = [9]
            commands = build_vina_argv_commands(request, root / "out")
            self.assertEqual(commands[0]["ligand_id"], "stable-42")
            self.assertEqual(commands[0]["chemical_state_id"], "stable-42-state-a")

    def test_header_only_ligand_library_is_never_ready_for_any_engine(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            library = root / "empty-library.csv"
            library.write_text(
                "ligand_id,chemical_state_id,file\n", encoding="utf-8"
            )
            for engine in ("autodock-vina", "diffdock-nim-hosted"):
                with self.subTest(engine=engine):
                    request = self.base_vina_request(receptor, ligand)
                    request.pop("ligand")
                    request["ligand_library"] = {
                        "manifest_file": str(library),
                        "chemical_state_strategy": "pre-enumerated states",
                    }
                    request["docking"]["engine"] = engine
                    if engine == "diffdock-nim-hosted":
                        protein = root / "protein.pdb"
                        protein.write_text("ATOM\n", encoding="utf-8")
                        request["target"]["structure_file"] = str(protein)
                        request["docking"] = {
                            "objective": "pose-prediction",
                            "engine": engine,
                            "num_poses": 2,
                        }
                        request["tools"] = {
                            "diffdock_hosted": {
                                "endpoint": DIFFDOCK_HOSTED_ENDPOINT,
                                "service_version": "hosted-v1",
                                "terms_url": "https://example.org/terms",
                                "license": "service-terms",
                                "auth_env": "NVIDIA_API_KEY",
                            }
                        }
                        request["external_service"] = {
                            "authorized": True,
                            "credential_rotation_acknowledged": True,
                            "data_classification": "public",
                            "auth_env": "NVIDIA_API_KEY",
                            "endpoint": DIFFDOCK_HOSTED_ENDPOINT,
                        }
                    self.assertIn("ligand_library.empty", docking_gaps(request))
                    preflight = docking_workflow.preflight_docking(
                        request,
                        strict_run=True,
                        environ={"NVIDIA_API_KEY": "environment-only-test-value"},
                    )
                    self.assertFalse(preflight["ready_for_local_execution"])
                    package = prepare_package(
                        request, "prepare", root / f"package-{engine}"
                    )
                    self.assertEqual(package["execution"]["argv_commands"], [])

    def test_nested_schema_types_fail_closed_without_attribute_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            cases = (
                (("target",), "not-a-mapping", "target"),
                (("docking",), "not-a-mapping", "docking"),
                (
                    ("target", "receptor_preparation"),
                    "not-a-mapping",
                    "target.receptor_preparation",
                ),
                (
                    ("ligand", "chemical_state"),
                    ["not", "a", "mapping"],
                    "ligand.chemical_state",
                ),
                (
                    ("docking", "binding_site"),
                    "not-a-mapping",
                    "docking.binding_site",
                ),
                (
                    ("docking", "validation"),
                    "not-a-mapping",
                    "docking.validation",
                ),
                (("tools",), "not-a-mapping", "tools"),
                (
                    ("target", "chains"),
                    "A",
                    "target.chains",
                ),
                (
                    ("target", "receptor_preparation", "protonation"),
                    ["pH 7.4"],
                    "target.receptor_preparation.protonation",
                ),
                (
                    ("docking", "binding_site", "source"),
                    [],
                    "docking.binding_site.source",
                ),
                (
                    ("docking", "exhaustiveness"),
                    True,
                    "docking.exhaustiveness",
                ),
            )
            for path, value, expected in cases:
                with self.subTest(path=path):
                    request = self.base_vina_request(receptor, ligand)
                    cursor = request
                    for key in path[:-1]:
                        cursor = cursor[key]
                    cursor[path[-1]] = value
                    for strict_run in (False, True):
                        gaps = docking_gaps(request, strict_run=strict_run)
                        self.assertIn(expected, gaps)
                    package = prepare_package(
                        request, "prepare", root / ("schema-" + "-".join(path))
                    )
                    self.assertIn(
                        expected,
                        package["execution"]["command_problems"],
                    )

            hosted = self.base_vina_request(receptor, ligand)
            hosted["docking"] = {
                "objective": "pose-prediction",
                "engine": "diffdock-nim-hosted",
                "num_poses": 2,
            }
            hosted["external_service"] = "not-a-mapping"
            self.assertIn(
                "external_service",
                docking_gaps(hosted, strict_run=True, environ={}),
            )

    def test_dynamic_required_tools(self) -> None:
        self.assertEqual(
            required_tools({"docking": {"engine": "autodock-vina"}}),
            ["autodock_vina"],
        )
        self.assertEqual(
            required_tools(
                {
                    "docking": {
                        "engine": "autodock-vina",
                        "preparation": {"use_meeko": True},
                        "analysis": {"plip": True},
                        "visualization": {"pymol": True},
                    }
                }
            ),
            ["autodock_vina", "meeko", "plip", "pymol"],
        )
        self.assertEqual(
            required_tools({"docking": {"engine": "diffdock-nim-hosted"}}),
            ["diffdock_hosted"],
        )
        self.assertEqual(
            required_tools({"docking": {"engine": "diffdock-nim-self-hosted"}}),
            ["diffdock_self_hosted"],
        )

    def test_hosted_diffdock_rejects_stale_endpoint_unauthorized_missing_env_and_tokens(self) -> None:
        request = {
            "target": {
                "structure_file": "r.pdb",
                "chains": ["A"],
                "biological_state": "monomer",
                "receptor_preparation": {
                    "protonation": "documented",
                    "missing_residues": "documented",
                    "alternate_locations": "highest occupancy",
                    "hetatm_policy": "retain relevant",
                },
            },
            "ligand": {
                "file": "l.sdf",
                "ligand_id": "l1",
                "chemical_state": {"strategy": "as provided", "chemical_state_id": "l1-s1"},
            },
            "docking": {
                "objective": "pose-prediction",
                "engine": "diffdock-nim-hosted",
                "num_poses": 5,
            },
            "external_service": {
                "authorized": False,
                "credential_rotation_acknowledged": False,
                "data_classification": "sensitive",
                "auth_env": "TOKEN",
                "endpoint": "https://stale.example/v1/diffdock",
            },
        }
        joined = "\n".join(docking_gaps(request, strict_run=True, environ={}))
        self.assertIn("external_service.authorized", joined)
        self.assertIn("external_service.endpoint", joined)
        self.assertIn("environment:NVIDIA_API_KEY", joined)
        for token in (
            "nvapi-" + "abcdef1234567890",
            "gho_" + "abcdefghijklmnopqrstuvwxyz",
            "sk-" + "proj-abcdefghijklmnopqrstuvwxyz",
        ):
            request["notes"] = {"nested": [token]}
            with self.subTest(kind=token.split("_", 1)[0].split("-", 1)[0]):
                with self.assertRaisesRegex(ValueError, "credential-like"):
                    docking_gaps(request)
        self.assertNotIn("abcdef1234567890", str(docking_gaps))
        self.assertEqual(
            DIFFDOCK_HOSTED_ENDPOINT,
            "https://health.api.nvidia.com/v1/molecular-docking/diffdock/generate",
        )

    def test_prepare_builds_docking_artifacts_but_strict_run_blocks_missing_vina(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            request = self.base_vina_request(receptor, ligand)
            request["backend"] = {"kind": "local", "gpu_vram_gb": 8}
            out = root / "package"
            manifest = prepare_package(request, "prepare", out)
            self.assertEqual(manifest["schema_version"], "1.1")
            for name in ("commands.ps1", "docking_candidates.csv", "docking_report.md"):
                self.assertTrue((out / name).is_file(), name)
            self.assertEqual(len(manifest["execution"]["argv_commands"]), 2)
            self.assertTrue(audit_manifest(manifest)["passed"])
            run_manifest = prepare_package(request, "run", root / "run-package")
            self.assertFalse(audit_manifest(run_manifest)["passed"])
            self.assertTrue(
                any("autodock_vina" in error for error in audit_manifest(run_manifest)["errors"])
            )

    def test_vina_and_diffdock_candidate_semantics_are_audited(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "docking_candidates.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=DOCKING_CANDIDATE_FIELDS)
                writer.writeheader()
                base = {
                    "ligand_id": "l1",
                    "chemical_state_id": "l1-s1",
                    "pose_id": "p1",
                    "pose_rank_within_ligand": 1,
                    "engine_version": "1",
                    "receptor_state_id": "r1",
                    "seed": 1,
                    "metric_value": -8,
                    "metric_unit": "kcal/mol",
                    "input_sha256": "a" * 64,
                    "output_path": "out.pdbqt",
                    "evidence_status": "computational_prediction",
                    "experimental_status": "not-tested",
                    "notes": "",
                }
                writer.writerow(
                    {
                        **base,
                        "engine": "autodock-vina",
                        "metric_name": "vina_score",
                        "metric_role": "experimental-free-energy",
                        "rank_scope": "within-ligand",
                    }
                )
                writer.writerow(
                    {
                        **base,
                        "ligand_id": "l2",
                        "engine": "diffdock-nim-hosted",
                        "metric_name": "confidence",
                        "metric_unit": "unitless",
                        "metric_role": "affinity-ranking-score",
                        "rank_scope": "across-ligands",
                    }
                )
            errors = "\n".join(audit_docking_candidates(path))
            self.assertIn("Vina score", errors)
            self.assertIn("DiffDock confidence", errors)

    def test_prediction_only_docking_rows_cannot_claim_experimental_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "docking_candidates.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=DOCKING_CANDIDATE_FIELDS)
                writer.writeheader()
                writer.writerow(
                    {
                        "ligand_id": "l1",
                        "chemical_state_id": "l1-s1",
                        "pose_id": "p1",
                        "pose_rank_within_ligand": 1,
                        "engine": "autodock-vina",
                        "engine_version": "1.2.7",
                        "receptor_state_id": "r1",
                        "seed": 1,
                        "metric_name": "vina_score",
                        "metric_value": -8,
                        "metric_unit": "kcal/mol",
                        "metric_role": "protocol-specific-ranking-score",
                        "rank_scope": "within-ligand",
                        "input_sha256": "a" * 64,
                        "output_path": "out.pdbqt",
                        "evidence_status": "prediction_only",
                        "experimental_status": "validated active binder",
                        "notes": "experimentally validated success",
                    }
                )
            errors = audit_docking_candidates(path)
            self.assertGreaterEqual(len(errors), 1)

    def test_prediction_notes_reject_positive_claims_but_allow_explicit_negation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "pose.pdbqt"
            output.write_text("MODEL 1\nENDMDL\n", encoding="utf-8")
            path = root / "docking_candidates.csv"
            base = {
                "run_id": "run-1",
                "ligand_id": "lig-1",
                "chemical_state_id": "state-1",
                "pose_id": "lig-1-state-1-pose-1",
                "pose_rank_within_ligand": 1,
                "engine": "autodock-vina",
                "engine_version": "1.2.7",
                "receptor_state_id": "rec-1",
                "seed": 1,
                "metric_name": "vina_score",
                "metric_value": -8,
                "metric_unit": "kcal/mol",
                "metric_role": "protocol-specific-ranking-score",
                "rank_scope": "within-ligand",
                "input_sha256": "a" * 64,
                "raw_output_sha256": "b" * 64,
                "output_path": str(output),
                "evidence_status": "computational_prediction",
                "experimental_status": "not-tested",
            }
            for phrase in (
                "validated affinity",
                "active compound",
                "selective inhibitor",
                "confirmed hit",
                "lead candidate",
                "strong binder",
                "binding free energy",
                "binding free-energy",
            ):
                with self.subTest(phrase=phrase):
                    with path.open("w", encoding="utf-8", newline="") as handle:
                        writer = csv.DictWriter(
                            handle, fieldnames=DOCKING_CANDIDATE_FIELDS
                        )
                        writer.writeheader()
                        writer.writerow({**base, "notes": phrase})
                    self.assertIn(
                        "positive biological or affinity claim",
                        "\n".join(audit_docking_candidates(path)).casefold(),
                    )
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=DOCKING_CANDIDATE_FIELDS
                )
                writer.writeheader()
                writer.writerow(
                    {
                        **base,
                        "notes": (
                            "This is not an affinity score, not a binding free "
                            "energy, and does not establish an active, selective "
                            "inhibitor, hit, lead, binder, or validated result."
                        ),
                    }
                )
            self.assertEqual(audit_docking_candidates(path), [])
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=DOCKING_CANDIDATE_FIELDS
                )
                writer.writeheader()
                writer.writerow({**base, "notes": "non-binder control"})
            self.assertEqual(audit_docking_candidates(path), [])

    @patch("docking_workflow.subprocess.run")
    def test_vina_execution_uses_shell_false(self, run_mock) -> None:
        from docking_workflow import execute_vina_commands

        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = ""
        run_mock.return_value.stderr = ""
        commands = [{"argv": ["vina", "--seed", "1"], "output_path": "x.pdbqt"}]
        execute_vina_commands(commands, cwd=Path("."))
        self.assertFalse(run_mock.call_args.kwargs["shell"])

    def test_probe_environment_dispatches_to_docking_preflight(self) -> None:
        request = {
            "route": "molecular-docking-screen",
            "docking": {"engine": "autodock-vina"},
            "tools": {"autodock_vina": {"executable": "C:/missing/vina.exe"}},
        }
        result = probe_environment(request, strict_run=True)
        self.assertFalse(result["ready_for_local_execution"])
        self.assertTrue(any("autodock_vina" in issue for issue in result["issues"]))

    def test_every_subprocess_run_explicitly_disables_shell(self) -> None:
        scripts = Path(__file__).parent
        for name in ("baker_design.py", "docking_workflow.py"):
            tree = ast.parse((scripts / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                function = node.func
                if not (
                    isinstance(function, ast.Attribute)
                    and isinstance(function.value, ast.Name)
                    and function.value.id == "subprocess"
                    and function.attr == "run"
                ):
                    continue
                shell = next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "shell"),
                    None,
                )
                self.assertIsInstance(shell, ast.Constant, f"{name}:{node.lineno}")
                self.assertIs(shell.value, False, f"{name}:{node.lineno}")

    def test_self_hosted_diffdock_run_is_blocked_without_builtin_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            request = self.base_vina_request(receptor, ligand)
            request["docking"] = {
                "objective": "pose-prediction",
                "engine": "diffdock-nim-self-hosted",
                "num_poses": 4,
                "argv_commands": [
                    ["C:/tools/diffdock-client.exe", "--endpoint", "http://127.0.0.1:8000"]
                ],
            }
            request["tools"] = {
                "diffdock_self_hosted": {
                    "endpoint": "http://127.0.0.1:8000",
                    "repository": "https://example.org/diffdock",
                    "version": "1.0",
                    "license": "test-license",
                },
                "plip": self.vina_tools()["plip"],
                "pymol": self.vina_tools()["pymol"],
            }
            manifest = prepare_package(request, "run", root / "out")
            result = audit_manifest(manifest)
            self.assertFalse(result["passed"])
            self.assertIn("adapter-not-implemented", "\n".join(result["errors"]))

    def test_schema_1_0_and_1_1_are_both_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            request = self.base_vina_request(receptor, ligand)
            manifest = prepare_package(request, "prepare", root / "out")
            for version in ("1.0", "1.1"):
                with self.subTest(version=version):
                    manifest["schema_version"] = version
                    self.assertTrue(audit_manifest(manifest)["passed"])
            manifest["schema_version"] = "2.0"
            self.assertFalse(audit_manifest(manifest)["passed"])
            for invalid in (1.0, 1.1, True, False):
                with self.subTest(invalid=invalid):
                    manifest["schema_version"] = invalid
                    result = audit_manifest(manifest)
                    self.assertFalse(result["passed"])
                    self.assertIn(
                        "schema_version",
                        "\n".join(result["errors"]),
                    )

    def test_explicit_receptor_state_id_must_be_canonical_and_derived_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            receptor, ligand = self.make_files(Path(temp))
            request = self.base_vina_request(receptor, ligand)
            for receptor_state_id in (
                "Rec-State-1",
                "../rec-state-1",
                "con",
                "rec-state-1.",
            ):
                with self.subTest(receptor_state_id=receptor_state_id):
                    candidate = copy.deepcopy(request)
                    candidate["target"][
                        "receptor_state_id"
                    ] = receptor_state_id
                    self.assertIn(
                        "target.receptor_state_id-unsafe",
                        docking_gaps(candidate, strict_run=True),
                    )
            target = copy.deepcopy(request["target"])
            target.pop("receptor_state_id")
            first = docking_workflow.stable_receptor_state_id(target)
            second = docking_workflow.stable_receptor_state_id(
                copy.deepcopy(target)
            )
            self.assertEqual(first, second)
            self.assertEqual(first, first.casefold())
            self.assertTrue(validate_stable_id(first))

    def test_request_schema_gate_preserves_supported_versions_and_rejects_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            for version in ("1.0", "1.1"):
                with self.subTest(version=version):
                    request = self.base_vina_request(receptor, ligand)
                    request["schema_version"] = version
                    manifest = prepare_package(
                        request, "prepare", root / f"schema-{version}"
                    )
                    self.assertEqual(manifest["schema_version"], version)
                    target_manifest = baker_design.load_yaml(
                        root / f"schema-{version}" / "target_manifest.yaml"
                    )
                    self.assertEqual(target_manifest["schema_version"], version)
            request = self.base_vina_request(receptor, ligand)
            manifest = prepare_package(request, "prepare", root / "schema-default")
            self.assertEqual(manifest["schema_version"], "1.1")
            request["schema_version"] = "2.0"
            rejected = root / "schema-rejected"
            with self.assertRaisesRegex(ValueError, "schema_version"):
                prepare_package(request, "prepare", rejected)
            with self.assertRaisesRegex(ValueError, "schema_version"):
                probe_environment(request, strict_run=False)
            self.assertFalse(rejected.exists())

            request_path = root / "unknown-schema.yaml"
            write_yaml(request_path, request)
            output = io.StringIO()
            argv = [
                "environment_check.py",
                "--request",
                str(request_path),
                "--strict-run",
            ]
            with patch.object(sys, "argv", argv), redirect_stdout(output):
                self.assertEqual(environment_check.main(), 2)
            self.assertIn("schema_version", output.getvalue())

    def test_stable_ids_reject_path_traversal_controls_and_windows_devices(self) -> None:
        for value in (
            ".",
            "..",
            "../escape",
            "a/b",
            "a\\b",
            "bad\nid",
            "CON",
            "nul.txt",
        ):
            with self.subTest(value=repr(value)):
                self.assertFalse(validate_stable_id(value))
        self.assertFalse(validate_stable_id("Ligand_01.state-A"))
        self.assertTrue(validate_stable_id("ligand_01.state-a"))

    def test_single_and_csv_ids_cannot_escape_output_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            request = self.base_vina_request(receptor, ligand)
            request["ligand"]["ligand_id"] = "../escape"
            self.assertIn("ligand.ligand_id-unsafe", docking_gaps(request))

            manifest_file = root / "unsafe.csv"
            with manifest_file.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=["ligand_id", "chemical_state_id", "file"]
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "ligand_id": "safe",
                        "chemical_state_id": "..\\escape",
                        "file": str(ligand),
                    }
                )
            request.pop("ligand")
            request["ligand_library"] = {
                "manifest_file": str(manifest_file),
                "chemical_state_strategy": "pre-enumerated",
            }
            self.assertTrue(
                any("chemical_state_id-unsafe" in gap for gap in docking_gaps(request))
            )

    def test_output_paths_include_ligand_state_and_seed_without_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            manifest_file = root / "states.csv"
            with manifest_file.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=["ligand_id", "chemical_state_id", "file"]
                )
                writer.writeheader()
                for state in ("state-a", "state-b"):
                    writer.writerow(
                        {
                            "ligand_id": "lig-1",
                            "chemical_state_id": state,
                            "file": str(ligand),
                        }
                    )
            request = self.base_vina_request(receptor, ligand)
            request.pop("ligand")
            request["ligand_library"] = {
                "manifest_file": str(manifest_file),
                "chemical_state_strategy": "pre-enumerated",
            }
            request["docking"]["seeds"] = [3]
            output = (root / "package").resolve()
            commands = build_vina_argv_commands(request, output)
            paths = [Path(command["output_path"]).resolve() for command in commands]
            self.assertEqual(len(paths), len(set(paths)))
            for path in paths:
                self.assertTrue(path.is_relative_to(output))
                self.assertIn("seed_3", path.parts)
            self.assertNotEqual(paths[0].parent, paths[1].parent)

    def test_seed_and_seeds_conflict_and_duplicate_seeds_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            receptor, ligand = self.make_files(Path(temp))
            request = self.base_vina_request(receptor, ligand)
            request["docking"]["seed"] = 5
            self.assertIn("docking.seed-xor-seeds", docking_gaps(request))
            request["docking"].pop("seed")
            request["docking"]["seeds"] = [5, 5]
            self.assertIn("docking.seeds-duplicate", docking_gaps(request))

    def test_vina_manifest_argv_has_digest_and_tamper_blocks_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            fake_vina = root / "vina.exe"
            fake_vina.write_text("not executed", encoding="utf-8")
            request = self.base_vina_request(receptor, ligand)
            request["tools"] = self.vina_tools(str(fake_vina))
            out = root / "run"
            manifest = prepare_package(request, "run", out)
            command = manifest["execution"]["argv_commands"][0]
            self.assertEqual(command["argv_sha256"], argv_sha256(command["argv"]))
            command["argv"][-1] = str(root / "escape.pdbqt")
            write_yaml(out / "run_manifest.yaml", manifest)
            with patch("docking_workflow.subprocess.run") as run_mock:
                result = execute_manifest(out / "run_manifest.yaml", manifest)
            self.assertEqual(result, 2)
            run_mock.assert_not_called()

    def test_strict_run_rehashes_inputs_and_detects_post_prepare_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            fake_vina = root / "vina.exe"
            fake_vina.write_text("not executed", encoding="utf-8")
            request = self.base_vina_request(receptor, ligand)
            request["tools"] = self.vina_tools(str(fake_vina))
            manifest = prepare_package(request, "run", root / "run")
            ligand.write_text("MODEL 1\nREMARK changed\nENDMDL\n", encoding="utf-8")
            result = audit_manifest(manifest)
            self.assertFalse(result["passed"])
            self.assertIn("hash mismatch", "\n".join(result["errors"]).casefold())

    def test_redocking_source_reference_and_mapping_inputs_are_hash_anchored(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            source = root / "source-structure.pdbqt"
            reference = root / "reference-pose.pdbqt"
            mapping = root / "atom-mapping.csv"
            source.write_text("ATOM source\n", encoding="utf-8")
            reference.write_text("MODEL reference\nENDMDL\n", encoding="utf-8")
            mapping.write_text("reference,predicted\nC1,C1\n", encoding="utf-8")
            fake_vina = root / "vina.exe"
            fake_vina.write_text("not executed", encoding="utf-8")
            request = self.base_vina_request(receptor, ligand)
            request["target"]["source_structure_file"] = str(source)
            request["target"]["source_structure_sha256"] = (
                docking_workflow._sha256_file(source)
            )
            validation = request["docking"]["validation"]
            validation["reference_pose"] = str(reference)
            validation["reference_pose_sha256"] = (
                docking_workflow._sha256_file(reference)
            )
            validation["atom_mapping"] = {
                "file": str(mapping),
                "sha256": docking_workflow._sha256_file(mapping),
            }
            request["tools"] = self.vina_tools(str(fake_vina))
            manifest = prepare_package(request, "run", root / "run")
            for label in (
                "target_source_structure",
                "docking_reference_pose",
                "docking_atom_mapping",
            ):
                self.assertRegex(
                    manifest["input_hashes"][label]["sha256"], r"^[0-9a-f]{64}$"
                )
            for path in (source, reference, mapping):
                with self.subTest(path=path.name):
                    original = path.read_bytes()
                    path.write_bytes(original + b"changed")
                    self.assertFalse(audit_manifest(manifest)["passed"])
                    path.write_bytes(original)

            mismatch = copy.deepcopy(request)
            mismatch["target"]["source_structure_sha256"] = "b" * 64
            mismatch_manifest = prepare_package(
                mismatch, "run", root / "declared-mismatch"
            )
            mismatch_audit = audit_manifest(mismatch_manifest)
            self.assertFalse(mismatch_audit["passed"])
            self.assertIn(
                "declared sha-256",
                "\n".join(mismatch_audit["errors"]).casefold(),
            )

    def test_strict_vina_requires_pdbqt_and_hosted_requires_pdb_plus_sdf_or_mol2(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            request = self.base_vina_request(receptor, ligand)
            pdb = root / "receptor.pdb"
            pdb.write_text("ATOM\n", encoding="utf-8")
            request["target"]["structure_file"] = str(pdb)
            self.assertIn(
                "target.structure_file-format:pdbqt",
                docking_gaps(request, strict_run=True),
            )
            request["docking"]["engine"] = "diffdock-nim-hosted"
            request["docking"].pop("binding_site")
            request["docking"].pop("seeds")
            request["ligand"]["file"] = str(ligand)
            request["external_service"] = {
                "authorized": True,
                "credential_rotation_acknowledged": True,
                "data_classification": "public",
                "auth_env": "NVIDIA_API_KEY",
                "endpoint": DIFFDOCK_HOSTED_ENDPOINT,
            }
            gaps = docking_gaps(
                request,
                strict_run=True,
                environ={"NVIDIA_API_KEY": "environment-only-test-value"},
            )
            self.assertIn("ligand.file-format:sdf-or-mol2", gaps)

    def test_library_inputs_are_all_hashed_and_missing_member_blocks_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            second = root / "second.pdbqt"
            second.write_text("MODEL 1\nENDMDL\n", encoding="utf-8")
            library = root / "library.csv"
            with library.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=["ligand_id", "chemical_state_id", "file"]
                )
                writer.writeheader()
                writer.writerow(
                    {"ligand_id": "l1", "chemical_state_id": "s1", "file": str(ligand)}
                )
                writer.writerow(
                    {"ligand_id": "l2", "chemical_state_id": "s2", "file": str(second)}
                )
            request = self.base_vina_request(receptor, ligand)
            request.pop("ligand")
            request["ligand_library"] = {
                "manifest_file": str(library),
                "chemical_state_strategy": "pre-enumerated",
            }
            fake_vina = root / "vina.exe"
            fake_vina.write_text("not executed", encoding="utf-8")
            request["tools"] = self.vina_tools(str(fake_vina))
            manifest = prepare_package(request, "run", root / "run")
            ligand_records = [
                key
                for key in manifest["input_hashes"]
                if key.startswith("ligand_library_member:")
            ]
            self.assertEqual(len(ligand_records), 2)
            second.unlink()
            self.assertFalse(audit_manifest(manifest)["passed"])

    def test_sensitive_keys_and_authorization_schemes_are_rejected_without_echo(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            receptor, ligand = self.make_files(Path(temp))
            request = self.base_vina_request(receptor, ligand)
            for secret_fragment in (
                {"api_key": "opaque-value"},
                {"nested": {"password": "opaque-value"}},
                {"header": "Bearer opaque-value"},
                {"header": "Basic opaque-value"},
            ):
                candidate = {**request, "notes": secret_fragment}
                with self.subTest(keys=list(secret_fragment)):
                    with self.assertRaisesRegex(ValueError, "credential-like"):
                        docking_gaps(candidate)

    def test_candidate_statuses_are_exact_and_completed_run_requires_current_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "docking_candidates.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=DOCKING_CANDIDATE_FIELDS)
                writer.writeheader()
                writer.writerow(
                    {
                        "run_id": "old-run",
                        "ligand_id": "l1",
                        "chemical_state_id": "s1",
                        "pose_id": "p1",
                        "pose_rank_within_ligand": 1,
                        "engine": "autodock-vina",
                        "engine_version": "1.2.7",
                        "receptor_state_id": "r1",
                        "seed": 1,
                        "metric_name": "vina_score",
                        "metric_value": -7,
                        "metric_unit": "kcal/mol",
                        "metric_role": "protocol-specific-ranking-score",
                        "rank_scope": "within-ligand-and-seed",
                        "input_sha256": "a" * 64,
                        "output_path": "p.pdbqt",
                        "evidence_status": "current_experimental",
                        "experimental_status": "validated",
                    }
                )
            errors = audit_docking_candidates(
                path,
                expected_run_id="current-run",
                execution_status="completed-computational-only",
            )
            joined = "\n".join(errors)
            self.assertIn("evidence_status", joined)
            self.assertIn("experimental_status", joined)
            self.assertIn("current run", joined)

    def test_prepare_truncates_stale_docking_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            request = self.base_vina_request(receptor, ligand)
            out = root / "package"
            prepare_package(request, "prepare", out)
            with (out / "docking_candidates.csv").open(
                "a", encoding="utf-8", newline=""
            ) as handle:
                handle.write("stale,row\n")
            prepare_package(request, "prepare", out)
            with (out / "docking_candidates.csv").open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows, [])

    def test_official_hosted_payload_and_response_materialization(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            protein = root / "protein.pdb"
            ligand = root / "ligand.sdf"
            protein.write_text("ATOM official-protein\n", encoding="utf-8")
            ligand.write_text("official-ligand\n$$$$\n", encoding="utf-8")
            request = self.base_vina_request(protein, ligand)
            request["docking"] = {
                "objective": "pose-prediction",
                "engine": "diffdock-nim-hosted",
                "num_poses": 2,
                "time_divisions": 10,
                "steps": 18,
                "save_trajectory": False,
                "skip_gen_conformer": False,
                "is_staged": False,
            }
            request["external_service"] = {
                "authorized": True,
                "credential_rotation_acknowledged": True,
                "data_classification": "public",
                "auth_env": "NVIDIA_API_KEY",
                "endpoint": DIFFDOCK_HOSTED_ENDPOINT,
            }
            hosted = build_hosted_requests(request)
            payload = hosted[0]["payload"]
            self.assertEqual(
                set(payload),
                {
                    "protein",
                    "ligand",
                    "ligand_file_type",
                    "num_poses",
                    "time_divisions",
                    "steps",
                    "save_trajectory",
                    "skip_gen_conformer",
                    "is_staged",
                },
            )
            self.assertIsInstance(payload["protein"], str)
            self.assertIsInstance(payload["ligand"], str)
            fixture = {
                "status": "success",
                "details": "",
                "protein": payload["protein"],
                "ligand": payload["ligand"],
                "ligand_positions": ["pose-one\n$$$$\n", "pose-two\n$$$$\n"],
                "position_confidence": [0.9, 0.8],
                "trajectory": None,
            }
            response = MagicMock()
            raw_fixture = json.dumps(fixture).encode("utf-8")
            response.__enter__.return_value.read.return_value = raw_fixture
            response.__enter__.return_value.headers = {
                "X-NIM-Version": "2026.07",
                "Authorization": "must-not-be-recorded",
            }
            with patch("docking_workflow._open_no_redirect", return_value=response) as opener:
                observed = execute_hosted_diffdock(
                    payload,
                    external_service=request["external_service"],
                    environ={"NVIDIA_API_KEY": "environment-only-test-value"},
                )
            opener.assert_called_once()
            sent = json.loads(opener.call_args.args[0].data.decode("utf-8"))
            self.assertEqual(sent, payload)
            self.assertEqual(observed.raw_bytes, raw_fixture)
            self.assertEqual(
                observed.observed_headers, {"x-nim-version": "2026.07"}
            )
            self.assertNotIn("authorization", observed.observed_headers)
            rows = materialize_diffdock_response(observed, hosted[0], root / "package")
            self.assertEqual(len(rows), 2)
            for row in rows:
                path = Path(row["output_path"]).resolve()
                self.assertTrue(path.is_relative_to((root / "package").resolve()))
                self.assertTrue(path.is_file())

    def test_official_hosted_limits_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            protein = root / "protein.pdb"
            ligand = root / "ligand.sdf"
            protein.write_text("ATOM\n", encoding="utf-8")
            ligand.write_text("ligand\n$$$$\n", encoding="utf-8")
            request = self.base_vina_request(protein, ligand)
            request["docking"] = {
                "objective": "pose-prediction",
                "engine": "diffdock-nim-hosted",
                "num_poses": 101,
                "time_divisions": 21,
                "steps": 19,
            }
            joined = "\n".join(docking_gaps(request))
            self.assertIn("docking.num_poses", joined)
            self.assertIn("docking.time_divisions", joined)
            self.assertIn("docking.steps", joined)

    def test_meeko_mode_never_passes_raw_pdb_or_sdf_directly_to_vina(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor = root / "receptor.pdb"
            ligand = root / "ligand.sdf"
            receptor_pdbqt = root / "prepared-receptor.pdbqt"
            ligand_pdbqt = root / "prepared-ligand.pdbqt"
            receptor.write_text("ATOM\n", encoding="utf-8")
            ligand.write_text("ligand\n$$$$\n", encoding="utf-8")
            receptor_pdbqt.write_text("ATOM\n", encoding="utf-8")
            ligand_pdbqt.write_text("MODEL 1\nENDMDL\n", encoding="utf-8")
            request = self.base_vina_request(receptor, ligand)
            request["docking"]["preparation"] = {"use_meeko": True}
            self.assertIn(
                "docking.preparation.receptor_pdbqt",
                docking_gaps(request, strict_run=True),
            )
            request["docking"]["preparation"]["receptor_pdbqt"] = str(receptor_pdbqt)
            request["ligand"]["prepared_file"] = str(ligand_pdbqt)
            self.assertEqual(docking_gaps(request, strict_run=True), [])
            command = build_vina_argv_commands(request, root / "out")[0]["argv"]
            self.assertIn(str(receptor_pdbqt), command)
            self.assertIn(str(ligand_pdbqt), command)
            self.assertNotIn(str(receptor), command)
            self.assertNotIn(str(ligand), command)

    def test_vina_zero_pose_output_fails_post_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            fake_vina = root / "vina.exe"
            fake_vina.write_text("not executed", encoding="utf-8")
            request = self.base_vina_request(receptor, ligand)
            request["docking"]["seeds"] = [7]
            request["tools"] = self.vina_tools(str(fake_vina))
            out = root / "run"
            manifest = prepare_package(request, "run", out)
            write_yaml(out / "run_manifest.yaml", manifest)

            def fake_run(argv, **kwargs):
                output = Path(argv[argv.index("--out") + 1])
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("MODEL 1\nENDMDL\n", encoding="utf-8")
                completed = MagicMock()
                completed.returncode = 0
                completed.stdout = ""
                completed.stderr = ""
                return completed

            with patch("docking_workflow.subprocess.run", side_effect=fake_run):
                result = execute_manifest(out / "run_manifest.yaml", manifest)
            self.assertEqual(result, 2)
            self.assertEqual(manifest["execution"]["status"], "failed-post-audit")
            self.assertIn(
                "failed-post-audit",
                (out / "docking_report.md").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "Passed: `false`",
                (out / "audit_report.md").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "zero poses",
                (out / "audit_report.md").read_text(encoding="utf-8").casefold(),
            )

    def test_vina_success_without_output_is_a_persisted_terminal_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            fake_vina = root / "vina.exe"
            fake_vina.write_text("not executed", encoding="utf-8")
            request = self.base_vina_request(receptor, ligand)
            request["docking"]["seeds"] = [7]
            request["tools"] = self.vina_tools(str(fake_vina))
            out = root / "run"
            manifest = prepare_package(request, "run", out)
            manifest_path = out / "run_manifest.yaml"
            write_yaml(manifest_path, manifest)
            completed = MagicMock(returncode=0, stdout="", stderr="")
            with patch("docking_workflow.subprocess.run", return_value=completed):
                self.assertEqual(execute_manifest(manifest_path, manifest), 2)
            self.assertEqual(
                manifest["execution"]["status"], "failed-post-audit"
            )
            self.assertIn(
                "output missing",
                "\n".join(manifest["execution"]["audit_errors"]).casefold(),
            )
            self.assertIn(
                "Passed: `false`",
                (out / "audit_report.md").read_text(encoding="utf-8"),
            )

    def test_hosted_adapter_failure_is_generic_and_persisted_without_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            protein = root / "protein.pdb"
            ligand = root / "ligand.sdf"
            protein.write_text("ATOM\n", encoding="utf-8")
            ligand.write_text("ligand\n$$$$\n", encoding="utf-8")
            request = self.base_vina_request(protein, ligand)
            request["docking"] = {
                "objective": "pose-prediction",
                "engine": "diffdock-nim-hosted",
                "num_poses": 1,
            }
            request["tools"] = {
                "diffdock_hosted": {
                    "endpoint": DIFFDOCK_HOSTED_ENDPOINT,
                    "service_version": "hosted-v1",
                    "terms_url": "https://example.org/terms",
                    "license": "service-terms",
                    "auth_env": "NVIDIA_API_KEY",
                }
            }
            request["external_service"] = {
                "authorized": True,
                "credential_rotation_acknowledged": True,
                "data_classification": "public",
                "auth_env": "NVIDIA_API_KEY",
                "endpoint": DIFFDOCK_HOSTED_ENDPOINT,
            }
            out = root / "hosted-failure"
            with patch.dict(
                os.environ, {"NVIDIA_API_KEY": "environment-only-test-value"}
            ):
                manifest = prepare_package(request, "run", out)
                manifest_path = out / "run_manifest.yaml"
                write_yaml(manifest_path, manifest)
                with patch(
                    "docking_workflow._open_no_redirect",
                    side_effect=RuntimeError(
                        "Authorization: Bearer " + "MUST_NOT_PERSIST"
                    ),
                ):
                    self.assertEqual(execute_manifest(manifest_path, manifest), 2)
            self.assertEqual(
                manifest["execution"]["status"], "failed-hosted-adapter"
            )
            persisted = "\n".join(
                path.read_text(encoding="utf-8", errors="replace")
                for path in (
                    manifest_path,
                    out / "docking_report.md",
                    out / "audit_report.md",
                )
            )
            self.assertNotIn("MUST_NOT_PERSIST", persisted)
            self.assertIn("failed-hosted-adapter", persisted)
            self.assertIn("hosted request failed", persisted)

    def test_default_test_network_guard_blocks_before_any_urllib_opener(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            protein = root / "protein.pdb"
            ligand = root / "ligand.sdf"
            protein.write_text("ATOM\n", encoding="utf-8")
            ligand.write_text("ligand\n$$$$\n", encoding="utf-8")
            request = self.base_vina_request(protein, ligand)
            request["docking"] = {
                "objective": "pose-prediction",
                "engine": "diffdock-nim-hosted",
                "num_poses": 1,
            }
            request["external_service"] = {
                "authorized": True,
                "credential_rotation_acknowledged": True,
                "data_classification": "public",
                "auth_env": "NVIDIA_API_KEY",
                "endpoint": DIFFDOCK_HOSTED_ENDPOINT,
            }
            hosted_request = build_hosted_requests(request)[0]
            with (
                patch(
                    "docking_workflow.urllib.request.build_opener"
                ) as builder,
                patch("docking_workflow.urllib.request.urlopen") as urlopen,
            ):
                with self.assertRaisesRegex(
                    AssertionError, "NetworkDisabled"
                ):
                    execute_hosted_diffdock(
                        hosted_request["payload"],
                        external_service=request["external_service"],
                        environ={
                            "NVIDIA_API_KEY": "environment-only-test-value"
                        },
                    )
            self.network_guard.assert_called_once()
            builder.assert_not_called()
            urlopen.assert_not_called()

    def test_low_level_network_guard_blocks_unmocked_urllib_and_direct_socket(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            protein = root / "protein.pdb"
            ligand = root / "ligand.sdf"
            protein.write_text("ATOM\n", encoding="utf-8")
            ligand.write_text("ligand\n$$$$\n", encoding="utf-8")
            request = self.base_vina_request(protein, ligand)
            request["docking"] = {
                "objective": "pose-prediction",
                "engine": "diffdock-nim-hosted",
                "num_poses": 1,
            }
            request["external_service"] = {
                "authorized": True,
                "credential_rotation_acknowledged": True,
                "data_classification": "public",
                "auth_env": "NVIDIA_API_KEY",
                "endpoint": DIFFDOCK_HOSTED_ENDPOINT,
            }
            hosted_request = build_hosted_requests(request)[0]
            with patch(
                "docking_workflow._open_no_redirect",
                self._real_open_no_redirect,
            ):
                with self.assertRaisesRegex(
                    AssertionError, "NetworkDisabled"
                ):
                    execute_hosted_diffdock(
                        hosted_request["payload"],
                        external_service=request["external_service"],
                        environ={
                            "NVIDIA_API_KEY": "environment-only-test-value"
                        },
                    )
            self.socket_create_guard.assert_called_once()

        with socket.socket() as probe:
            with self.assertRaisesRegex(AssertionError, "NetworkDisabled"):
                probe.connect(("127.0.0.1", 9))
        self.socket_connect_guard.assert_called_once()

    def test_hosted_redirects_are_never_followed_or_persisted(self) -> None:
        class RedirectProbeOpener:
            def __init__(self, handler, status):
                self.handler = handler
                self.status = status
                self.request_urls = []

            def open(self, request, timeout):
                self.request_urls.append(request.full_url)
                redirected = self.handler.redirect_request(
                    request,
                    None,
                    self.status,
                    "redirect blocked",
                    {"Location": "https://evil.example/steal"},
                    "https://evil.example/steal",
                )
                if redirected is not None:
                    self.request_urls.append(redirected.full_url)
                raise urllib.error.HTTPError(
                    request.full_url,
                    self.status,
                    "redirect blocked",
                    {"Location": "https://evil.example/steal"},
                    None,
                )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            protein = root / "protein.pdb"
            ligand = root / "ligand.sdf"
            protein.write_text("ATOM\n", encoding="utf-8")
            ligand.write_text("ligand\n$$$$\n", encoding="utf-8")
            request = self.base_vina_request(protein, ligand)
            request["docking"] = {
                "objective": "pose-prediction",
                "engine": "diffdock-nim-hosted",
                "num_poses": 1,
            }
            request["tools"] = {
                "diffdock_hosted": {
                    "endpoint": DIFFDOCK_HOSTED_ENDPOINT,
                    "service_version": "hosted-v1",
                    "terms_url": "https://example.org/terms",
                    "license": "service-terms",
                    "auth_env": "NVIDIA_API_KEY",
                }
            }
            request["external_service"] = {
                "authorized": True,
                "credential_rotation_acknowledged": True,
                "data_classification": "public",
                "auth_env": "NVIDIA_API_KEY",
                "endpoint": DIFFDOCK_HOSTED_ENDPOINT,
            }
            for status in (301, 302, 303, 307, 308):
                with self.subTest(status=status):
                    out = root / f"redirect-{status}"
                    probes = []

                    def fake_build_opener(handler):
                        probe = RedirectProbeOpener(handler, status)
                        probes.append(probe)
                        return probe

                    with patch.dict(
                        os.environ,
                        {"NVIDIA_API_KEY": "environment-only-test-value"},
                    ):
                        manifest = prepare_package(request, "run", out)
                        manifest_path = out / "run_manifest.yaml"
                        write_yaml(manifest_path, manifest)
                        with (
                            patch(
                                "docking_workflow._open_no_redirect",
                                new=self._real_open_no_redirect,
                            ),
                            patch(
                                "docking_workflow.urllib.request.build_opener",
                                side_effect=fake_build_opener,
                            ) as builder,
                            patch(
                                "docking_workflow.urllib.request.urlopen",
                                side_effect=AssertionError(
                                    "default redirect-following urlopen used"
                                ),
                            ) as urlopen_mock,
                        ):
                            self.assertEqual(
                                execute_manifest(manifest_path, manifest), 2
                            )
                    builder.assert_called_once()
                    urlopen_mock.assert_not_called()
                    self.assertEqual(len(probes), 1)
                    self.assertIsInstance(
                        probes[0].handler,
                        docking_workflow.NoRedirectHTTPHandler,
                    )
                    self.assertEqual(
                        probes[0].request_urls, [DIFFDOCK_HOSTED_ENDPOINT]
                    )
                    self.assertEqual(
                        manifest["execution"]["status"],
                        "failed-hosted-adapter",
                    )
                    self.assertFalse(
                        any(
                            path.name == "response.json"
                            for path in out.rglob("*")
                            if path.is_file()
                        )
                    )
                    persisted = b"\n".join(
                        path.read_bytes()
                        for path in out.rglob("*")
                        if path.is_file()
                    )
                    self.assertNotIn(b"evil.example", persisted)
                    self.assertNotIn(
                        b"environment-only-test-value", persisted
                    )

    def test_hosted_response_credentials_are_rejected_before_any_raw_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            protein = root / "protein.pdb"
            ligand = root / "ligand.sdf"
            protein.write_text("ATOM\n", encoding="utf-8")
            ligand.write_text("ligand\n$$$$\n", encoding="utf-8")
            request = self.base_vina_request(protein, ligand)
            request["docking"] = {
                "objective": "pose-prediction",
                "engine": "diffdock-nim-hosted",
                "num_poses": 1,
            }
            request["tools"] = {
                "diffdock_hosted": {
                    "endpoint": DIFFDOCK_HOSTED_ENDPOINT,
                    "service_version": "hosted-v1",
                    "terms_url": "https://example.org/terms",
                    "license": "service-terms",
                    "auth_env": "NVIDIA_API_KEY",
                }
            }
            request["external_service"] = {
                "authorized": True,
                "credential_rotation_acknowledged": True,
                "data_classification": "public",
                "auth_env": "NVIDIA_API_KEY",
                "endpoint": DIFFDOCK_HOSTED_ENDPOINT,
            }
            hosted_request = build_hosted_requests(request)[0]
            fixture = {
                "status": "success",
                "ligand_positions": ["pose\n$$$$\n"],
                "position_confidence": [0.9],
                "metadata": {
                    "Authorization": "Bearer " + "RESPONSE_SECRET_MUST_NOT_PERSIST"
                },
            }
            raw_fixture = json.dumps(fixture).encode("utf-8")
            response = MagicMock()
            response.__enter__.return_value.read.return_value = raw_fixture
            response.__enter__.return_value.headers = {}
            with patch(
                "docking_workflow._open_no_redirect",
                return_value=response,
            ):
                with self.assertRaisesRegex(ValueError, "credential-like"):
                    execute_hosted_diffdock(
                        hosted_request["payload"],
                        external_service=request["external_service"],
                        environ={
                            "NVIDIA_API_KEY": "environment-only-test-value"
                        },
                    )

            clean_fixture = {
                "status": "success",
                "ligand_positions": ["pose\n$$$$\n"],
                "position_confidence": [0.9],
            }
            clean_raw_fixture = json.dumps(clean_fixture).encode("utf-8")
            response = MagicMock()
            response.__enter__.return_value.read.return_value = clean_raw_fixture
            response.__enter__.return_value.headers = {
                "X-Service-Version": "Bearer " + "HEADER_SECRET_MUST_NOT_PERSIST"
            }
            with patch(
                "docking_workflow._open_no_redirect",
                return_value=response,
            ):
                with self.assertRaisesRegex(ValueError, "credential-like"):
                    execute_hosted_diffdock(
                        hosted_request["payload"],
                        external_service=request["external_service"],
                        environ={
                            "NVIDIA_API_KEY": "environment-only-test-value"
                        },
                    )

            out = root / "hosted-secret-response"
            with patch.dict(
                os.environ, {"NVIDIA_API_KEY": "environment-only-test-value"}
            ):
                manifest = prepare_package(request, "run", out)
                manifest_path = out / "run_manifest.yaml"
                write_yaml(manifest_path, manifest)
                response = MagicMock()
                response.__enter__.return_value.read.return_value = raw_fixture
                response.__enter__.return_value.headers = {}
                with patch(
                    "docking_workflow._open_no_redirect",
                    return_value=response,
                ):
                    self.assertEqual(
                        execute_manifest(manifest_path, manifest), 2
                    )
            self.assertEqual(
                manifest["execution"]["status"], "failed-hosted-adapter"
            )
            self.assertFalse(
                any(
                    path.name == "response.json"
                    for path in out.rglob("*")
                    if path.is_file()
                )
            )
            secret = b"RESPONSE_SECRET_MUST_NOT_PERSIST"
            for path in out.rglob("*"):
                if path.is_file():
                    with self.subTest(path=path.name):
                        self.assertNotIn(secret, path.read_bytes())
            self.assertEqual(
                list(
                    csv.DictReader(
                        (out / "docking_candidates.csv").read_text(
                            encoding="utf-8"
                        ).splitlines()
                    )
                ),
                [],
            )

            header_out = root / "hosted-secret-header"
            with patch.dict(
                os.environ, {"NVIDIA_API_KEY": "environment-only-test-value"}
            ):
                header_manifest = prepare_package(
                    request, "run", header_out
                )
                header_manifest_path = header_out / "run_manifest.yaml"
                write_yaml(header_manifest_path, header_manifest)
                response = MagicMock()
                response.__enter__.return_value.read.return_value = (
                    clean_raw_fixture
                )
                response.__enter__.return_value.headers = {
                    "X-Service-Version": (
                        "Bearer " + "HEADER_SECRET_MUST_NOT_PERSIST"
                    )
                }
                with patch(
                    "docking_workflow._open_no_redirect",
                    return_value=response,
                ):
                    self.assertEqual(
                        execute_manifest(
                            header_manifest_path, header_manifest
                        ),
                        2,
                    )
            self.assertEqual(
                header_manifest["execution"]["status"],
                "failed-hosted-adapter",
            )
            self.assertFalse(
                any(
                    path.name == "response.json"
                    for path in header_out.rglob("*")
                    if path.is_file()
                )
            )
            header_secret = b"HEADER_SECRET_MUST_NOT_PERSIST"
            for path in header_out.rglob("*"):
                if path.is_file():
                    with self.subTest(header_path=path.name):
                        self.assertNotIn(header_secret, path.read_bytes())

    def test_docking_controls_evidence_status_and_terminal_reports_are_current(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            request = self.base_vina_request(receptor, ligand)
            request["docking"]["objective"] = "target-focused-screen"
            request["docking"]["validation"] = {
                "controls": ["known-active", "known-inactive"]
            }
            out = root / "controls"
            manifest = prepare_package(request, "prepare", out)
            brief = (out / "design_brief.md").read_text(encoding="utf-8")
            self.assertIn("known-active", brief)
            self.assertIn("known-inactive", brief)
            self.assertEqual(manifest["evidence_status"], "planning_only")
            invalid = copy.deepcopy(manifest)
            invalid["evidence_status"] = "current_experimental"
            self.assertFalse(audit_manifest(invalid)["passed"])


    def test_execute_manifest_rejects_prepare_mode_before_any_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            request = self.base_vina_request(receptor, ligand)
            out = root / "prepare"
            manifest = prepare_package(request, "prepare", out)
            manifest["execution"]["argv_commands"] = [
                {
                    "argv": ["tampered-program", "--out", str(root / "x.pdbqt")],
                    "argv_sha256": argv_sha256(
                        ["tampered-program", "--out", str(root / "x.pdbqt")]
                    ),
                }
            ]
            write_yaml(out / "run_manifest.yaml", manifest)
            with (
                patch("docking_workflow.subprocess.run") as run_mock,
                patch("docking_workflow._open_no_redirect") as urlopen_mock,
            ):
                result = execute_manifest(out / "run_manifest.yaml", manifest)
            self.assertEqual(result, 2)
            self.assertEqual(
                manifest["execution"]["status"], "blocked-not-run-mode"
            )
            self.assertIn(
                "Passed: `false`",
                (out / "audit_report.md").read_text(encoding="utf-8"),
            )
            run_mock.assert_not_called()
            urlopen_mock.assert_not_called()

    def test_canonical_ids_reject_trailing_dot_and_casefold_collisions(self) -> None:
        self.assertFalse(validate_stable_id("foo."))
        self.assertFalse(validate_stable_id("foo "))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            library = root / "collision.csv"
            with library.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=["ligand_id", "chemical_state_id", "file"]
                )
                writer.writeheader()
                writer.writerow(
                    {"ligand_id": "foo", "chemical_state_id": "s1", "file": str(ligand)}
                )
                writer.writerow(
                    {"ligand_id": "Foo", "chemical_state_id": "s1", "file": str(ligand)}
                )
            request = self.base_vina_request(receptor, ligand)
            request.pop("ligand")
            request["ligand_library"] = {
                "manifest_file": str(library),
                "chemical_state_strategy": "pre-enumerated",
            }
            joined = "\n".join(docking_gaps(request))
            self.assertIn("canonical-id-collision", joined)

            library.write_text(
                "ligand_id,chemical_state_id,file\n"
                f"foo,s1,{ligand}\n"
                f"foo.,s1,{ligand}\n",
                encoding="utf-8",
            )
            joined = "\n".join(docking_gaps(request))
            self.assertIn("ligand_id-unsafe", joined)

    def test_empty_semantic_candidate_row_and_unknown_engine_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "docking_candidates.csv"
            output = Path(temp) / "pose.out"
            output.write_text("pose", encoding="utf-8")
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=DOCKING_CANDIDATE_FIELDS)
                writer.writeheader()
                writer.writerow(
                    {
                        "engine": "unknown-engine",
                        "input_sha256": "a" * 64,
                        "output_path": str(output),
                        "evidence_status": "computational_prediction",
                        "experimental_status": "not-tested",
                    }
                )
            joined = "\n".join(audit_docking_candidates(path))
            for field in (
                "run_id",
                "ligand_id",
                "chemical_state_id",
                "pose_id",
                "engine",
                "engine_version",
                "receptor_state_id",
                "pose_rank_within_ligand",
                "metric_name",
                "metric_value",
                "metric_unit",
                "metric_role",
                "rank_scope",
                "raw_output_sha256",
            ):
                with self.subTest(field=field):
                    self.assertIn(field, joined)

    def test_meeko_candidate_hash_uses_prepared_ligand_actually_passed_to_vina(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor = root / "receptor.pdb"
            ligand = root / "ligand.sdf"
            prepared_receptor = root / "prepared-receptor.pdbqt"
            prepared_ligand = root / "prepared-ligand.pdbqt"
            receptor.write_text("ATOM original\n", encoding="utf-8")
            ligand.write_text("original ligand\n$$$$\n", encoding="utf-8")
            prepared_receptor.write_text("ATOM prepared\n", encoding="utf-8")
            prepared_ligand.write_text("MODEL prepared\nENDMDL\n", encoding="utf-8")
            request = self.base_vina_request(receptor, ligand)
            request["docking"]["preparation"] = {
                "use_meeko": True,
                "receptor_pdbqt": str(prepared_receptor),
            }
            request["ligand"]["prepared_file"] = str(prepared_ligand)
            commands = build_vina_argv_commands(request, root / "out")
            expected = docking_workflow._sha256_file(prepared_ligand)
            original = docking_workflow._sha256_file(ligand)
            self.assertNotEqual(expected, original)
            self.assertEqual(commands[0]["input_sha256"], expected)
            manifest = prepare_package(request, "prepare", root / "package")
            self.assertEqual(
                manifest["input_hashes"]["prepared_receptor_pdbqt"]["sha256"],
                docking_workflow._sha256_file(prepared_receptor),
            )

    def test_requested_plip_or_pymol_adapter_blocks_run_but_not_prepare(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            fake_vina = root / "vina.exe"
            fake_vina.write_text("not executed", encoding="utf-8")
            for section, key in (("analysis", "plip"), ("visualization", "pymol")):
                request = self.base_vina_request(receptor, ligand)
                request["docking"][section] = {key: True}
                request["tools"] = self.vina_tools(str(fake_vina))
                prepare_manifest = prepare_package(
                    request, "prepare", root / f"prepare-{key}"
                )
                self.assertTrue(audit_manifest(prepare_manifest)["passed"])
                run_manifest = prepare_package(
                    request, "run", root / f"run-{key}"
                )
                result = audit_manifest(run_manifest)
                self.assertFalse(result["passed"])
                self.assertIn("adapter-not-implemented", "\n".join(result["errors"]))

    def test_nonofficial_diffdock_poses_parser_is_not_public(self) -> None:
        self.assertFalse(hasattr(docking_workflow, "diffdock_response_rows"))

    def test_secret_prepare_manifest_is_rejected_before_state_or_disk_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            request = self.base_vina_request(receptor, ligand)
            out = root / "prepare"
            manifest = prepare_package(request, "prepare", out)
            manifest["nested"] = {
                "Authorization": "Bearer " + "opaque-test-secret",
                "api_key": "opaque-test-secret",
            }
            manifest_path = out / "run_manifest.yaml"
            write_yaml(manifest_path, manifest)
            before = docking_workflow._sha256_file(manifest_path)
            before_status = manifest["execution"]["status"]
            with (
                patch("docking_workflow.subprocess.run") as run_mock,
                patch("docking_workflow._open_no_redirect") as urlopen_mock,
            ):
                result = execute_manifest(manifest_path, manifest)
            self.assertEqual(result, 2)
            self.assertEqual(before, docking_workflow._sha256_file(manifest_path))
            self.assertEqual(manifest["execution"]["status"], before_status)
            run_mock.assert_not_called()
            urlopen_mock.assert_not_called()

    def test_completed_vina_candidates_are_exactly_rebuilt_from_raw_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            fake_vina = root / "vina.exe"
            fake_vina.write_text("not executed", encoding="utf-8")
            request = self.base_vina_request(receptor, ligand)
            request["docking"]["seeds"] = [7]
            request["docking"]["num_poses"] = 2
            request["tools"] = self.vina_tools(str(fake_vina))
            out = root / "run"
            manifest = prepare_package(request, "run", out)
            manifest_path = out / "run_manifest.yaml"
            write_yaml(manifest_path, manifest)

            def fake_run(argv, **kwargs):
                output = Path(argv[argv.index("--out") + 1])
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(
                    "MODEL 1\nREMARK VINA RESULT: -8.2 0.0 0.0\nENDMDL\n"
                    "MODEL 2\nREMARK VINA RESULT: -7.4 1.0 2.0\nENDMDL\n",
                    encoding="utf-8",
                )
                completed = MagicMock()
                completed.returncode = 0
                completed.stdout = ""
                completed.stderr = ""
                return completed

            with patch("docking_workflow.subprocess.run", side_effect=fake_run):
                self.assertEqual(execute_manifest(manifest_path, manifest), 0)
            candidates = out / "docking_candidates.csv"
            baseline_text = candidates.read_text(encoding="utf-8")
            self.assertIn(
                "completed-computational-only",
                (out / "docking_report.md").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "Passed: `true`",
                (out / "audit_report.md").read_text(encoding="utf-8"),
            )
            self.assertTrue(
                audit_manifest(manifest, candidates, package_root=out)["passed"]
            )
            raw_output = Path(
                list(csv.DictReader(baseline_text.splitlines()))[0]["output_path"]
            )
            raw_baseline = raw_output.read_bytes()
            result_anchor = manifest["execution"]["results"][0]
            self.assertEqual(
                result_anchor["output_sha256"],
                docking_workflow._sha256_file(raw_output),
            )
            baseline_rows = list(csv.DictReader(baseline_text.splitlines()))
            self.assertTrue(
                all(
                    row["chemical_state_id"] in row["pose_id"]
                    for row in baseline_rows
                )
            )
            self.assertTrue(
                all(
                    row["raw_output_sha256"] == result_anchor["output_sha256"]
                    for row in baseline_rows
                )
            )

            def mutate(field, value):
                rows = list(csv.DictReader(baseline_text.splitlines()))
                rows[0][field] = value
                return rows

            mutations = {
                "input_sha256": mutate("input_sha256", "b" * 64),
                "engine_version": mutate("engine_version", "tampered-version"),
                "receptor_state_id": mutate("receptor_state_id", "tampered-receptor"),
                "metric_value": mutate("metric_value", "-99.0"),
                "delete_row": list(csv.DictReader(baseline_text.splitlines()))[:1],
            }
            added = list(csv.DictReader(baseline_text.splitlines()))
            extra = dict(added[-1])
            extra["pose_id"] = "extra-pose"
            extra["pose_rank_within_ligand"] = "3"
            added.append(extra)
            mutations["add_row"] = added

            for name, rows in mutations.items():
                with self.subTest(name=name):
                    with candidates.open("w", encoding="utf-8", newline="") as handle:
                        writer = csv.DictWriter(
                            handle, fieldnames=DOCKING_CANDIDATE_FIELDS
                        )
                        writer.writeheader()
                        writer.writerows(rows)
                    result = audit_manifest(
                        manifest, candidates, package_root=out
                    )
                    self.assertFalse(result["passed"])
                    self.assertIn(
                        "provenance", "\n".join(result["errors"]).casefold()
                    )
            candidates.write_text(baseline_text, encoding="utf-8")
            missing_results = copy.deepcopy(manifest)
            missing_results["execution"]["results"] = []
            missing_result_audit = audit_manifest(
                missing_results, candidates, package_root=out
            )
            self.assertFalse(missing_result_audit["passed"])
            self.assertIn(
                "provenance",
                "\n".join(missing_result_audit["errors"]).casefold(),
            )
            failed_result = copy.deepcopy(manifest)
            failed_result["execution"]["results"][0]["returncode"] = 99
            failed_result_audit = audit_manifest(
                failed_result, candidates, package_root=out
            )
            self.assertFalse(failed_result_audit["passed"])
            self.assertIn(
                "provenance",
                "\n".join(failed_result_audit["errors"]).casefold(),
            )
            missing_anchor = copy.deepcopy(manifest)
            missing_anchor["execution"]["results"][0].pop("output_sha256")
            self.assertFalse(
                audit_manifest(
                    missing_anchor, candidates, package_root=out
                )["passed"]
            )
            raw_output.write_text(
                "MODEL 1\nREMARK VINA RESULT: -1.0 0.0 0.0\nENDMDL\n"
                "MODEL 2\nREMARK VINA RESULT: -2.0 1.0 2.0\nENDMDL\n",
                encoding="utf-8",
            )
            synchronized_rows = copy.deepcopy(baseline_rows)
            synchronized_hash = docking_workflow._sha256_file(raw_output)
            for row, score in zip(synchronized_rows, ("-1.0", "-2.0")):
                row["metric_value"] = score
                row["raw_output_sha256"] = synchronized_hash
            with candidates.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=DOCKING_CANDIDATE_FIELDS
                )
                writer.writeheader()
                writer.writerows(synchronized_rows)
            self.assertFalse(
                audit_manifest(manifest, candidates, package_root=out)["passed"]
            )
            raw_output.write_bytes(raw_baseline)
            candidates.write_text(baseline_text, encoding="utf-8")
            raw_output.write_bytes(b"\xff\xfeBINARY_VINA_SECRET")
            corrupted = audit_manifest(manifest, candidates, package_root=out)
            self.assertFalse(corrupted["passed"])
            self.assertNotIn(
                "BINARY_VINA_SECRET", "\n".join(corrupted["errors"])
            )
            raw_output.unlink()
            missing = audit_manifest(manifest, candidates, package_root=out)
            self.assertFalse(missing["passed"])

    def test_completed_hosted_candidates_rebuild_from_official_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            protein = root / "protein.pdb"
            ligand = root / "ligand.sdf"
            protein.write_text("ATOM hosted\n", encoding="utf-8")
            ligand.write_text("hosted ligand\n$$$$\n", encoding="utf-8")
            request = self.base_vina_request(protein, ligand)
            request["docking"] = {
                "objective": "pose-prediction",
                "engine": "diffdock-nim-hosted",
                "num_poses": 2,
                "time_divisions": 10,
                "steps": 18,
                "save_trajectory": False,
                "skip_gen_conformer": False,
                "is_staged": False,
            }
            request["tools"] = {
                "diffdock_hosted": {
                    "endpoint": DIFFDOCK_HOSTED_ENDPOINT,
                    "service_version": "hosted-v1",
                    "terms_url": "https://example.org/terms",
                    "license": "service-terms",
                    "auth_env": "NVIDIA_API_KEY",
                }
            }
            request["external_service"] = {
                "authorized": True,
                "credential_rotation_acknowledged": True,
                "data_classification": "public",
                "auth_env": "NVIDIA_API_KEY",
                "endpoint": DIFFDOCK_HOSTED_ENDPOINT,
            }
            fixture = {
                "status": "success",
                "details": "",
                "protein": protein.read_text(encoding="utf-8"),
                "ligand": ligand.read_text(encoding="utf-8"),
                "ligand_positions": ["pose one\n$$$$\n", "pose two\n$$$$\n"],
                "position_confidence": [0.91, 0.82],
                "trajectory": None,
            }
            response = MagicMock()
            raw_fixture_bytes = json.dumps(fixture).encode("utf-8")
            response.__enter__.return_value.read.return_value = raw_fixture_bytes
            response.__enter__.return_value.headers = {}
            out = root / "hosted-run"
            with patch.dict(os.environ, {"NVIDIA_API_KEY": "environment-test-value"}):
                manifest = prepare_package(request, "run", out)
                manifest_path = out / "run_manifest.yaml"
                write_yaml(manifest_path, manifest)
                with patch(
                    "docking_workflow._open_no_redirect",
                    return_value=response,
                ):
                    self.assertEqual(execute_manifest(manifest_path, manifest), 0)
                candidates = out / "docking_candidates.csv"
                baseline_text = candidates.read_text(encoding="utf-8")
                self.assertTrue(
                    audit_manifest(manifest, candidates, package_root=out)["passed"]
                )
                self.assertRegex(
                    manifest["execution"]["results"][0].get("response_sha256", ""),
                    r"^[0-9a-f]{64}$",
                )

                rows = list(csv.DictReader(baseline_text.splitlines()))
                result_record = manifest["execution"]["results"][0]
                response_path = Path(result_record["response_path"])
                self.assertEqual(response_path.read_bytes(), raw_fixture_bytes)
                self.assertEqual(
                    result_record["expected_service_version"], "hosted-v1"
                )
                self.assertEqual(
                    result_record["observed_service_version"], "unreported"
                )
                self.assertEqual(len(result_record["pose_outputs"]), 2)
                for row, anchor in zip(rows, result_record["pose_outputs"]):
                    self.assertEqual(row["raw_output_sha256"], anchor["sha256"])
                    self.assertEqual(
                        anchor["sha256"],
                        docking_workflow._sha256_file(Path(anchor["path"])),
                    )
                missing_pose_anchor = copy.deepcopy(manifest)
                missing_pose_anchor["execution"]["results"][0].pop("pose_outputs")
                self.assertFalse(
                    audit_manifest(
                        missing_pose_anchor, candidates, package_root=out
                    )["passed"]
                )
                tampered_rows = copy.deepcopy(rows)
                tampered_rows[0]["metric_value"] = "0.01"
                with candidates.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(
                        handle, fieldnames=DOCKING_CANDIDATE_FIELDS
                    )
                    writer.writeheader()
                    writer.writerows(tampered_rows)
                self.assertFalse(
                    audit_manifest(manifest, candidates, package_root=out)["passed"]
                )
                candidates.write_text(baseline_text, encoding="utf-8")
                pose_path = Path(rows[0]["output_path"])
                pose_baseline = pose_path.read_bytes()
                pose_path.write_text("changed pose\n$$$$\n", encoding="utf-8")
                synchronized_rows = copy.deepcopy(rows)
                synchronized_rows[0][
                    "raw_output_sha256"
                ] = docking_workflow._sha256_file(pose_path)
                with candidates.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(
                        handle, fieldnames=DOCKING_CANDIDATE_FIELDS
                    )
                    writer.writeheader()
                    writer.writerows(synchronized_rows)
                self.assertFalse(
                    audit_manifest(manifest, candidates, package_root=out)["passed"]
                )
                pose_path.write_bytes(pose_baseline)
                candidates.write_text(baseline_text, encoding="utf-8")
                changed_fixture = copy.deepcopy(fixture)
                changed_fixture["position_confidence"][0] = 0.01
                response_path.write_bytes(json.dumps(changed_fixture).encode("utf-8"))
                synchronized_rows = copy.deepcopy(rows)
                synchronized_rows[0]["metric_value"] = "0.01"
                with candidates.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(
                        handle, fieldnames=DOCKING_CANDIDATE_FIELDS
                    )
                    writer.writeheader()
                    writer.writerows(synchronized_rows)
                self.assertFalse(
                    audit_manifest(manifest, candidates, package_root=out)["passed"]
                )
                response_path.write_bytes(raw_fixture_bytes)
                candidates.write_text(baseline_text, encoding="utf-8")
                pose_path.write_bytes(b"\xff\xfeBINARY_POSE_SECRET")
                corrupted_pose = audit_manifest(
                    manifest, candidates, package_root=out
                )
                self.assertFalse(corrupted_pose["passed"])
                self.assertNotIn(
                    "BINARY_POSE_SECRET", "\n".join(corrupted_pose["errors"])
                )
                pose_path.write_text(
                    fixture["ligand_positions"][0], encoding="utf-8", newline="\n"
                )
                response_path.write_bytes(b"\xff\xfeBINARY_RESPONSE_SECRET")
                corrupt_response_manifest = copy.deepcopy(manifest)
                corrupt_response_manifest["execution"]["results"][0][
                    "response_sha256"
                ] = docking_workflow._sha256_file(response_path)
                corrupted_response = audit_manifest(
                    corrupt_response_manifest, candidates, package_root=out
                )
                self.assertFalse(corrupted_response["passed"])
                self.assertNotIn(
                    "BINARY_RESPONSE_SECRET",
                    "\n".join(corrupted_response["errors"]),
                )
                response_path.write_bytes(raw_fixture_bytes)
                candidates.write_text(baseline_text, encoding="utf-8")
                tampered_manifest = copy.deepcopy(manifest)
                tampered_manifest["execution"]["results"][0][
                    "response_path"
                ] = str(root / "outside.json")
                self.assertFalse(
                    audit_manifest(
                        tampered_manifest, candidates, package_root=out
                    )["passed"]
                )
                tampered_manifest = copy.deepcopy(manifest)
                tampered_manifest["execution"]["results"][0][
                    "response_sha256"
                ] = "b" * 64
                self.assertFalse(
                    audit_manifest(
                        tampered_manifest, candidates, package_root=out
                    )["passed"]
                )
                tampered_rows = copy.deepcopy(rows)
                tampered_rows[0]["input_sha256"] = "b" * 64
                with candidates.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(
                        handle, fieldnames=DOCKING_CANDIDATE_FIELDS
                    )
                    writer.writeheader()
                    writer.writerows(tampered_rows)
                self.assertFalse(
                    audit_manifest(manifest, candidates, package_root=out)["passed"]
                )

    def test_binary_candidate_csv_returns_failed_audit_and_cli_exit_two(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receptor, ligand = self.make_files(root)
            request = self.base_vina_request(receptor, ligand)
            out = root / "package"
            manifest = prepare_package(request, "prepare", out)
            candidate_path = out / "docking_candidates.csv"
            candidate_path.write_bytes(b"\xff\xfeBINARY_CANDIDATE_SECRET")
            result = audit_manifest(manifest, candidate_path, package_root=out)
            self.assertFalse(result["passed"])
            self.assertNotIn(
                "BINARY_CANDIDATE_SECRET", "\n".join(result["errors"])
            )
            output = io.StringIO()
            argv = [
                "baker_design.py",
                "audit",
                "--manifest",
                str(out / "run_manifest.yaml"),
            ]
            with patch.object(sys, "argv", argv), redirect_stdout(output):
                returncode = baker_design.main()
            self.assertEqual(returncode, 2)
            report = (out / "audit_report.md").read_text(encoding="utf-8")
            self.assertNotIn("BINARY_CANDIDATE_SECRET", report)
            self.assertNotIn("BINARY_CANDIDATE_SECRET", output.getvalue())

if __name__ == "__main__":
    unittest.main()
