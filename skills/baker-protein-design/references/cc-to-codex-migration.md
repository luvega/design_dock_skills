# CC-to-Codex migration record

This skill is a clean-room migration of workflow intent and safety constraints. Original lecture/source material remains unchanged and is not vendored or rewritten in place.

## Component decisions

- AutoDock Vina material had no explicit reusable license in the supplied lecture/source context. Only high-level workflow intent was carried over; implementation and documentation were independently rewritten from official interfaces. No lecture code was copied.
- ZYDock was identified as MIT-licensed, but its behavior was still safety-rewritten: schema validation, path confinement, reviewed argv arrays, `shell=false`, dry-run defaults, provenance, and secret rejection replace permissive execution patterns.
- Original CC sources have zero modifications.
- The local, non-distributed 21-file SHA-256 baseline is fail-closed. Its source
  inventory and verification utility remain with the excluded CC materials; they
  are not part of this public repository. Any future baseline change requires a
  separate, explicit human review.
- Suspected token-like values were not migrated and are not reproduced here; the aggregate verified match count was `2`. Treat the affected credentials as exposed: revoke them and issue rotated replacements before any hosted request.

Never write a token value into this record, examples, manifests, tests, logs, or candidate tables.
