# Methods, inputs, outputs and primary sources

## Source-selection method

本页覆盖 skill 当前直接登记的方法，不扩展为全领域综述。

- **检索日期**：2026-07-24
- **纳入来源**：官方 GitHub 仓库及 LICENSE、Crossref DOI 元数据、官方服务文档、RCSB 条目、原始方法论文
- **排除来源**：博客转载、聚合下载页、镜像仓库、未核对的二手参数表
- **核对方法**：GitHub API 核对仓库、默认分支、commit 和 LICENSE；Crossref API 核对 DOI 与论文题名；官方页面核对 DiffDock NIM endpoint 与 4WKQ 条目
- **版本原则**：论文年代版本、当前仓库快照和实际运行版本分别记录

仓库或服务可能在检查日期后变化。实际运行前应重新核对 commit、checkpoint、许可和服务条款。

## Protein-design methods

| Method | Role | Minimum input | Main computational output | Interpretation boundary | Official code | Primary paper |
|---|---|---|---|---|---|---|
| RFdiffusion | Target-conditioned backbone, binder and motif generation | Target structure/state, chain, hotspot/motif, contig, seed | Candidate backbones and generation metadata | Generated geometry is not expression, affinity or function | [Repository](https://github.com/RosettaCommons/RFdiffusion) | [De novo design of protein structure and function with RFdiffusion](https://doi.org/10.1038/s41586-023-06415-8) |
| ProteinMPNN | Sequence design for a declared backbone | Backbone, fixed positions, chain design mask, seed, model checkpoint | Candidate sequences and model likelihood-related fields | Sequence-model score is not binding or stability measurement | [Repository](https://github.com/dauparas/ProteinMPNN) | [Robust deep learning–based protein sequence design using ProteinMPNN](https://doi.org/10.1126/science.add2187) |
| LigandMPNN | Atomic-context-conditioned sequence design | Protein backbone plus explicit ligand/metal/nucleic-acid context and chemical state | Context-aware candidate sequences | The ligand chemical state must not be inferred or pooled | [Repository](https://github.com/dauparas/LigandMPNN) | [Atomic context-conditioned protein sequence design using LigandMPNN](https://doi.org/10.1038/s41592-025-02626-1) |
| RoseTTAFold All-Atom | All-atom biomolecular modeling and design context | Protein and non-protein atoms, declared state and dependencies | All-atom predictions/design intermediates | Whole-structure confidence and local ligand geometry are separate | [Repository](https://github.com/baker-laboratory/RoseTTAFold-All-Atom) | [Generalized biomolecular modeling and design with RoseTTAFold All-Atom](https://doi.org/10.1126/science.adl2528) |
| RFdiffusion All-Atom | Ligand-aware all-atom diffusion generation | Explicit ligand identity/state and protein design constraints | Ligand-aware backbone hypotheses | A generated pocket does not establish binding or catalysis | [Repository](https://github.com/baker-laboratory/rf_diffusion_all_atom) | [Generalized biomolecular modeling and design with RoseTTAFold All-Atom](https://doi.org/10.1126/science.adl2528) |
| ProteinGenerator | Sequence-space diffusion for multistate/functional design | Positive/negative states, symmetry or sequence constraints | Sequence/backbone candidates across declared states | Same sequence must be evaluated in every required state | [Repository](https://github.com/RosettaCommons/protein_generator) | [Multistate and functional protein design using RoseTTAFold sequence space diffusion](https://doi.org/10.1038/s41587-024-02395-w) |
| CA_RFDiffusion | Active-site-conditioned backbone generation | Reaction-state/catalytic geometry, ligand/metal state, seed | Backbones conditioned on active-site geometry | Catalytic geometry is not a measured rate | [Repository](https://github.com/baker-laboratory/CA_RFDiffusion) | [Computational design of serine hydrolases](https://doi.org/10.1126/science.adu2454) |
| PLACER | Local ligand/reaction-state geometry assessment | Candidate structure, ligand/reaction-state atoms, checkpoint | Local-environment ensemble and geometry-related scores | PLACER output remains computational | [Repository](https://github.com/baker-laboratory/PLACER) | [Computational design of serine hydrolases](https://doi.org/10.1126/science.adu2454) |

## Molecular-docking methods

| Method/service | Role | Minimum input | Main output | Metric role | Implementation status | Official source |
|---|---|---|---|---|---|---|
| AutoDock Vina | Pocket-defined docking, redocking and protocol-consistent screening | Prepared receptor/ligand PDBQT, explicit grid, seed, pose count, exhaustiveness | Multi-pose PDBQT with `REMARK VINA RESULT` | Protocol-specific score; redocking RMSD needs declared mapping/symmetry | Built-in adapter | [Code](https://github.com/ccsb-scripps/AutoDock-Vina), [paper](https://doi.org/10.1002/jcc.21334), [FAQ](https://autodock-vina.readthedocs.io/en/latest/faq.html) |
| DiffDock | Blind pose generation | Protein structure, one ligand state, model/version | Ranked poses and confidence | Confidence ranks poses within one ligand; it is not affinity | Upstream/self-hosted code is external | [Code](https://github.com/gcorso/DiffDock), [ICLR 2023 paper](https://openreview.net/pdf?id=kKF8_K-mBbS) |
| NVIDIA DiffDock NIM hosted | Hosted DiffDock inference | Public/non-sensitive protein PDB, ligand SDF/Mol2, explicit authorization and API environment variable | Response JSON plus materialized pose SDF files | Same within-ligand confidence boundary | Built-in hosted adapter | [API reference](https://docs.api.nvidia.com/nim/reference/mit-diffdock-infer), [model card](https://docs.api.nvidia.com/nim/reference/mit-diffdock) |
| NVIDIA DiffDock NIM self-hosted | Private/local service deployment | Installed and licensed container/service plus declared endpoint | Depends on actual deployment | Record observed service/container version | `prepare-only` | [Getting started](https://docs.nvidia.com/nim/bionemo/diffdock/latest/getting-started.html) |
| Meeko | Receptor/ligand PDBQT preparation | Explicit chemical state and preparation protocol | Prepared PDBQT | Preparation provenance, not a docking score | External preparation only; no run adapter | [Repository](https://github.com/forlilab/Meeko) |
| PLIP | Interaction annotation for a supplied pose | Pose, receptor/ligand protonation, tool version and parameters | Hydrogen-bond, hydrophobic, salt-bridge and related geometry annotations | Predicted contact geometry; not energy or mechanism evidence | Adapter not implemented | [Code](https://github.com/pharmai/plip), [paper](https://doi.org/10.1093/nar/gkv315) |
| PyMOL | Visualization | Installed distribution and structure/pose files | Images/session files | Visualization only | Adapter not implemented | Verify the installed distribution and local license |

## Route-supporting case sources

These papers support examples and route-specific cautions; their numerical recipes are not universal defaults.

| Route/case | Source | What enters the skill | Boundary |
|---|---|---|---|
| TNFR flat-surface binder | [Target-conditioned diffusion generates potent TNFR superfamily antagonists and agonists](https://doi.org/10.1126/science.adp1779) | Target-conditioned generation, partial-diffusion round separation, family counter-screen | First-round and partial-diffusion experiments retain separate denominators |
| Helical peptide binder | [De novo design of high-affinity binders of bioactive helical peptides](https://doi.org/10.1038/s41586-023-06953-1) | Flexible peptide-target route and specificity controls | A fixed peptide model does not represent every biological conformation |
| IDR binder | [Design of intrinsically disordered region binding proteins](https://doi.org/10.1126/science.adr8063) | Scrambled/homolog/point-mutant controls | Sequence-only generation is not cellular ensemble evidence |
| IDP binder diffusion | [Diffusing protein binders to intrinsically disordered proteins](https://doi.org/10.1038/s41586-025-09248-9) | Joint/flexible target-binder design concepts | Target conformation remains a model hypothesis until tested |
| Artificial allosteric switch | [Artificial allosteric protein switches with machine-learning-designed receptors](https://doi.org/10.1038/s41587-026-03081-9) | Receptor insertion/fusion libraries and ON/OFF experimental selection | A one-pass ligand-binding calculation does not establish switching |
| EGFR–gefitinib redocking | [RCSB 4WKQ](https://www.rcsb.org/structure/4WKQ) | Co-crystal-defined site and pose-recovery protocol example | Users must confirm assembly, chain, construct, HETATM and chemical state |

## License and availability notes

- Repository code, released checkpoint, training data, external database and hosted service are separate legal objects.
- A GitHub API `NOASSERTION` result does not prove absence of a license. For RFdiffusion, RFAA, RFdiffusion All-Atom and PLACER, this project also inspected the upstream `LICENSE` file and recorded its SHA-256 in the dated snapshot.
- Rosetta/PyRosetta, SignalP, model databases and installed PyMOL distributions require independent review.
- This repository vendors none of the listed software or weights.

See [`tool-registry-snapshot.md`](../skills/baker-protein-design/references/tool-registry-snapshot.md) for the dated commit and LICENSE snapshot.
