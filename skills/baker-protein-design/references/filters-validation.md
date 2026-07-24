# Filters and validation

## Protein design

Keep four layers separate:

1. Backbone generation: geometry, contig, motif, symmetry, clash, diversity.
2. Sequence design: likelihood, composition, fixed positions, unwanted motifs.
3. Structure prediction: monomer/complex confidence, interface error, RMSD, local geometry, alternate states.
4. Experiment: expression, monodispersity, affinity, specificity, structure, catalysis, signaling, stability, in vivo function.

Every numerical filter needs a stage and source locator. Label an empirical project choice `user_hypothesis`. Keep predicted candidates `prediction_only=true` until assay provenance exists.

## Docking protocol QC

Before accepting results, verify:

- receptor assembly, chain, biological and mutation state;
- protonation, missing residues, alternate locations, and explicit HETATM retain/remove decisions;
- stable lowercase ligand and chemical-state IDs;
- input file hashes and engine/version;
- input schema is absent (defaults to `1.1`) or exactly string `1.0`/`1.1`;
- a binding-site/grid source; zeros are data only when the source explicitly yields zero;
- seed(s), pose count, controls, and redocking reference when applicable;
- Vina rank scope and redocking RMSD protocol;
- DiffDock confidence marked `within-ligand`, never cross-ligand;
- PLIP annotations kept as pose geometry only;
- hosted upload authorization, non-sensitive classification, approved endpoint, and environment-only credential;
- prepared-only or unimplemented adapters blocked from `run`.
- no strict-run placeholders, wrong types, `NaN`/`Inf`, empty libraries, uppercase enums, or uppercase IDs;
- raw output hashes and terminal audit/report refresh are present.

Use `docking-protocol-qc.md` for the complete checklist. Candidate rows must match the exact header in `assets/docking_candidates.template.csv`.
