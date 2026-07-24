---
name: baker-protein-design
description: Route, plan, prepare, run, and audit protein design and molecular docking workflows, including Baker-lab-style binder or enzyme design, AutoDock Vina or DiffDock pose prediction, redocking, target-focused batch screening, and evidence-tracked candidate review. Use when Codex needs an auditable protein-design brief, receptor-ligand docking protocol, Vina/DiffDock choice, batch screening package, exact reviewed commands, compute-aware routing, provenance, or prediction-versus-experiment audit.
---

# Baker Protein Design and Docking

Build auditable workflows while keeping generation, scoring, prediction, and experiment separate.

## Workflow

1. Select `route`, `plan`, `prepare`, `run`, or `audit`; default to `plan`.
2. Read `references/task-routing.md` and its selected route reference.
3. Always read `references/evidence-model.md`. For `prepare`, `run`, or `audit`, also read `references/filters-validation.md`.
4. For `molecular-docking-screen`, read both `references/molecular-docking-screen.md` and `references/docking-protocol-qc.md`.
5. When the request involves CC migration sources or a migration-safety audit, read `references/cc-to-codex-migration.md`.
6. Stop for any missing choice that changes biological state, chemical state, mechanism, protocol validity, or external-service authorization. Otherwise mark the field `unresolved` and remain in planning mode.
7. Use `scripts/baker_design.py` for protein-design packages and its docking route for docking packages. Use `scripts/structure_resolver.py` only to resolve explicit RCSB or UniProt identifiers; it is offline by default and never selects a structure.

## Modes

- `route`: classify the task and list minimum biological, chemical, compute, and authorization inputs.
- `plan`: produce an evidence-linked staged workflow without claiming executability.
- `prepare`: hash local inputs and write manifests plus reviewed commands; do not install, download weights, or contact hosted services.
- `run`: proceed only after strict preflight and an explicit execution request.
- `audit`: verify hashes, versions, licenses, seed, source-linked filters, candidate provenance, and claim boundaries.

`run` may execute only a schema-valid, preflight-approved `argv` list with `shell=false`. Never interpret arbitrary shell text, use `Invoke-Expression`, persist secrets, auto-install software, accept licenses, fetch gated weights, or silently submit remote work. Hosted DiffDock requires explicit upload authorization, public/non-sensitive inputs, the current approved endpoint, and a newly supplied `NVIDIA_API_KEY` environment variable.

## Route boundary

- `small-molecule-enzyme` designs a **new protein** around a ligand, substrate, catalytic geometry, metal, or covalent context.
- `molecular-docking-screen` predicts or evaluates poses for an **existing receptor and ligand** and may perform redocking or target-focused screening. It does not create a new protein.

Do not substitute docking for Baker generative design. A project may use docking to validate an existing pocket and then separately route a new-protein design request.

## Input contract

For protein design, record goal, mechanism, target state and chains, motif or hotspots, ligand state, constraints, negatives, readouts, controls, backend, repository commit, license, checkpoint hash, seed, candidate count, filters, and output root. Use `assets/design_request.example.yaml`.

For docking, accept only string schema versions `1.0` and `1.1`; a missing version defaults to `1.1`. Record receptor assembly/chains/state, mutations, protonation, missing residues, altloc and HETATM decisions; stable lowercase ligand and chemical-state IDs; engine, explicit site provenance, seeds, controls, preparation boundary, tool versions, and external-service authorization. Use `assets/docking_request.example.yaml` and `assets/docking_batch_template.csv`.

Strict `run` rejects placeholders, wrong types, non-finite numbers (`NaN`/`Inf`), empty ligand libraries, and non-lowercase objective/engine enums or stable IDs. A redocking run also requires a local reference pose plus hash, atom mapping artifact plus hash, symmetry handling, receptor alignment, heavy-atom rule, RMSD tool/version, `pose_selection`, and positive `top_n`.

Never infer receptor assembly, agonism versus antagonism, ligand chemical state, mutations, or a grid center. `[0, 0, 0]` is valid only when an explicit documented source yields those coordinates.

## Required outputs

Produce the applicable files:

- `design_brief.md`
- `target_manifest.yaml`
- `run_manifest.yaml`
- `commands.sh` for protein-design backends
- `commands.ps1` for docking command review
- `candidates.csv` for protein design
- `docking_candidates.csv` for docking
- `docking_report.md` for docking
- `audit_report.md` for an audit or attempted run

The run manifest must retain schema version, input hashes, repositories and commits, versions and licenses, checkpoint hashes, parameters, seed, candidate scale, source-linked filters, backend, external-service boundary, argv digests, raw-output hashes/byte counts, output paths, and `evidence_status`. Every terminal docking state must refresh `audit_report.md` and `docking_report.md`.

## Evidence and safety

- Use only the evidence labels in `references/evidence-model.md`.
- Treat AF2/RFAA confidence, PAE, RMSD, pLDDT, CMS, BSA, Vina scores, DiffDock confidence, PLIP contacts, and MPNN likelihood as computational or structural evidence.
- Do not claim affinity, activity, catalysis, signaling, selectivity, or experimental validation without assay provenance. Prediction-only candidate fields must not use positive claims such as `affinity`, `selective`, `inhibitor`, `hit`, or `lead`.
- Bind numerical recipes to their exact task and stage; never turn a paper-specific cutoff into a universal default.
- Read `references/tool-registry.md` before execution. Repository licenses, model weights, hosted-service terms, databases, and installed PyMOL distributions are separate boundaries.
- Read `references/casebook.md` before reusing a worked example.

Stop `run` when any required input, hash, seed, executable/version, license, endpoint authorization, output boundary, or backend permission is missing.
