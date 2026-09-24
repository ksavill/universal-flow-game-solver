# Camera Photo Puzzle Import Plan

## Objective

Support slightly blurry, off-angle photographs of a puzzle displayed on
another phone while preserving the quality, output, and performance of the
existing screenshot importer.

## Implementation status (2026-09-23, preprocessing `camera-v3`)

Implemented for the initial guarded release:

- versioned `screenshot`, `camera`, and conservative `auto` source contracts;
- EXIF-safe decode limits, multi-candidate display detection, manual corners,
  two-stage display/board rectification, lattice-aware candidate scoring,
  separate geometry/color views, and diffuse/clipped glare masks that exclude
  the game's white and gray terminal dots;
- blur measured as an edge-spread estimate relative to cell size, exposure
  judged from the brightest content, and banding measured after grid lines are
  removed (phase 4);
- one cached preparation per upload shared by every endpoint (phase 6), with
  grid hints re-ranking the board only when the unhinted lattice disagrees;
- review routing from display ambiguity/corroboration and near-miss terminals
  in addition to quality metrics (part of phase 5);
- metadata-stripped camera archives and job uploads (performance and safety);
- consistent single, guided, client-batch, server-job, archive, and reprocess
  mode propagation with persisted transforms and preprocessing version;
- draggable four-corner UI, corrected preview, quality warnings, and automatic
  durable review flags outside the reliable envelope; detected corners stay
  automatic until the user moves or confirms them;
- synthetic camera-corpus build/replay tools (including a 10+ flow set and an
  `--auto-corners` mode) and API/unit regression coverage.

Completed in the follow-up pass:

- `auto` without camera EXIF compares the two pipelines: it takes the camera
  path only when the screenshot path's grid is missing or irregular *and* the
  camera path finds a confident board through a corroborated, tilted display
  outline. Geometry alone could not do this (hexagonal and diamond boards in
  screenshots look like a tilted phone), but no corpus screenshot passes both
  checks: 0 of 219 are misrouted, while 37 of 75 EXIF-less synthetic photos are
  recognized. Photos with camera EXIF still switch on metadata.
- Phase 5 candidate scoring: when the best display outline is ambiguous or
  uncorroborated, the top three outlines are each carried through board
  detection and scored by the board they produce (cached per outline).
- The browser uploads each image once (`POST /image/uploads`) and later
  stages send only its content hash; expired uploads are re-sent
  automatically.
- `pillow-heif` is a declared dependency, so HEIC/HEIF photos decode.
- Real-photo workflow: clearing a review flag records `reviewed_at`, uploads
  record a pseudonymous `device_id`, `scripts/build_real_camera_corpus.py`
  harvests reviewed camera imports into a device-split tuning/held-out corpus,
  and `scripts/calibrate_camera_review.py` sweeps every review threshold
  against replay reports.
- The synthetic corpus gained sensor-noise/color-cast, in-plane rotation, and
  moire variants (3-5); variants 0-2 are unchanged.

Still open (measured with the scene renderer, `scripts/synthetic_camera.py`):

- Square boards: inside the envelope 60% reconstruct exactly with labeled
  corners but 30% with automatic corners; automatic display detection struggles
  with bezels, cases, hands, and clutter around the phone.
- Photos lose warp adjacencies on walls/warps boards (16 unflagged errors in
  73), and hex boards fail region-graph detection entirely from photos. Treat
  both as unsupported for photos until warp-glyph and region detection are
  adapted to rectified photos; a crop margin alone did not help.
- Review misses two terminal failure modes: a whole pair lost when no flow
  count is advertised, and similar colors paired with the wrong partner under
  a white-balance shift.

- Thresholds remain calibrated on synthetic photos only. The tooling above is
  ready, but no labeled real-device photos exist yet; run the harvest and
  calibration on the tuning split, then confirm on the held-out split, before
  widening the supported envelope.
- Automatic display detection misses the true screen outline when the app's
  black background meets a dark bezel or desk (the main cause of the gap
  between labeled-corner and automatic-corner accuracy). Candidate scoring
  cannot recover an outline that was never proposed. Extrapolating the board's
  own grid lines to its corners was prototyped and rejected: only 1 of 6
  failing photos would have been fixed, so it needs a proper
  perspective-lattice fit.

Verification results are recorded in `docs/PRODUCTION_READINESS.md`.

The central design rule is to keep screenshot processing on its current path
and add a separate camera-photo preparation path that produces a
screenshot-like rectified image for the existing classifier, OCR, grid,
terminal, topology, and solver stages.

```text
Uploaded image
|- Screenshot mode --------------------------------> existing pipeline
|- Camera mode
|  `- orient -> assess -> locate display/board -> rectify
|     -> suppress camera artifacts -> prepared views -> existing pipeline
`- Auto mode
   `- score the original and camera candidates
      -> select only with strong evidence ----------> existing pipeline
```

## Compatibility contract

- Add `source_mode=screenshot|camera|auto`.
- Keep `screenshot` as the default for existing API calls and archived imports.
- Leave the current screenshot crop, optional perspective, classification,
  grid, and terminal paths behaviorally unchanged.
- Preserve the legacy `perspective` option.
- Put camera-specific work in a dedicated module instead of expanding the
  existing image utility module.
- In `auto` mode, retain the unmodified screenshot path as a candidate and
  prefer it when scores are close.
- Treat imports without a persisted source mode as screenshots.

The committed/local screenshot corpus is the hard non-regression gate:

- Preserve semantic puzzle hashes in recorded mode.
- Do not turn a previously solved screenshot into an unsolved import.
- Preserve grid dimensions, terminal cells, geometry, and modifiers.
- Avoid a material screenshot p95 latency increase.
- Keep existing API clients backward-compatible.

## Phase 1: Input contract and normalized preparation

Introduce a prepared-photo model containing:

- original and oriented dimensions;
- source format and preprocessing version;
- downscaled detection image and capped-resolution color image;
- proposed display/board quadrilaterals;
- selected homography plus forward/inverse transforms;
- rectified color and geometry views;
- unreliable/glare mask;
- quality measurements and warnings;
- candidate scores and selection evidence.

Camera ingestion will:

1. Apply EXIF orientation before RGB conversion.
2. Enforce upload byte, decoded pixel, and working-dimension limits.
3. Detect at a bounded pyramid resolution.
4. Warp a higher-resolution image only for shortlisted/winning candidates.
5. Return clear errors for unsupported camera formats and add HEIC support or
   browser-side conversion if needed.
6. Persist enough transform and version information for deterministic replay.

## Phase 2: Display and board localization

Replace the camera path's single largest-four-point-contour choice with
multiple candidate proposals:

- multi-scale Canny contours at several thresholds;
- external and nested contour hierarchy candidates;
- long line segments and quadrilaterals formed from opposite line pairs;
- illuminated display boundaries against bezel/background;
- grid envelopes extrapolated to outer corners;
- inner board candidates found after display rectification.

Reject candidates that are non-convex, too small, geometrically
ill-conditioned, mostly out of frame, weakly supported on several sides, or
too oblique to rectify without destructive magnification. Deduplicate similar
candidates and carry a small shortlist forward.

Use two-stage rectification for photographed phones:

1. Rectify the phone display from the full photograph.
2. Run the established board crop on the rectified display.
3. Use lattice evidence for a small residual board correction.
4. Evaluate right-angle rotations when orientation is uncertain.

Do not force square-grid corrections onto circle, region, star, figure-eight,
or other nonrectangular puzzle layouts.

## Phase 3: Blur and photographed-screen artifacts

Prepare separate downstream views.

### Geometry view

Use a luminance image with conservative local contrast, mild edge-preserving
denoising, blur-aware sharpening, multi-threshold edges, and area resampling
after rectification. Correct horizontal display banding only when it is
confidently detected.

### Color view

Keep rectified original color data for terminal detection. Avoid aggressive
sharpening, estimate illumination from likely background cells, evaluate raw
and conservatively balanced colors, mask clipped glare, use robust cell color
statistics, and compare colors perceptually rather than relying only on RGB.

Never synthesize evidence hidden by severe blur or glare. Low-quality input
must produce a retake/review result rather than invented terminals.

## Phase 4: Quality assessment

Measure:

- scale-normalized blur and edge spread;
- glare overlapping the candidate board;
- under/overexposure;
- board resolution and estimated pixels per cell;
- perspective severity and homography conditioning;
- residual grid convergence and spacing regularity;
- moire or periodic display banding;
- board area obscured by glare or crop boundaries.

Translate measurements into actionable capture guidance, including moving
closer, avoiding glare, exposing missing corners, or retaking an image that is
too blurred to distinguish terminals.

## Phase 5: Candidate evaluation and safe selection

Run the existing detection stages on each shortlisted preparation and score:

- quadrilateral edge support;
- grid count, coverage, spacing regularity, and residual convergence;
- terminal center placement and colors occurring in pairs;
- agreement with OCR's expected flow count;
- level-classifier confidence;
- parser and structural validation;
- solver success plus independent verification;
- blur, glare, and magnification penalties.

Solver success is supporting evidence, not proof of a correct visual import.
Require hard validity gates and a meaningful score margin. Uncertain results
must be surfaced for review. Store a versioned score breakdown.

## Phase 6: Consistent server-side processing

Avoid independently re-preparing the same photograph for classification,
OCR, grid, terminal, and generation calls. Refactor around a single prepared
image and use a unified camera processing operation, while leaving existing
screenshot endpoints available and compatible.

Persist:

- source mode and preprocessing version;
- orientation, selected corners, homography, and board crop;
- quality metrics and warnings;
- candidate score summaries and selected candidate;
- original request options and downstream detection evidence.

Server-side batch jobs and archive reprocessing must use the same preparation
and selection logic as the single-image path.

## Phase 7: Camera and correction UI

- Make the existing camera button select camera mode automatically.
- Add an input-profile selector for gallery/file uploads.
- Show the detected display quadrilateral and rectified preview.
- Add four draggable corner handles, reset, and alternate-candidate actions.
- Re-run downstream detection without another upload when corners change.
- Overlay detected grid cells and terminals on the prepared preview.
- Display concise quality and retake guidance.
- Preserve the existing screenshot workflow and advanced controls.

## Phase 8: Regression corpora and tests

Keep camera data separate from the screenshot corpus.

### Synthetic camera corpus

Generate known-ground-truth variants with projective warp, rotation, Gaussian
and mild motion blur, downsampling, JPEG compression, color/gamma changes,
bezel/background composition, glare, moire, brightness bands, shadows, and
combined degradations.

### Real camera corpus

Capture multiple source displays, camera phones, orientations, lighting
conditions, angles, focus quality, screen brightness levels, and puzzle
geometries. Label screen corners, board crop, dimensions, terminal cells,
expected flow count, exact puzzle, and supported-quality status. Split by
device and capture session.

Add tests for:

- EXIF orientation and transform composition;
- known homographies and corner reprojection;
- nested display/bezel/board quadrilaterals;
- blur, glare, and banding measurements;
- degraded-image grid and terminal recovery;
- glare masks not creating terminals;
- API defaults and archive compatibility;
- deterministic archive reprocessing;
- camera UI, corner correction, preview, and retake feedback;
- full screenshot and camera replay gates.

Initial supported-envelope release targets:

- all four display corners visible;
- rectified display at least 600 pixels on its short side;
- mildly blurred but visibly distinguishable grid boundaries;
- no more than roughly 10% of board area obscured by glare;
- moderate rather than extreme perspective compression;
- at least 95% correct display/board localization;
- at least 98% correct grid dimensions;
- at least 95% terminal-cell precision and recall;
- at least 90% exact end-to-end puzzle reconstruction;
- fewer than 1% incorrect results labeled high-confidence;
- zero screenshot semantic regressions.

Targets will be calibrated against the first held-out real-photo corpus.

## Performance and safety

- Detect on bounded image pyramids.
- Carry only a small candidate beam into expensive downstream stages.
- Warp higher resolution only after cheap geometric ranking.
- Cache preparation within a request/job.
- Enforce byte, pixel, dimension, candidate-count, time, and memory limits.
- Reuse the existing image pipeline concurrency controls.
- Record stage timings without persisting every derivative unless diagnostic
  mode is enabled.
- Retain and delete camera originals under an explicit privacy/retention
  policy because photos may contain surroundings.

## Rollout

1. Lock screenshot baselines and compatibility contracts.
2. Release explicit camera mode behind a feature flag.
3. Add manual corners before depending on increasingly complex automation.
4. Add blur/color preparation, glare masks, and candidate scoring.
5. Tune against a held-out real-camera corpus.
6. Run automatic camera candidates in shadow mode for gallery uploads.
7. Allow opt-in auto selection when confidence is clearly higher.
8. Make the camera button use the proven camera path while keeping screenshot
   defaults unchanged.

## Implementation slices

1. Screenshot regression contract and `source_mode`.
2. Camera preparation module, EXIF handling, limits, and quality metrics.
3. Multi-candidate display detection and homography.
4. Rectified geometry/color views and glare masks.
5. Candidate scoring and safe selection.
6. Unified camera processing and persisted metadata.
7. Manual-corner and corrected-preview UI.
8. Synthetic camera corpus and replay tooling.
9. Real-photo evaluation, tuning, and guarded rollout.

The first useful milestone consists of explicit camera mode, screen-first
rectification, EXIF handling, quality warnings, manual corner correction, and
the screenshot regression gate. Full completion includes automated candidate
selection, degradation handling, batch/reprocess parity, camera corpora,
release gates, and documentation.
