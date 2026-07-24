# Tool registry policy

Record the **installed/run version and commit** separately from a **paper-era version** and a **dated documentation snapshot**. Never promote a dynamic latest version into a fixed run value. Read `tool-registry-snapshot.md` for previously checked Baker-tool commits; recheck licenses and versions at execution time.

| Tool/service | Official source | Role | License or terms boundary |
|---|---|---|---|
| RFdiffusion | https://github.com/RosettaCommons/RFdiffusion | backbone/binder/motif generation | repository BSD; record checkpoint hash |
| ProteinMPNN | https://github.com/dauparas/ProteinMPNN | sequence design | MIT; record model hash |
| LigandMPNN | https://github.com/dauparas/LigandMPNN | ligand-aware sequence design | MIT; record model hash |
| RoseTTAFold All-Atom | https://github.com/baker-laboratory/RoseTTAFold-All-Atom | all-atom modeling | BSD; databases, SignalP, and weights are separate |
| ProteinGenerator | https://github.com/RosettaCommons/protein_generator | sequence-space/multistate generation | MIT |
| CA_RFDiffusion | https://github.com/baker-laboratory/CA_RFDiffusion | active-site-conditioned generation | MIT |
| PLACER | https://github.com/baker-laboratory/PLACER | ligand/local-geometry assessment | BSD-3-Clause; record weight hash |
| AutoDock Vina | https://github.com/ccsb-scripps/AutoDock-Vina | PDBQT docking/redocking/screening | Apache-2.0; record installed version and executable hash/path |
| Meeko | https://github.com/forlilab/Meeko | PDBQT preparation outside this workflow | LGPL-2.1; current adapter does not run it |
| DiffDock code and published weights | https://github.com/gcorso/DiffDock | pose prediction | MIT for the linked repository/code and released weights; record commit and weight hash |
| NVIDIA DiffDock NIM hosted service | https://docs.api.nvidia.com/nim/reference/mit-diffdock-infer | hosted inference | service access and NVIDIA terms are separate from DiffDock MIT code/weights |
| NVIDIA DiffDock NIM self-hosting docs | https://docs.nvidia.com/nim/bionemo/diffdock/latest/getting-started.html | deployment reference | container/service entitlement and terms require a separate check |
| PLIP | https://github.com/pharmai/plip | pose interaction annotation | GPL-2.0; current adapter is not implemented |
| PyMOL | installed distribution and its local license record | visualization | verify the on-site distribution/license before use; current adapter is not implemented |

## Version recording

Use fields such as:

```yaml
installed_version: "REPLACE_WITH_INSTALLED_VERSION"
installed_commit: "REPLACE_WITH_COMMIT"
paper_era_version: "REPLACE_WITH_VERSION_REPORTED_BY_PAPER"
documentation_snapshot:
  product_version: "2.3.0"
  accessed_or_published_date: "2026-07-06"
  status: "documentation_snapshot_not_run_version"
```

The NIM `2.3.0` / `2026-07-06` pair may be recorded only as a dated documentation snapshot. A run manifest must capture the actual service/container version observed for that run.

Do not vendor repositories or weights. Code availability does not license checkpoints, services, databases, Rosetta/PyRosetta, SignalP, or installed commercial distributions.
