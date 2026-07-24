# Folded-target binder

1. Define the biological assembly, target chain, desired epitope, excluded surfaces, valency, and functional mechanism.
2. Inspect surface geometry and glycosylation/membrane constraints. A flat surface requires precise hotspot and counter-screen definitions, not a lower bar for evidence.
3. Generate backbones with a target-conditioned method such as RFdiffusion.
4. Design sequences separately with ProteinMPNN or another declared sequence model.
5. Predict complexes and apply source-linked interface and monomer filters. Preserve each model and seed.
6. Use partial diffusion only as a separately tracked optimization round.
7. Counter-screen homologues, paralogues, alternate target states, and negative targets before selecting candidates.
8. Hand off a diverse set for expression, monodispersity, binding, specificity, structure, and function testing.

For receptor agonism, define oligomer geometry and valency explicitly. A high-affinity monovalent binder does not establish receptor clustering or signaling.
