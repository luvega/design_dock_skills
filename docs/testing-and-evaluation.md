# Testing data and evaluation criteria

## 1. What the test suite establishes

The unit tests evaluate routing, schema validation, provenance, command safety and output semantics. They do not benchmark protein-design quality, docking accuracy or experimental success.

| Test module | Tests | Main coverage |
|---|---:|---|
| `test_baker_design.py` | 10 | Protein-design routes, docking-route priority, schema 1.0 compatibility, 8 GB planning downgrade, seed/checkpoint audit, prediction-language boundary |
| `test_docking_workflow.py` | 64 | Request validation, Vina parsing/execution plan, Hosted DiffDock payload/response, secret rejection, path confinement, output hashes, candidate reconstruction, terminal audit |
| `test_structure_resolver.py` | 10 | Explicit RCSB/UniProt identifiers, offline dry-run, provenance writing and non-selection language |
| **Total** | **84** | Offline engineering verification |

## 2. Test-data classes

| Class | Included material | Intended use | Not established |
|---|---|---|---|
| Planning template | YAML with explicit `REPLACE`/`planning_only` fields | Missing-input reporting and run blocking | Executability |
| Synthetic parser fixture | Minimal PDBQT-like text containing two Vina result records | Score/pose parser behavior | Chemical validity or docking performance |
| Synthetic manifest | 50 stable ligand/state IDs with non-existent paths | Batch schema, ID stability and missing-file blocking | Hosted service behavior or ligand ranking |
| Synthetic candidate table | Example Vina and DiffDock rows | Header, rank scope and evidence labels | Real pose, score or confidence |
| External structure link | RCSB 4WKQ | Reproducible case definition without redistributing coordinates | A prepared receptor or completed redocking |
| Mock hosted response | Constructed in unit tests and held in temporary directories | Response parsing, output materialization and secret checks | Live NVIDIA API availability |

## 3. Route tests

### Positive routing cases

| Input clue | Expected route |
|---|---|
| Folded receptor surface, flat PPI, new binder | `folded-target-binder` |
| Peptide, IDR, IDP or flexible helix | `peptide-idr-binder` |
| New protein around ligand/catalytic geometry | `small-molecule-enzyme` |
| Symmetry, stoichiometry or multiple required states | `multistate-oligomer` |
| Ligand-dependent reporter/output system | `allosteric-switch` |
| Existing receptor plus pose/redocking/screen objective | `molecular-docking-screen` |

### Required negative cases

- A generic ligand field must not override an explicit new-protein design objective.
- Docking keywords or explicit `workflow.kind: molecular-docking` take priority over ligand-aware design routing.
- Missing receptor state, ligand state, grid source, seed or controls blocks strict docking.
- Missing checkpoint SHA-256 or seed blocks protein-design execution.
- A planning template containing placeholders must never become run-ready.
- Prediction-only results cannot produce `current_experimental` or positive biological claims.

## 4. Docking protocol tests

### Vina

Pass criteria:

- one reviewed argv per ligand state × seed;
- `shell=false`;
- prepared PDBQT input;
- explicit site provenance, center and size;
- output path confined to package root;
- `REMARK VINA RESULT` parsed into pose rows;
- raw multi-pose PDBQT bytes and SHA-256 retained;
- every candidate row points to the raw-output hash.

Failure cases include missing output, zero poses, non-finite score, argv tampering, path collision, post-prepare input swap and invalid stable IDs.

### Hosted DiffDock

Pass criteria:

- current endpoint exactly matches the official API path;
- upload is authorized and data are classified `public` or `non-sensitive`;
- `NVIDIA_API_KEY` exists only in the environment;
- official request limits are enforced;
- redirect following is disabled;
- raw response and per-pose SDF files receive separate hashes;
- observed service headers use the allowlist;
- confidence is marked `within-ligand`.

Failure cases include stale endpoint, missing environment variable, embedded token, credential-like response content, non-finite confidence, unauthorized upload and cross-ligand affinity ranking.

## 5. Engineering release criteria

| Dimension | Pass condition | Blocking examples |
|---|---|---|
| Schema | Only absent/`"1.0"`/`"1.1"`; strict field types | Numeric version, unknown enum, `NaN`/`Inf` |
| Identity | Stable lowercase path-safe IDs | Traversal, Windows device name, casefold collision |
| Inputs | Existing files and declared SHA-256 match | Missing file, post-prepare swap, empty library |
| Reproducibility | Seed, version/commit, checkpoint/service version recorded | Missing seed/checkpoint hash |
| Execution | Reviewed argv and `shell=false` | Arbitrary command string, auto-install |
| Provenance | Raw output path, bytes and SHA-256 retained | Candidate without raw-output anchor |
| Reports | Blocked/failed/completed states refresh audit and docking reports | Stale terminal report |
| Evidence language | Computational and experimental status remain distinct | “validated binder” from prediction-only row |
| Secrets | No token in input, manifest, output or log | Authorization header or token-like key/value |

## 6. Scientific evaluation criteria

### Protein-design candidates

Evaluation is stage-specific:

1. **Generation**: constraint satisfaction, motif/hotspot geometry, clash and diversity.
2. **Sequence design**: model score/likelihood, composition, fixed positions and unwanted motifs.
3. **Structure prediction**: monomer/complex confidence, interface error, RMSD, local geometry, alternate and negative states.
4. **Experiment**: expression, monodispersity, affinity, specificity, structure, catalysis, signaling, stability or in vivo function.

Any numerical cutoff must retain:

```yaml
name: "<metric>"
operator: "<|<=|>|>="
value: "<number>"
stage: "<named stage>"
source: "<DOI or protocol>"
source_locator: "<section, page, figure or table>"
scope: "<task-specific use>"
```

An unsourced cutoff is a `user_hypothesis`.

### Docking candidates

| Metric/output | Valid role | Invalid interpretation |
|---|---|---|
| Vina score | Pose or compound prioritization within a fixed, declared protocol, with controls | Experimental affinity or binding free energy |
| Redocking RMSD | Pose recovery under declared atom mapping, symmetry, alignment and pose-selection rules | Target biology or affinity validation |
| DiffDock confidence | Pose ranking within one ligand | Cross-ligand hit/affinity ranking |
| PLIP contacts | Geometric annotation of a supplied pose | Energy, selectivity or mechanism proof |
| Co-crystal reference | Database/paper-reported structural evidence | Automatic endorsement of a prepared protocol |

### Experimental handoff

`current_experimental` requires:

- sample or construct identifier;
- assay and protocol version;
- experiment date/batch;
- denominator and control definitions;
- raw-data pointer;
- result interpretation with known limitations.

## 7. Reproducible test command

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:BAKER_DESIGN_TEST_ALLOW_NETWORK = "0"
python -m unittest discover `
  -s .\skills\baker-protein-design\scripts `
  -p "test_*.py" `
  -v
```

Expected software-test terminal:

```text
Ran 84 tests

OK
```

Runtime is machine-dependent and is not part of the acceptance criterion. The test suite must not leave `__pycache__` or `.pyc` files in the deliverable.
