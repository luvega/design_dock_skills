# Docking protocol QC

## Preflight

- [ ] Explicit receptor source and SHA-256 recorded.
- [ ] Assembly, chain(s), biological state, construct, and mutations confirmed.
- [ ] Protonation method/pH, missing residues, altloc policy, waters, ions, cofactors, and other HETATM decisions recorded.
- [ ] Ligand and chemical-state IDs are stable lowercase values; protonation, tautomer, stereo, and covalent state are explicit.
- [ ] Objective is `pose-prediction`, `redocking`, or `target-focused-screen`.
- [ ] Engine/version/license and actual executable/container/service version recorded.
- [ ] Grid source and dimensions recorded for Vina; all zeros are source-derived values, not placeholders.
- [ ] Seeds, pose count, exhaustiveness/steps, and task-relevant controls recorded.
- [ ] Output paths remain inside the declared package root.
- [ ] Schema is absent (defaults to `1.1`) or exactly string `1.0`/`1.1`.
- [ ] Strict input contains no placeholders, wrong types, non-finite numbers, empty library, or non-lowercase enums/IDs.

For `assets/docking_request.example.yaml`, unresolved planning gaps are mandatory evidence that the scaffold remains non-executable. A strict preflight that does not block while any `planning_only` / `REPLACE` value remains is a failure; do not silence it with invented values.

## Redocking

Define `reference_pose` as a local file with `reference_pose_sha256`; define `atom_mapping` as `{file, sha256}`; then record `symmetry_handling`, `receptor_alignment`, `heavy_atom_rule`, `rmsd_tool`, `rmsd_tool_version`, `pose_selection`, and positive integer `top_n`. Report top-1/top-N recovery only within that declared protocol. Do not treat a recovered pose or low Vina score as measured affinity.

## Screening

Include positive/negative controls or justify their absence. Preserve one result identity per receptor state, ligand state, seed, and pose. Vina scores may rank poses within a fixed protocol but require additional validation before cross-ligand prioritization. DiffDock confidence is strictly within-ligand pose confidence and cannot rank hits across ligands.

## Interaction annotation

PLIP contacts are geometry annotations of a predicted pose. Record PLIP version, parameters, receptor/ligand protonation, and pose hash. Do not call a PLIP contact experimentally validated. The current PLIP adapter is not implemented.

PyMOL is visualization only. Verify the installed distribution/license on site. The current PyMOL adapter is not implemented.

## Hosted DiffDock

- [ ] Upload explicitly authorized.
- [ ] Inputs classified `public` or `non-sensitive`.
- [ ] Endpoint equals `https://health.api.nvidia.com/v1/molecular-docking/diffdock/generate`.
- [ ] `NVIDIA_API_KEY` is newly supplied in the environment and never persisted.
- [ ] Raw response path, bytes, SHA-256, request input hash, and pose count retained.
- [ ] Configured `expected_service_version` is distinct from `observed_service_version`.
- [ ] `observed_headers` contains only `x-nim-version`, `x-nvidia-service-version`, or `x-service-version`.
- [ ] Every per-pose record retains pose ID, path, SHA-256, and bytes.
- [ ] Each ligand is submitted and interpreted separately.

## Audit language

Allowed: `computational_prediction`, `protocol pose recovery`, `within-ligand pose rank`, `predicted interaction geometry`.

Blocked without experiments: `validated binder`, `affinity`, `free energy`, `active`, `selective`, `inhibitor`, `hit`, or `lead`.

## Terminal provenance

For each successful Vina command retain `output_path`, `output_sha256`, `output_bytes`, and `pose_count`; copy the raw PDBQT hash into each corresponding candidate's `raw_output_sha256`. For hosted DiffDock, keep the raw response anchor separately and set each candidate's `raw_output_sha256` to its materialized pose SDF hash. Any blocked, failed, or completed terminal state must refresh both `audit_report.md` and `docking_report.md`.

Audit the current manifest shape directly: receptor and single-ligand inputs live under `parameters.target` / `parameters.ligand`; `receptor_state_id` is a target field; library digest key is `sha256`, never `manifest_sha256`; runtime anchors live only in `execution.results`.
