# Camera photo reference corpus

This directory holds local synthetic and real photographs used to evaluate the
camera-photo importer. Generated photos, manifests, and replay results are
ignored by Git because source screenshots and real camera photos may contain
private material.

Build deterministic synthetic photographs from the screenshot corpus:

```powershell
python scripts/build_camera_photo_corpus.py --geometry square --target-type square --limit 30 --variants 3
python scripts/replay_camera_photo_corpus.py --output reference_camera_corpus/results/replay.json
```

Variant 0 is the supported mild perspective/blur/JPEG gate. Variant 1 adds a
broad reflection and variant 2 adds stronger blur, JPEG loss, and display
banding; those stress cases may be routed to review. A replay report records
exact structural matches, review-required results (the archive review flag or
the camera photo review), and the critical `incorrect_high_confidence` count.

Levels with ten or more flows use the game's white and gray terminals. Keep a
separate set of them so neutral colors stay covered:

```powershell
python scripts/build_camera_photo_corpus.py --geometry square --min-terminals 20 --limit 5 --variants 3 --output reference_camera_corpus/sets/large-flows
python scripts/replay_camera_photo_corpus.py --corpus reference_camera_corpus/sets/large-flows
```

By default the replay sends each photo's labeled screen corners, which isolates
board and terminal recovery. Add `--auto-corners` to exercise automatic
display detection as well.

`--variants 6` adds camera effects on top of the mild variant: 3 = sensor
noise, color cast, and vignetting; 4 = 8-25 degree in-plane rotation; 5 = moire
from the photographed pixel grid. Variants 0-2 are byte-identical whether 3 or
6 variants are built.

```powershell
python scripts/build_camera_photo_corpus.py --geometry square --target-type square --limit 10 --variants 6 --output reference_camera_corpus/sets/realistic
```

## Scene renderer

`--renderer scene` (`scripts/synthetic_camera.py`) renders each screenshot as
a photo of a mock device in a mock room instead of applying fixed
degradations:

- **Devices** (9): notch, punch-hole, and island phones, home-button phones
  with white or black fronts, rugged and clear cases, a compact phone, and a
  tablet. The screenshot is letterboxed to each display's aspect.
- **Environments** (9): wood desk, office desk, dark cloth, marble, ruled
  notebook paper, carpet, outdoor concrete, sofa fabric, and a night-time glass
  table, each with its own light (window, ceiling, lamp, overcast), color
  temperature, clutter, drop shadow, and optional hand holding the phone.
- **Display and glass**: emitted light independent of the room, glass
  reflections with the photographer's silhouette, soft glare from the light
  source, backlight flicker banding, and moire from RGB subpixels aliasing
  against the camera grid.
- **Camera**: 3D pose (yaw, pitch, roll, distance), auto-exposure,
  white-balance error, vignetting, lens distortion, chromatic aberration,
  optical/defocus/motion blur, signal-dependent noise, sharpening halos,
  optional messaging-app downscale, JPEG, and EXIF orientation/metadata.

Every photo's full scene parameters are stored in the manifest, so any photo
can be regenerated exactly. Labels include `screen_corners` and
`board_corners` (from the screenshot's board crop), carried through lens
distortion and EXIF rotation. Each entry records objective `envelope`
metrics (display size, foreshortening, glare coverage, occlusion, blur, cell
size) and `in_envelope` against the supported-capture criteria.

Difficulties are mixed with `--difficulty-mix` (default
`easy=0.4,medium=0.4,hard=0.2`). Splits (`--splits`, default
`train=0.7,val=0.15,test=0.15`) are assigned per source screenshot, so a
puzzle never appears in two splits. The `tablet-gray` and `clear-case-notch`
devices and the `glass-table-night` environment are held out: they only
appear in the test split, so test results also measure generalization to
unseen hardware and rooms.

```powershell
python scripts/build_camera_photo_corpus.py --renderer scene --geometry square --limit 45 --scenes-per-source 3 --output reference_camera_corpus/sets/scene-square --preview reference_camera_corpus/sets/scene-square/preview.png
python scripts/replay_camera_photo_corpus.py --corpus reference_camera_corpus/sets/scene-square --split test --auto-corners
```

The replay report adds `by_difficulty`, `by_in_envelope`, `by_device`,
`by_environment`, `by_split`, and `by_geometry` breakdowns. Check `--preview`
contact sheets after changing the renderer: labels are drawn on each photo.

Renderer `scene-v2` adds a curved-edge phone (`curved-edge-silver`), screen
protectors (extra reflection, haze, trapped-air bubbles), cracked glass (always
outside the envelope), and a second reflected light source.
`--full-resolution` renders at native 12 MP phone-camera sizes; it is about
three times slower and needs roughly 1.5 GB of memory per photo. Region-graph
replays (hex and other region boards) compare cells by normalized position,
because region ids and pixel data legitimately differ between a screenshot and
a photo.

## Real photos

Real camera imports become labels once a person reviews them: clearing an
import's review flag records `reviewed_at`, and the archived result at that
point is the expected puzzle. Build the corpus from the archive, split into
`tuning` and `holdout` by camera device (a hashed make/model recorded at
upload) or by capture day when only one device is present:

```powershell
python scripts/build_real_camera_corpus.py --output reference_camera_corpus/real
python scripts/replay_camera_photo_corpus.py --corpus reference_camera_corpus/real --split tuning --auto-corners --output reference_camera_corpus/results/real-tuning.json
python scripts/calibrate_camera_review.py reference_camera_corpus/results/real-tuning.json --split tuning
```

The calibration report shows, for each review threshold, how many wrong
results would go unflagged and how many photos would be sent to review. Adjust
`CAMERA_REVIEW_THRESHOLDS` in `backend/app.py` from the tuning split, then
confirm the change with `--split holdout` without tuning on it.
`--include-unreviewed` also harvests solved, never-flagged imports, labeled
`solved-unreviewed`; keep those out of accuracy claims.

Real-photo entries use the same manifest shape and add reviewed labels for
display corners, board crop, grid dimensions, terminal cells, and whether the
capture falls inside the supported quality envelope. Keep tuning and held-out
captures separated by device and capture session.
