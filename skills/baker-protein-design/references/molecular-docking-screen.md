# Molecular docking and screening

Use this route for pose prediction, co-crystal redocking, or target-focused screening against an existing receptor.

## Engine choice

| Need | Choose | Boundary |
|---|---|---|
| Explicit pocket/grid, PDBQT inputs, reproducible seeds, protocol redocking | AutoDock Vina | Requires externally prepared PDBQT; score is protocol-specific |
| Pose generation without a Vina grid and authorized hosted upload | Hosted DiffDock NIM | Public/non-sensitive only; confidence is within-ligand |
| Private/local deployment already installed and licensed | Self-hosted DiffDock NIM | Currently `prepare-only`; execution adapter not implemented |

Do not choose by convenience alone. Prefer Vina when a defensible binding-site box and redocking control exist. Prefer DiffDock for pose hypotheses when no validated grid is available, but do not use confidence as cross-ligand affinity ranking.

## Input schema

Accept only string schema versions `1.0` and `1.1`; omission defaults to `1.1`. Strict execution rejects any other version, placeholder values, wrong types, non-finite numeric values (`NaN`/`Inf`), empty ligand-library CSVs, and non-lowercase objective/engine enums or IDs.

Require:

- `route: molecular-docking-screen`;
- `target.structure_file`, assembly/biological state, chains, mutation state, receptor-state ID;
- receptor preparation: protonation, missing residues, alternate locations, explicit HETATM retain/remove policy;
- exactly one of `ligand` or `ligand_library`;
- stable lowercase `ligand_id` and `chemical_state_id`, file, protonation/tautomer/stereo/covalent-state strategy;
- `docking.objective`, engine, pose count, seeds where applicable, controls, analysis/visualization flags;
- Vina binding-site source, center, size, exhaustiveness;
- tools with actual paths/versions/licenses;
- backend and output root;
- hosted-service authorization fields when applicable.

Use `assets/docking_request.example.yaml`. Batch libraries use `assets/docking_batch_template.csv`.

The docking request example is deliberately a **prepare-only scaffold**. Its `planning_only` / `REPLACE` values must surface as planning gaps; this is expected and is not a validation failure for the asset. Never substitute plausible-looking values merely to clear the gap list. Strict `run` must remain blocked until each placeholder is replaced by a real local file, hash, protocol decision, installed version, or authorized path and the package is rebuilt.

## Receptor and ligand decisions

Confirm assembly before chain extraction. Record chain IDs, biological state, constructs, mutations, cofactors, bound ions, waters, missing residues, altloc selection, protonation, and each HETATM class retained or removed. `remove_all` is not an acceptable policy.

IDs must be stable, lowercase, path-safe, and independent of row order. One chemical state gets one ID. Do not combine unspecified protonation, tautomer, stereoisomer, or covalent states under one result.

## Grid, seeds, and controls

Record the grid source as co-crystal ligand, experimentally supported residues, or another explicit traceable definition. `[0, 0, 0]` is not a missing-value sentinel; it is valid only when the recorded derivation produces those coordinates.

Declare seed(s), pose count, and redocking reference/mapping. Strict redocking requires `reference_pose` as an existing local file, `reference_pose_sha256`, `atom_mapping: {file, sha256}`, `symmetry_handling`, `receptor_alignment`, `heavy_atom_rule`, `rmsd_tool`, `rmsd_tool_version`, `pose_selection`, and a positive integer `top_n`. For a screen, include known-active/decoy or other task-relevant controls. Redocking tests protocol pose recovery; it does not establish biological affinity.

## Execution boundaries

- Vina execution accepts reviewed `argv` arrays only and uses `shell=false`.
- Meeko means PDBQT was processed externally. This workflow does not install or run Meeko.
- Hosted DiffDock requires explicit upload authorization, `public` or `non-sensitive` classification, endpoint `https://health.api.nvidia.com/v1/molecular-docking/diffdock/generate`, and a newly provided environment variable named `NVIDIA_API_KEY`.
- Never place the key in a manifest, command, candidate table, response file, or log. Rotate any credential suspected of prior exposure.
- Self-hosted DiffDock is `prepare-only`; PLIP and PyMOL are `adapter-not-implemented`. Requesting them must block strict `run`.
- Do not auto-install, accept terms, download weights, or execute arbitrary shell.

## Outputs

Create `run_manifest.yaml`, `commands.ps1`, `docking_candidates.csv`, `docking_report.md`, and `audit_report.md` when auditing or attempting a run. Keep raw response/pose files and hashes under the declared output root.

For completed Vina commands, retain `output_path`, `output_sha256`, `output_bytes`, and parsed `pose_count`. For hosted DiffDock, retain raw `response_path`, `response_sha256`, `response_bytes`, input hash, pose count, configured `expected_service_version`, separately observed service version, and only the observed header allowlist: `x-nim-version`, `x-nvidia-service-version`, `x-service-version`. Each materialized pose anchor retains `pose_id`, path, SHA-256, and bytes. Candidate `raw_output_sha256` points to the Vina raw PDBQT or hosted per-pose SDF as appropriate.

Every terminal docking outcome—blocked, failed, or completed—must regenerate `audit_report.md` and `docking_report.md`.

## Run-manifest shape

Use `assets/run_manifest.template.yaml` as a generated-manifest shape, not as a request. The current manifest stores receptor state and any single ligand under `parameters.target` and `parameters.ligand`; `receptor_state_id` belongs inside `parameters.target`. A library uses `manifest_file`, `sha256`, and `chemical_state_strategy` under both `parameters.ligand_library` and the generated top-level `ligand_library` mirror. The generated top-level `docking`, `ligand_library`, and `external_service` values mirror their request counterparts; do not invent separate values.

`execution.results` starts as an empty list and is populated only during execution. Do not add alternate result-schema objects to the manifest. Reader-facing `audit_report.md` is refreshed on terminal states even though it is not a key in the current `outputs` mapping.
