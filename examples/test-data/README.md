# Synthetic test data

This directory contains documentation and parser fixtures only.

- `ligands-50.synthetic.csv` uses stable IDs but points to files that do not exist. It is intended to test planning and strict missing-file blocking.
- `vina-two-pose.synthetic.pdbqt` contains only synthetic `REMARK VINA RESULT` records and model delimiters. It is not a chemically valid structure or docking result.
- `docking-candidates.synthetic.csv` demonstrates the exact candidate-table header and metric roles. Hashes and paths are synthetic.

None of these files support scientific or experimental claims. Do not use them to compare methods, calibrate thresholds or report docking performance.
