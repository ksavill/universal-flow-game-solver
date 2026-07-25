# Reference screenshot corpus

This local, persistent corpus holds up to 500 byte-unique source screenshots
for end-to-end image-pipeline regression testing. It is intentionally separate
from `data/image_imports`: archive reprocessing may create duplicate records,
while this corpus keeps one source image per SHA-256 and selects across
geometry, modifiers, grid size, and historical solve status.

The generated images, manifest, and result reports are local artifacts and are
included by `scripts/backup_data.py`; they are not required by CI.

Build or refresh the corpus:

```powershell
python scripts/build_reference_corpus.py --limit 500
```

Replay every selected screenshot from source pixels:

```powershell
uv run --with httpx python scripts/replay_reference_corpus.py `
  --jobs 3 `
  --output reference_screenshot_corpus/results/latest.json
```

The default `recorded` mode reuses the original crop, detector settings,
classifier decision, and any recorded edge corrections. Use `--mode auto` to
exercise the current automatic classifier and detector choices instead.
Baselines are stored independently for both modes.

The first reviewed run can establish the local baseline:

```powershell
uv run --with httpx python scripts/replay_reference_corpus.py `
  --jobs 3 `
  --write-baseline `
  --accept-baseline-update
```

Each stable signature covers the source SHA-256, canonical generated-puzzle
SHA-256, geometry, modifiers, grid, terminal completeness, solve outcome, and
failure reason. Runtime measurements and temporary import identifiers are
reported but excluded from comparisons.

Re-running the builder preserves baselines for unchanged SHA-256 entries and
adds newly archived, byte-unique screenshots until the 500-image capacity is
reached.
