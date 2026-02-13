# 3D Phase 1

Phase 1 of the 3D photogrammetry pipeline: setup, frame extraction, and initial Metashape processing.

This module wraps the first half of the [3D_vicarius](https://github.com/laurenkolinger/3D_vicarius) pipeline into the VICARIUS module system.

## Purpose

Automates the setup and execution of:
- **Step 0**: Frame extraction from video files using FFmpeg (hardware-accelerated)
- **Step 1**: Initial 3D reconstruction using Agisoft Metashape (photo matching, alignment, depth maps, mesh building, texturing)

After Phase 1 completes, the user performs a **manual step** (straightening and cropping models in the Metashape GUI), then runs Phase 2 for automatic scaling and export.

## Quick Start

```bash
# Run interactively (recommended)
python src/run_phase1.py

# Or with arguments
python src/run_phase1.py --input /path/to/videos --project /path/to/project_dir

# Through VICARIUS CLI
python $VICARIUS_ROOT/start_vicarius.py
# Select [4] Run a Module -> 3D_phase1
```

The interactive runner will:
1. Ask for input folder and project directory
2. Detect input type (videos or pre-extracted frames)
3. Validate naming conventions
4. Set up project structure and Python environment
5. Open analysis_params.yaml in vim for editing
6. Run Step 0 (if video input) and Step 1
7. Print instructions for the manual step

## Inputs

| Input | Type | Formats | Description |
|-------|------|---------|-------------|
| video_files | directory | .mov, .mp4, .mkv, .avi | Videos for frame extraction (step0 + step1) |
| frame_directories | directory | .tiff, .tif, .jpg, .png | Subdirs of pre-extracted frames (step1 only) |

Only one input type is needed. The runner auto-detects which type is present.

## Outputs

| Output | Format | Location | Description |
|--------|--------|----------|-------------|
| PSX files | .psx | `$PROJECT_DIR/processing/psxraw/` | Metashape projects with initial 3D models |
| Frames | .tiff | `$PROJECT_DIR/processing/frames/{MODEL_ID}/` | Extracted 16-bit TIFF frames |
| Tracking CSV | .csv | `$PROJECT_DIR/status_{PROJECT_NAME}.csv` | Processing status for all models |
| Reports | .pdf | `$PROJECT_DIR/processing/reportsraw/` | Metashape processing reports |

## Naming Convention

Input files (videos or frame directories) must follow this pattern:

```
{PROJECTTYPE}{YYYYMMDD}_3D_{SITE}_{REPLICATE}[_n][_PROXY]
```

| Component | Values | Examples |
|-----------|--------|----------|
| PROJECTTYPE | TCRMP, RBTEST, RBMAPPING, HYDRUSTEST, HYDRUSMAPPING, HYDRUSTCRMP, MISC | TCRMP, RBTEST |
| YYYYMMDD | Date | 20241014, 20250301 |
| SITE | 3-letter codes or identifiers | BWR, FLC, DOCK |
| REPLICATE | T1-Tn, TRY1-TRYn, RUN1-RUNn | T2, TRY1, RUN3 |
| _n | Multipart suffix (optional) | _1, _2, _3 |
| _PROXY | Encoding byproduct (stripped automatically) | _PROXY |

Examples:
- `TCRMP20241014_3D_BWR_T2.mov` — single video
- `RBTEST20250301_3D_DOCK_TRY1_1.mp4` — multipart video (part 1)
- `RBTEST20250301_3D_DOCK_TRY1_2_PROXY.mp4` — multipart with PROXY suffix (stripped)

Multipart videos (same base name, different `_n` suffixes) are automatically grouped and their frames combined.

## Parameters

Configured in `analysis_params.yaml` (copied to project directory):

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| frames_per_transect | integer | 1000 | Frames to extract per video |
| chunk_size | integer | 1000 | Max frames per Metashape chunk |
| max_chunks_per_psx | integer | 4 | Max chunks per PSX file |
| use_gpu | boolean | true | GPU acceleration for Metashape |
| enable_texture_gpu | boolean | false | GPU for texturing (can cause OOM) |
| keypoint_limit | integer | 40000 | Max keypoints per image |
| tiepoint_limit | integer | 4000 | Max tie points per image pair |
| texture_size | integer | 16384 | Texture resolution in pixels |

## Runtime Requirements

- **Python 3.9** (bundled with Metashape)
- **Agisoft Metashape Pro** >= 2.1.1
- **FFmpeg** (for video frame extraction)
- **GPU** recommended (CUDA-capable for Linux)

Metashape is detected automatically from `$METASHAPE_PATH`, known paths, or `$PATH`.

## Manual Step (After Phase 1)

After Phase 1 completes, open each PSX file in `$PROJECT_DIR/processing/psxraw/` with the Metashape GUI.

For each chunk:

**Straightening (required):**
1. Load the textured model
2. Auto-adjust brightness/contrast
3. Rotate the model horizontally
4. Use "Model > Region > Rotate Region to View"
5. Resize region to crop to model area
6. Use rectangular crop tool

**Scaling Preparation:**
1. Ensure coded targets are visible
2. Verify at least 2 scale bars of targets are visible

Save and quit Metashape. Then run Phase 2 (`3D_phase2`) for automatic scaling and export.

## Logging Integration

This module integrates with VICARIUS logging:
```python
from vicarius_log import get_log
log = get_log()
log.process_start("3D_phase1", purpose="...", study="S2_3D_structure")
```

## Full 3D Pipeline

```
Phase 1 (3D_phase1)    ← YOU ARE HERE
  └─ Step 0: Frame extraction (FFmpeg)
  └─ Step 1: Initial 3D processing (Metashape)

Manual Step: Straighten and crop models in Metashape GUI

Phase 2 (3D_phase2)
  └─ Step 2: Automatic scaling (coded targets)
  └─ [Manual: Fix FAIL models if needed]
  └─ Step 3: Dual export + orthomosaics
```

### What's Next

After Phase 1 completes:

1. **Manual Step**: Open each PSX file in `$PROJECT_DIR/processing/psxraw/` with Metashape GUI. Straighten, crop, and verify coded targets are visible (see Manual Step section above).
2. **Phase 2**: Run `3D_phase2` on the same PROJECT_DIR for automatic scaling, model export (hi-poly + lo-poly), and orthomosaic generation.

## Changelog

- v0.1.0 (2026-02-13): Initial version — split from 3D_vicarius
