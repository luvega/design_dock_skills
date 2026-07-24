# Evidence and provenance model

## Evidence labels

- `planning_only`: proposed work, not run.
- `paper_reported_computational`: computation reported by a paper.
- `paper_reported_experimental`: experimental or structural result reported by a paper.
- `official_example`: upstream example, not a local reproduction.
- `local_replay_unverified`: local execution without established equivalence or biological validity.
- `computational_prediction`: current generated, predicted, or scored candidate.
- `current_experimental`: current-project measurement with sample, assay, date, and raw-data provenance.

Never collapse these labels. A high model score cannot upgrade `computational_prediction` to `current_experimental`.

## Claim record

```yaml
claim_id: "stable-lowercase-id"
claim: "Concise statement"
evidence_status: "paper_reported_experimental"
source: "DOI, official URL, or raw-data pointer"
locator: "Results subsection, figure, table, or file"
stage_or_round: "Named stage"
notes: "Limitations and denominator"
```

## Docking interpretation

- Vina score is a protocol-specific computational ranking value. It is not measured affinity or binding free energy.
- Redocking pose RMSD evaluates protocol pose recovery only when atom mapping, symmetry handling, reference pose, and pose-selection rule are declared.
- DiffDock confidence ranks poses **within one ligand**. Do not use it as an affinity score or a cross-ligand hit ranking.
- PLIP describes predicted interaction geometry in a supplied pose. It does not prove binding, activity, or selectivity.
- A co-crystal structure is paper/database-reported structural evidence; a newly generated pose remains a computational prediction.

For each docking row retain receptor state, ligand ID, chemical-state ID, pose rank, engine/version, seed, metric name/value/unit/role, rank scope, input hash, `raw_output_sha256`, output path, evidence status, experimental status, and notes. For Vina, `raw_output_sha256` is the hash of the raw multi-pose PDBQT that contains the row's pose. For hosted DiffDock, it is the hash of that pose's materialized SDF, not the raw JSON response hash. Use `assets/docking_candidates.template.csv`.

Prediction-only rows must use `evidence_status: computational_prediction` and `experimental_status: not-tested`. Their notes and statuses must not make positive biological or affinity claims, including `affinity`, `free energy`, `active`, `selective`, `inhibitor`, `hit`, `lead`, `binder`, or `validated`. Negated interpretation boundaries remain allowed.

## Threshold record

```yaml
name: "interaction_pae"
operator: "<"
value: 7.5
stage: "initial_complex_prediction"
source: "10.xxxx/..."
source_locator: "Methods section"
scope: "Paper-reported recipe for this task and stage"
```

An unsourced cutoff is a `user_hypothesis`, not an evidence-backed recipe.
