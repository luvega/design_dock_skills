# Project illustrations

The four PNG files in this directory were generated with OpenAI image generation on 2026-07-24 and visually checked before publication. They are documentation illustrations, not structural models, scientific measurements or benchmark results.

| File | Purpose | Prompt summary |
|---|---|---|
| `project-icon.png` | Repository icon | Square flat-vector emblem combining a protein ribbon, ligand pocket and audit check; navy/teal/coral; no text or logos |
| `workflow-overview.png` | Main workflow | Six labeled stages `DEFINE → ROUTE → PREPARE → RUN → AUDIT → EXPERIMENT`, with separate protein-design and docking lanes and execution/evidence gates |
| `method-taxonomy.png` | Route classification | `NEW PROTEIN` routes separated from `EXISTING RECEPTOR` docking; method names kept to short exact labels |
| `evidence-boundary.png` | Evaluation boundary | Inputs, compute, QC, audit and experiment, with `PREDICTION ≠ EXPERIMENT` and no numeric performance claims |

## Full generation prompts

### `project-icon.png`

> Create a square 1:1 project icon for an open scientific software repository about auditable protein design and molecular docking. Flat vector-style emblem, not a photo. Central abstract protein alpha-helix/ribbon curves around a small hexagonal ligand seated in a binding pocket; integrate a subtle checkmark/audit trail motif into the negative space. Color palette: deep navy, teal, cyan, and one restrained coral accent. Crisp geometric edges, generous negative space, readable at 48 px, balanced scientific aesthetic, no text, no letters, no numbers, no border, no watermark, no brand logos. Solid very-light cool gray background.

### `workflow-overview.png`

> Create a clean landscape 16:9 scientific workflow infographic for a software repository named only through six short stage labels. White background, flat vector editorial style, deep navy and teal with restrained coral warnings. Show one horizontal flow from left to right: DEFINE → ROUTE → PREPARE → RUN → AUDIT → EXPERIMENT. At ROUTE, split into two clearly separate lanes: upper lane labeled PROTEIN DESIGN with stylized folded protein, peptide, ligand-aware pocket, oligomer, and switch icons; lower lane labeled DOCKING with an existing receptor pocket and small-molecule pose icons. The lanes rejoin at AUDIT. Include tiny hash, seed, version, and evidence-tag symbols as visual motifs, but no extra prose. Put a visible gate before RUN and a bold evidence boundary before EXPERIMENT. Exact short labels only, spelled correctly: DEFINE, ROUTE, PREPARE, RUN, AUDIT, EXPERIMENT, PROTEIN DESIGN, DOCKING. No logos, no watermark, no decorative background texture, no photorealism. High information clarity suitable for a GitHub README hero diagram.

### `method-taxonomy.png`

> Create a landscape 16:9 scientific method taxonomy infographic on a white background, flat vector editorial design, deep navy, teal, cyan, restrained coral accents. Split the canvas into two clearly separated panels with a strong central boundary. Left panel exact title: NEW PROTEIN. Show five icon-based branches with exact short labels: FOLDED BINDER, PEPTIDE / IDR, LIGAND / ENZYME, MULTISTATE, SWITCH. Visual motifs: designed protein scaffold, flexible peptide, ligand pocket/catalytic geometry, symmetric oligomer, two-state switch. Right panel exact title: EXISTING RECEPTOR. Show a receptor pocket receiving existing ligands, with two primary branches exact labels: VINA and DIFFDOCK; beneath them two small optional-analysis cards exact labels: MEEKO and PLIP. Add a small central decision diamond labeled OBJECTIVE. Arrows should make clear that the objective determines the side; no arrows should imply that docking creates a protein. Keep text minimal and spelled exactly. No performance claims, no decorative filler, no logos, no watermark, no photorealism. Suitable as a GitHub README classification figure.

### `evidence-boundary.png`

> Create a landscape 16:9 scientific evidence and evaluation workflow diagram on a white background, flat vector editorial style, deep navy and teal with restrained coral warning accents. Show five exact labeled stages from left to right: INPUTS → COMPUTE → QC → AUDIT → EXPERIMENT. Under INPUTS show structure, ligand, chemical state, chain, hash, seed icons. Under COMPUTE show separate icons for backbone generation, sequence design, structure prediction, and docking. Under QC show metric cards labeled only RMSD, PAE, pLDDT, VINA SCORE, CONFIDENCE, CONTACTS. Under AUDIT show provenance, version, checkpoint hash, candidate table, and a red vertical boundary labeled PREDICTION. To the right of that boundary, EXPERIMENT shows assay plates, binding curves, expression, function, and raw-data provenance. Include a clear statement in large exact text near the boundary: PREDICTION ≠ EXPERIMENT. Do not show any numeric threshold or success rate. No logos, no watermark, no photorealism, no extra prose. High legibility for a GitHub documentation subfigure.
