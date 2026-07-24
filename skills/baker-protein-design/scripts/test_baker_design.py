from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from baker_design import (
    CANDIDATE_FIELDS,
    ROUTE_TOOLS,
    audit_candidates,
    audit_manifest,
    prepare_package,
    probe_environment,
    render_commands,
    route_request,
)


VALID_SHA = "a" * 64


def configured_tools(route: str) -> dict:
    result = {}
    for name in ROUTE_TOOLS[route]:
        result[name] = {
            "repository": f"https://example.org/{name}",
            "repo_path": f"/opt/{name}",
            "commit": "0123456789abcdef",
            "license": "test-license",
            "checkpoint": f"/models/{name}.pt",
            "checkpoint_sha256": VALID_SHA,
            "command_template": f"echo {name} {{seed}} {{output_dir}}",
        }
    return result


class BakerDesignTests(unittest.TestCase):
    def base_request(self, target_file: Path) -> dict:
        return {
            "design_goal": "Monovalent antagonist for a flat receptor surface",
            "mechanism": "antagonism",
            "target": {
                "type": "folded-protein",
                "structure_file": str(target_file),
                "chains": ["A"],
                "hotspots": ["A:29"],
            },
            "negative_targets": [{"name": "paralog"}],
            "function": {"valency": "monovalent", "readouts": ["binding", "antagonism"]},
            "controls": ["family_cross_screen"],
            "backend": {"kind": "linux-gpu", "gpu_vram_gb": 24},
            "reproducibility": {"seed": 7, "candidate_count": 100},
            "filters": [
                {
                    "name": "interaction_pae",
                    "stage": "complex_prediction",
                    "source_doi": "10.1126/science.adp1779",
                    "source_locator": "Methods > computational filtering",
                    "operator": "<",
                    "value": 7.5,
                }
            ],
        }

    def test_flat_ppi_routes_to_folded_target(self) -> None:
        request = {"design_goal": "flat PPI binder", "target": {"type": "folded-protein"}}
        self.assertEqual(route_request(request), "folded-target-binder")

    def test_idr_route(self) -> None:
        request = {"design_goal": "bind an IDR", "target": {"type": "intrinsically-disordered-region"}}
        self.assertEqual(route_request(request), "peptide-idr-binder")

    def test_small_molecule_route_and_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            request = {
                "design_goal": "design a small-molecule pocket",
                "target": {"type": "small-molecule-binder", "structure_file": "/data/t.pdb"},
                "ligand": {"file": "/data/l.sdf", "chemical_state": "declared"},
                "backend": {"kind": "remote"},
                "reproducibility": {"seed": 4, "candidate_count": 20},
            }
            route = route_request(request)
            request["tools"] = configured_tools(route)
            commands, problems = render_commands(request, route, out, out / "run_manifest.yaml")
            self.assertEqual(route, "small-molecule-enzyme")
            self.assertFalse(problems)
            self.assertTrue(any("ligandmpnn" in command for command in commands))
            self.assertTrue(any("placer" in command for command in commands))

    def test_explicit_docking_route_has_priority_over_ligand(self) -> None:
        request = {
            "route": "molecular-docking-screen",
            "design_goal": "design a ligand-aware enzyme",
            "ligand": {"file": "ligand.sdf", "chemical_state": "declared"},
        }
        self.assertEqual(route_request(request), "molecular-docking-screen")

    def test_docking_markers_have_priority_over_generic_ligand_routing(self) -> None:
        requests = [
            {"workflow": {"kind": "molecular-docking"}, "ligand": {"file": "l.sdf"}},
            {"docking": {"engine": "autodock-vina"}, "ligand": {"file": "l.sdf"}},
            {"design_goal": "redocking a known ligand", "ligand": {"file": "l.sdf"}},
            {"design_goal": "virtual screening campaign", "ligand": {"file": "l.sdf"}},
            {"design_goal": "run docking poses", "ligand": {"file": "l.sdf"}},
            {"design_goal": "分子对接", "ligand": {"file": "l.sdf"}},
            {"docking": {}, "ligand": {"file": "l.sdf"}},
        ]
        for request in requests:
            with self.subTest(request=request):
                self.assertEqual(route_request(request), "molecular-docking-screen")

    def test_ligand_constrained_protein_design_is_not_misrouted_to_docking(self) -> None:
        request = {
            "design_goal": "design a ligand constrained enzyme active site",
            "target": {"type": "small-molecule-enzyme"},
            "ligand": {"file": "ligand.sdf", "chemical_state": "declared"},
        }
        self.assertEqual(route_request(request), "small-molecule-enzyme")

    def test_eight_gb_backend_downgrades(self) -> None:
        result = probe_environment(
            {"backend": {"kind": "local", "gpu_vram_gb": 8}}, strict_run=True
        )
        self.assertFalse(result["ready_for_local_execution"])
        self.assertTrue(any("downgrade" in issue for issue in result["issues"]))

    def test_missing_seed_and_checkpoint_block_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "target.pdb"
            target.write_text("ATOM\n", encoding="utf-8")
            request = self.base_request(target)
            request["reproducibility"].pop("seed")
            request["tools"] = configured_tools("folded-target-binder")
            request["tools"]["rfdiffusion"]["checkpoint_sha256"] = ""
            manifest = prepare_package(request, "prepare", Path(temp) / "out")
            result = audit_manifest(manifest)
            self.assertFalse(result["passed"])
            joined = "\n".join(result["errors"])
            self.assertIn("Missing random_seed", joined)
            self.assertIn("checkpoint_sha256", joined)

    def test_prediction_cannot_claim_experimental_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "candidates.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=CANDIDATE_FIELDS)
                writer.writeheader()
                writer.writerow(
                    {
                        "candidate_id": "c1",
                        "stage": "prediction",
                        "source_tool": "AF2",
                        "seed": 1,
                        "status": "validated binder",
                        "prediction_only": "true",
                        "evidence_status": "computational_prediction",
                        "experimental_status": "validated",
                    }
                )
            errors = audit_candidates(path)
            self.assertGreaterEqual(len(errors), 2)

    def test_minimal_real_schema_1_0_manifest_is_accepted(self) -> None:
        tools = configured_tools("folded-target-binder")
        manifest = {
            "schema_version": "1.0",
            "mode": "prepare",
            "route": "folded-target-binder",
            "evidence_status": "planning_only",
            "input_hashes": {},
            "tools": tools,
            "parameters": {
                "target": {"type": "folded-protein", "chains": ["A"]},
                "negative_targets": [],
                "function": {},
            },
            "random_seed": 7,
            "candidate_count": 1,
            "filters": [],
            "preflight": {
                "ready_for_local_execution": True,
                "issues": [],
            },
            "execution": {
                "status": "not-started",
                "command_problems": [],
                "commands": [
                    "echo rfdiffusion",
                    "echo proteinmpnn",
                    "echo structure_predictor",
                ],
                "argv_commands": [],
                "results": [],
            },
        }
        self.assertTrue(audit_manifest(manifest)["passed"])


if __name__ == "__main__":
    unittest.main()
