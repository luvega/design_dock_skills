# Casebook

## EGFR gefitinib redocking: RCSB 4WKQ

Use [RCSB 4WKQ](https://www.rcsb.org/structure/4WKQ) as a concrete protocol example, not as an automatic input choice.

1. Confirm the relevant assembly, wild-type EGFR kinase biological/mutation state, and chain A before preparation. Do not assume the asymmetric unit is the intended receptor state.
2. Identify co-crystal ligand `IRE` (gefitinib) and preserve its explicit chemical state as a stable lowercase ID such as `ire-gefitinib-state-01`.
3. Define the Vina grid from the documented co-crystal ligand coordinates. Record the centroid/box derivation and source. A zero coordinate is legal only if that derivation yields zero.
4. Remove the reference ligand for docking while retaining or removing other HETATM records one-by-one according to biological relevance. Record protonation, missing residues, altloc, waters, ions, and cofactors.
5. Redock with declared seeds and a pose-selection rule. Report pose RMSD only with the mapping/symmetry protocol. Report Vina scores only as protocol-specific computational ranking values.
6. Do not call the score affinity, free energy, inhibition, selectivity, or experimental validation.

This validates a receptor-ligand pose workflow. If the next goal is to design a new gefitinib-binding protein or enzyme, start a separate `small-molecule-enzyme` route with its own scaffold/generation and experimental plan.

## Hosted DiffDock batch: 50 ligands

For 50 public/non-sensitive ligands:

1. Create one stable lowercase `ligand_id` and `chemical_state_id` per input in `docking_batch_template.csv`.
2. Confirm explicit upload authorization, current approved endpoint, and a newly provided `NVIDIA_API_KEY` environment variable. Never place the key in YAML, CSV, logs, or commands.
3. Submit one ligand per pose-prediction request and retain raw response hashes and per-pose files.
4. Rank DiffDock poses only within each ligand. A ligand's confidence values do not support ranking that ligand against the other 49.
5. Use a separate, explicitly justified cross-ligand method plus controls if target-focused hit ranking is required.

## TNFR1 flat-surface binder

Specify TNFR1 state, desired surface, monovalent antagonism versus multivalent clustering, family negatives, and assays. The initial 96-design round and later 96-design partial-diffusion round are separate experiments: 90/96 versus 94/96 expressed/monomeric, and 6 versus 28/94 bound. Source: DOI `10.1126/science.adp1779`, Results/Methods, PMC author manuscript `PMC12416549`.

## Small-molecule binding and sensing

For a **new protein**, start from an explicit ligand and pocket/catalytic geometry. Use RFAA/RFdiffusionAA or CA_RFDiffusion for generation, LigandMPNN for sequence design, and PLACER plus local/whole-structure checks. Sources: DOI `10.1126/science.adl2528`, `10.1126/science.adn3780`, and `10.1126/science.adu2454`.

A sensor additionally needs insertion/fusion libraries and experimental ON/OFF selection. For the corrected allosteric-switch example, use apo anticalin PDB `6Z6Z` and colchicine-bound PDB `5NKN`; source DOI `10.1038/s41587-026-03081-9`, correction `10.1038/s41587-026-03263-5`.

## Flexible peptide or IDR

Match target representation to biology and require scrambled peptide, homologous IDR, point-mutant, and cross-specificity controls. Sources: DOI `10.1038/s41586-023-06953-1`, `10.1126/science.adr8063`, and `10.1038/s41586-025-09248-9`.
