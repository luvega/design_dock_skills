# Task routing

Route from the biological objective before choosing software.

| Route | Use when | Load next | Typical engines |
|---|---|---|---|
| `folded-target-binder` | Design a new binder to a folded surface or flat PPI | `folded-target-binder.md` | RFdiffusion, ProteinMPNN, structure prediction |
| `peptide-idr-binder` | Design a new binder to a peptide, IDR, IDP, or flexible helix | `peptide-idr-binder.md` | flexible-target RFdiffusion, ProteinMPNN |
| `small-molecule-enzyme` | Design a new protein around a ligand, substrate, transition-state geometry, metal, or covalent context | `small-molecule-enzyme.md` | RFAA/RFdiffusionAA, CA_RFDiffusion, LigandMPNN, PLACER |
| `molecular-docking-screen` | Predict poses, redock a known ligand, or screen ligands against an existing receptor | `molecular-docking-screen.md`; then `docking-protocol-qc.md` | AutoDock Vina, hosted or self-hosted DiffDock |
| `multistate-oligomer` | Design a new protein whose symmetry, assembly, or multiple states define success | `multistate-oligomer.md` | ProteinGenerator, symmetry-aware generation |
| `allosteric-switch` | Design a new protein system that changes state or signal after ligand binding | `allosteric-switch.md` | receptor design plus insertion/fusion/selection |

Do not route an existing receptor-ligand pose question to `small-molecule-enzyme`: that route creates a new protein. Do not route new enzyme or ligand-binding protein generation to docking: docking does not generate the protein scaffold.

Ask before routing if mechanism, target assembly/chain/state, mutation state, ligand chemical state, valency, or hosted-upload authorization is unresolved.
