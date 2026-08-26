# 3D phase 1 (`3D_phase1`)

Turns underwater transect video into draft 3D photogrammetric reconstructions, so a reef survey operator can align, mesh, and texture each transect in Metashape before hand-finishing and scaling it.

- Tags: 3D | Version: 0.2.0 | Status: active | Owner: Lauren K Olinger
- Repo: `vicarius/modules/3D_phase1/github_repo` (upstream project https://github.com/laurenkolinger/3D_vicarius) | Related studies: S2_3D_structure

## What it does and why it exists

3D_phase1 is the first stage of the VICARIUS reef photogrammetry pipeline. It takes a directory of underwater transect videos (or a directory of already-extracted frames), sets up a self-contained project workspace, pulls still frames from each video with FFmpeg, and runs Agisoft Metashape to align the cameras, build depth maps, build a mesh, and texture it. The output of the stage is a set of draft Metashape projects (PSX files), one PDF report per model, and a status CSV that tracks every transect.

The module exists to wrap the first half of the standalone `3D_vicarius` project into the VICARIUS module system, so the run is launched, logged, and tracked the same way as every other module. An operator runs it once per field collection, at the start of the survey chain, before any downstream scaling or segmentation happens. Each transect video becomes one 3D model.

Two properties shape the design of the module. First, the job is long: a single run takes an estimated 2 to 24 hours depending on the number and size of the models, so the module launches only as a terminal run and never as an in-browser stream tied to a tab. Second, the run must survive interruption: version 0.2.0 added a set of safeguards after the 2026-05 FLC T6 incident, in which two Step 1 runs raced on the same project and left an unrecoverable half-saved reconstruction. The "How it works inside" section describes those safeguards: a temp free-space preflight, an exclusive project lock, save-then-verify per project, and a completeness sweep.

Phase 1 stops at a deliberate manual gate. After Step 1 builds the reconstructions, a person opens each project in the Metashape GUI and straightens and crops every model by hand. Phase 2 (`3D_phase2`) does not start until the person completes this manual gate.

## Where it sits in the pipeline

The VICARIUS survey chain runs bag_metashape_export -> 3D_phase1 -> 3D_phase2 -> reef_point_seg. This module is the first Metashape stage in that chain.

Upstream, 3D_phase1 consumes raw transect video captured in the field, or a directory of pre-extracted frame directories. No VICARIUS module produces the raw video; field capture supplies it. The frame-directory input flavor is the seam where an upstream frame-export step (for example bag_metashape_export) plugs in: if frames already exist, the module skips its own extraction and goes straight to reconstruction.

Downstream, the direct consumer is 3D_phase2. Phase 2 takes the Phase 1 project workspace as its input directory verbatim, auto-scales each chunk (the reconstruction of one model inside a Metashape project) against its coded targets, and exports scaled models and orthomosaics. Those Phase 2 exports feed the later reef point segmentation work. The handoff between the two phases is the project workspace plus the shared `status_{PROJECT_NAME}.csv`: Phase 1 writes the Step 0 and Step 1 columns, and Phase 2 reads them and writes the Step 2 and Step 3 columns.

## Inputs

The runner accepts one of two input flavors and auto-detects which one it received by scanning the input directory. Provide exactly one flavor; the run requires one of the two.

| Input | Type | Formats | Required | What it is |
|-------|------|---------|----------|------------|
| video_files | directory | `.mov`, `.mp4`, `.mkv`, `.avi` | one of the two | A directory of source transect videos. Detecting videos runs Step 0 (frame extraction) followed by Step 1 (reconstruction). |
| frame_directories | directory | `.jpg`, `.jpeg`, `.tiff`, `.tif`, `.png` | one of the two | A directory whose immediate subdirectories each hold the frames for one model. Detecting frame subdirectories runs Step 1 only and skips Step 0. |

How the runner handles the two flavors on disk:

- Source videos are read-only. By default the runner symlinks each video into the project workspace. Passing `--copy-videos`, or choosing copy at the interactive prompt, copies them instead (with rsync and parallel workers). The runner never modifies the originals.
- The runner always copies frame directories into the workspace and never symlinks them, so the on-disk structure stays consistent.

### Naming convention

Both flavors use the same name pattern:

    {PROJECTTYPE}{YYYYMMDD}_3D_{SITE}_{REPLICATE}[_n][_PROXY]

- `PROJECTTYPE`: one of `TCRMP`, `RBTEST`, `RBMAPPING`, `HYDRUSTEST`, `HYDRUSMAPPING`, `HYDRUSTCRMP`, `MISC`.
- `YYYYMMDD`: eight-digit capture date.
- `SITE`: a site identifier such as `BWR`, `FLC`, or `DOCK`.
- `REPLICATE`: one of `T{n}`, `TRY{n}`, `RUN{n}`.
- `_n`: an optional multipart suffix. The runner groups parts that share a base name and combines their frames into one continuously-numbered model.
- `_PROXY`: an optional encoding byproduct, for example an iOS proxy H.264 copy sitting next to a ProRes original. The runner strips it before parsing.

Two examples show the grouping. `TCRMP20241014_3D_BWR_T2.mov` is one video and one model. `RBTEST20250301_3D_DOCK_TRY1_1.mp4` plus `RBTEST20250301_3D_DOCK_TRY1_2.mp4` merge into one model, `RBTEST20250301_3D_DOCK_TRY1`, with the two parts extracted in proportion to their durations.

A name that does not parse triggers a warning and a `Continue anyway? (y/n)` prompt. Continuing makes each unparseable item its own model with `base_id` set to the full filename stem, which the tracking CSV and Phase 2 do not expect. Rename the source upstream instead.

## Parameters

The VICARIUS form exposes a curated four-field parameter set (`ui.params_writethrough: true`). Each field writes through to a dotted key in `<project>/analysis_params.yaml` when the run launches. On the CLI the same four fields take a repeatable `--param key=value` flag (for example `--param frames_per_transect=100`), which writes through the same way. The "What it controls" column is the tooltip an operator sees in the UI.

| Parameter | Type | Default | What it controls |
|-----------|------|---------|------------------|
| frames_per_transect | integer | 1000 | Number of frames to extract per video/transect. Written to `processing.frames_per_transect`. Use 100 for a fast smoke test and 1000 to 1200 for production. A higher value improves transect coverage and lengthens alignment time. |
| chunk_size | integer | 1000 | Maximum frames per Metashape chunk. Written to `processing.chunk_size`. |
| max_chunks_per_psx | integer | 4 | Maximum chunks packed into one PSX file for batch processing. Written to `processing.max_chunks_per_psx`. Bounds per-file memory in the GUI. |
| use_gpu | boolean | true | Enable GPU acceleration for Metashape processing. Written to `processing.use_gpu`. Turn off only for debugging. Alignment on the CPU runs slower than on the GPU. |

Every other tuning knob lives in `<project>/analysis_params.yaml` under `processing.*`: the Metashape alignment, depth-map, mesh, UV, and texture defaults, the chunk-management settings, and the Phase 2 model-processing and export blocks. Edit that file directly when you need those knobs. The bare-shell runner can open it in vim before processing unless you pass `--skip-vim`.

## Outputs and data-dictionary entries

Every output lands under the project workspace chosen at launch (`$PROJECT_DIR`, referred to below as `${project_dir}`). Four datasets are catalogued in the data dictionary.

| Output dataset | Path template | Descriptor | What it is |
|----------------|---------------|------------|------------|
| 3D_phase1_extracted_frames | `${project_dir}/processing/frames/{MODEL_ID}/` | [3D_phase1_extracted_frames](../../../_METADATA/dictionary/datasets/3D_phase1_extracted_frames.yaml) | One image directory per model, holding the still frames Step 0 extracts from the video. Each frame is a 16-bit RGB TIFF (FFmpeg `-c:v tiff -pix_fmt rgb48le`), named `{MODEL_ID}_{NNNNN}.tiff` with a 1-based five-digit index. A run holds about `frames_per_transect` frames per directory. |
| 3D_phase1_psx_files | `${project_dir}/processing/psxraw/psx_{BATCH}_{YYYYMMDD}.psx` | [3D_phase1_psx_files](../../../_METADATA/dictionary/datasets/3D_phase1_psx_files.yaml) | The draft Metashape projects Step 1 writes, each carrying up to `max_chunks_per_psx` per-transect chunks (aligned cameras and tie points, depth maps, and a textured mesh). A Metashape project is the small `.psx` XML pointer plus its sibling `.psx.files/` bundle. |
| 3D_phase1_step1_reports | `${project_dir}/processing/reportsraw/{MODEL_ID}_step1.pdf` | [3D_phase1_step1_reports](../../../_METADATA/dictionary/datasets/3D_phase1_step1_reports.yaml) | One Metashape processing report per model. Step 1 exports it with `chunk.exportReport` right after it builds and saves the model. The PDF summarizes survey data, camera calibration, the DEM (digital elevation model), and the processing parameters for the alignment and initial reconstruction. |
| 3D_phase1_tracking_csv | `${project_dir}/status_{PROJECT_NAME}.csv` | [3D_phase1_tracking_csv](../../../_METADATA/dictionary/datasets/3D_phase1_tracking_csv.yaml) | The per-project status ledger, one header row and one data row per model. `PROJECT_NAME` is the workspace directory name. |

### PSX filename

The PSX filename carries the Step 1 run date. The project name does not appear in it. In the normal multi-chunk case the file is `psx_{BATCH}_{YYYYMMDD}.psx`, for example `psx_1_20260421.psx`. In the single-model special case where a batch holds one model and `max_chunks_per_psx == 1`, the file is `{MODEL_ID}_{YYYYMMDD}.psx`. `{YYYYMMDD}` is the run date of Step 1, computed from `datetime.now().strftime("%Y%m%d")`. Step 1 packs chunks up to `max_chunks_per_psx` per file to bound memory.

### Tracking CSV

`status_{PROJECT_NAME}.csv` has 40 fixed columns, hard-coded as the `headers` list in `src/config.py`, and one row per model. The column set is shared with Phase 2. Phase 1 writes only the Step 0 columns (extraction stats, source video paths, timings) and the Step 1 columns (alignment counts, PSX path, report path, timings, completion flags). On a failure, the failing step records its error timestamp in `Step 0 error time` or `Step 1 error time`. Phase 2 writes the Step 2 and Step 3 columns later, plus `Scale`, `Scale Error (m)`, and `Cameras Removed`; those columns stay blank until then. Phase 1 records timings in the machine local time (AST), 24-hour.

### Workspace layout

    ${project_dir}/
      analysis_params.yaml                 # seeded from the module template, then overlaid with the four UI params
      status_{PROJECT_NAME}.csv            # tracking CSV
      .processing.lock                     # exclusive step lock (see safeguards)
      .venv/                               # Python 3.9 venv for Step 0
      video_source/                        # symlinked or copied source videos (video input only)
      processing/
        frames/{MODEL_ID}/                 # 16-bit TIFF frames (Step 0)
          {MODEL_ID}_00001.tiff
          {MODEL_ID}_00002.tiff
          ...
        psxraw/                            # draft Metashape projects (Step 1)
          psx_1_{YYYYMMDD}.psx
          ...
        reportsraw/                        # per-model PDF reports (Step 1)
          {MODEL_ID}_step1.pdf
      output/
        logs/                              # Step 0 and Step 1 run logs

The runner writes the VICARIUS provenance run folder for the launch under the module directory at `vicarius/modules/3D_phase1/inprocess/run_{YYYYMMDD_HHMMSS}/`, separate from the project workspace, with `outputs/project` symlinked back to the workspace, a `logs/` directory, and a run-provenance `analysis_params.yaml`.

## How to run it

Launch this module through VICARIUS. Do not call `step0.py` or `step1.py` directly.

### From the UI

1. Open the 3D_phase1 module page from the module launcher.
2. The module page offers only "Run in terminal" because `module.yaml` sets `ui.run_mode: terminal`. The module hides the in-page streaming run on purpose, because a multi-hour Metashape run should not stay tied to a browser tab.
3. Fill the fields:
   - "Video or frame source directory": the read-only input directory of videos or frame subfolders.
   - "Project workspace directory": the workspace to create or reuse. This maps to the `--project` argument of the script, so it is the project root itself. The module writes all outputs inside this workspace.
   - Run purpose: why you are running this.
   - The four parameters above.
4. Click "Run in terminal".
5. Use Pause/Resume when needed (`ui.supports_pause: true`). Pause finishes the current model, saves, releases the GPU and the project lock, and stops cleanly. Resume relaunches the module and the idempotent steps continue.

The form runs the script non-interactively. The runner maps the form fields to CLI flags via `cli.flag_map` so the script never prompts: input to `--input`, project to `--project`, purpose to `--purpose`, plus `--yes` to skip the summary confirmation and `--skip-vim` to leave the params file unopened.

### From the CLI

Run through the `vicarius` launcher:

    vicarius run 3D_phase1 \
      --input  /mnt/nas/tcrmp/20241014_FLC/videos \
      --output /mnt/nas/tcrmp/20241014_FLC/phase1 \
      --purpose "FLC Oct 2024 3D phase 1 pilot" \
      --param  frames_per_transect=1000

The launcher forwards the input, output, and purpose to the module, and writes each `--param key=value` through to `processing.<key>` in the `analysis_params.yaml` of the project. The four curated parameters take this route: `frames_per_transect`, `chunk_size`, `max_chunks_per_psx`, and `use_gpu`. Repeat `--param` once per value. Use `vicarius restart 3D_phase1` to relaunch, for example after a pause or after pointing `TMPDIR` at a larger volume.

A bare-shell fallback exists for development and recovery. Reserve it for those cases and launch normal runs through VICARIUS:

    python src/run_phase1.py \
      --input /path/to/source --project /path/to/workspace \
      --purpose "recovery run" --yes --skip-vim

With no flags, `python src/run_phase1.py` prompts interactively for the input folder, the project directory, the video transfer mode, the run purpose, and, if Metashape is not found, its executable path.

## How it works inside

`src/run_phase1.py` is the orchestrator. In order, it detects the Metashape executable, detects whether the input is video or frame directories, validates and groups the model names, creates a VICARIUS provenance run, sets up the project workspace (creates the `.venv`, symlinks or copies the inputs, and seeds `analysis_params.yaml`), optionally opens the params file in vim, runs Step 0 for video input, runs Step 1, and prints the manual-step instructions. It translates a step exit code of 42 into a clean pause and marks the run as paused.

Step 0 (`src/step0.py`) runs under the project `.venv` that the runner builds from system Python 3.9. It probes each video with OpenCV to read the frame count, FPS, and duration, computes the extraction rate as `frames_per_transect / duration`, and calls FFmpeg to write 16-bit RGB TIFF frames (`-c:v tiff -pix_fmt rgb48le`). On Linux it attempts NVIDIA CUDA hardware decode first and falls back to software decode on failure, logging a warning. For a multipart model, it extracts each part in proportion to its duration into one continuously-numbered sequence. It skips any transect already marked `Step 0 complete=True` in the tracking CSV.

Step 1 (`src/step1.py`) runs under the bundled Python of Metashape. The runner launches it as `metashape -r step1.py <project>` with `PYTHONPATH` pointed at the `.venv` site-packages so PyYAML and pandas import. It groups the unprocessed models into batches of `max_chunks_per_psx` and processes each batch in isolation: it opens one document, and for each model it adds the frames, matches and aligns cameras, filters tie points and optimizes cameras, builds depth maps, builds and smooths the mesh, builds UVs and the texture, exports the report, and saves the PSX. The document is closed before the next batch begins.

External software and hardware: Agisoft Metashape Pro 2.1.1 or newer, FFmpeg (any version), and system Python 3.9. The module requires a GPU (`runtime.gpu_required: true`), tested on Ubuntu 22.04 and 24.04. Expect 2 to 24 hours per run.

Step 1 carries the safeguards added in 0.2.0 after the 2026-05 FLC T6 incident:

- Temp free-space preflight. `check_temp_free_space()` prevents Step 1 from starting if `TMPDIR` (or `/tmp`) has less than `STEP1_MIN_TEMP_FREE_GB` free, default 50 GB. Metashape spills `depth_maps_pyramids` intermediates there, and running out mid-build was the original trigger.
- Exclusive project lock. `acquire_project_lock("step1")` takes an exclusive `fcntl.flock` on `<project>/.processing.lock`, stamped with the PID, step name, hostname, and start time. Phase 2 takes the same lock. A second 3D step for the same project cannot take the lock, so it stops and prints the current holder.
- Save-then-verify per model. A model flips to `Step 1 complete=True` only after `doc.save()` succeeds and a throwaway `Metashape.Document` re-opens the PSX and confirms the chunk exists with a model and at least one texture. A failed verification leaves the row `False` with an explanatory status so a later run repicks it.
- Raise on missing model or texture. A missing model after `buildModel`, or a missing texture after `buildTexture`, raises `RuntimeError`, so Step 1 marks the chunk failed and does not record it as complete.
- Post-batch completeness sweep. After all batches, Step 1 re-verifies every row that claims `Step 1 complete=True` against the PSX on disk and resets any drift to `Step 1 complete=False` with `Status="Step 1 sweep failed"` before Phase 2 can start.

After Step 1, the operator opens each PSX in the Metashape GUI and, per chunk, straightens the model, rotates the region to view, crops to the model area, and confirms the coded targets and scale bars are visible. Phase 2 cannot start until the operator finishes this manual work.

## Gotchas and troubleshooting

- Metashape version. The module requires Agisoft Metashape Pro 2.1.1 or newer, since older versions have an incompatible Python API. The default search path is `/home/bizon/applications/metashape-pro_2_2_2_amd64/metashape-pro/metashape`. Override it with the `METASHAPE_PATH` environment variable, or let the bare-shell runner prompt for it.
- Python 3.9. Step 0 needs system Python 3.9 to build the project `.venv`. Install it with `sudo apt install python3.9 python3.9-venv python3.9-dev`.
- FFmpeg decode. Best throughput needs FFmpeg with NVIDIA hardware decode. The CPU-only fallback works and runs slower than hardware decode.
- Temp space. If `/tmp` is small, point `TMPDIR` at a larger volume before launching, for example `TMPDIR=/mnt/scratch/metashape_tmp vicarius restart 3D_phase1`. The preflight blocks the launch below 50 GB free; raise or lower the bar with `STEP1_MIN_TEMP_FREE_GB`.
- Project lock. If a run reports that another 3D step is already running, a live run holds the lock. Only delete `<project>/.processing.lock` when you are certain no run is active.
- Unparseable names. Names that do not match the pattern warn and prompt to continue. Continuing produces a `base_id` that Phase 2 does not expect, so rename the source instead.
- Presets. The module wires no presets. The `presets/lightroom/` and `presets/metashape/` directories exist but are empty, and the UI has no preset surface.
- The Metashape default path above is a hardcoded machine path. The `analysis_params.yaml` template also carries a Sketchfab token under the deprecated Phase 2 `final_exports` block; Phase 1 never uses it.

## Resume from cold

A person with no prior context can set up, run, and verify this module from this section alone.

### Environment

Step 0 and Step 1 use two different interpreters.

- Step 0 runs under the project `.venv`, built from system Python 3.9. Install `python3.9`, `python3.9-venv`, and `python3.9-dev` first. The runner creates `<project>/.venv` and installs `requirements.txt` into it (PyYAML, pandas, NumPy, opencv-python, plus matplotlib and pillow). Step 0 also shells out to FFmpeg, so FFmpeg must be on `PATH`; Step 0 prefers NVIDIA hardware decode and uses CPU decode as the fallback.
- Step 1 runs under the bundled Python of Agisoft Metashape Pro. The runner invokes it as `metashape -r src/step1.py <project>`. Metashape Pro is commercial software sold by Agisoft (agisoft.com), and Step 1 launches it as an external program. The module never downloads, installs, or activates Metashape. `detect_metashape()` only locates a copy that is already installed and license-activated on the machine, in this order: the `METASHAPE_PATH` environment variable, then the built-in default path `/home/bizon/applications/metashape-pro_2_2_2_amd64/metashape-pro/metashape`, then `metashape` on `PATH`, then an interactive prompt for the path. When it finds no executable it raises `Metashape Pro not found`, and Step 1 never starts.
- Set Metashape up before the first run by doing three things. First, obtain a Metashape Professional license from Agisoft. Second, install Metashape Pro version 2.1.1 or newer, because versions older than 2.1.1 use an incompatible Python API and Step 1 fails on them. Third, activate the license in the Metashape GUI or with the activation tool from Agisoft so the program launches without a license prompt. Then leave the executable at the built-in default path above, point `METASHAPE_PATH` at it, or put it on `PATH`. If Metashape starts without an active license it exits non-zero, and `run_step1` reports that exit as a failed run. The module requires a working GPU. An inherited lab machine that already has an activated Metashape at the default path needs none of these steps.

### Inputs and outputs

The input is a read-only directory of either transect videos (`.mov/.mp4/.mkv/.avi`) or subfolders of pre-extracted frames, named per the convention above. The output is the project workspace directory you choose at launch. Everything the module writes lands under that workspace, laid out as shown in "Workspace layout": `analysis_params.yaml`, `status_{PROJECT_NAME}.csv`, `.venv/`, `video_source/`, `processing/frames/{MODEL_ID}/`, `processing/psxraw/`, `processing/reportsraw/`, and `output/logs/`. The runner writes the provenance run folder under `vicarius/modules/3D_phase1/inprocess/run_{YYYYMMDD_HHMMSS}/`.

### Run

Use the VICARIUS launcher, either the UI "Run in terminal" button or the CLI. The four curated parameters (`frames_per_transect`, `chunk_size`, `max_chunks_per_psx`, `use_gpu`) have no dedicated CLI flag of their own. On the CLI you set each one with a repeatable `--param key=value` argument, and the launcher writes the value through to `processing.<key>` in `<project>/analysis_params.yaml`, the file `src/config.py` reads at run time. The UI form fields set the same four values.

For a fast smoke test, extract 100 frames per transect, below the default of 1000:

    vicarius run 3D_phase1 \
      --input  /path/to/source \
      --output /path/to/workspace \
      --purpose "why you are running this" \
      --param  frames_per_transect=100

For production, raise it to 1000 to 1200 with `--param frames_per_transect=1200`. Set any of the other three the same way, for example `--param use_gpu=false` or `--param max_chunks_per_psx=2`, and repeat `--param` once per value. The bare-shell `src/run_phase1.py` runner accepts no `--param`; it opens `analysis_params.yaml` in vim before processing so you edit `processing.frames_per_transect` there directly, unless you pass `--skip-vim`.

### Verify success

The run succeeded when all of these hold:

1. The terminal prints the `PHASE 1 COMPLETE - MANUAL STEP REQUIRED` banner and exits with code 0.
2. In `status_{PROJECT_NAME}.csv`, every model row shows `Step 1 complete` set to `True` and `Status` set to `Step 1 complete`. No row shows `Step 1 save verification failed` or `Step 1 sweep failed`.
3. `processing/psxraw/` holds the `psx_{BATCH}_{YYYYMMDD}.psx` projects, and `processing/reportsraw/` holds one `{MODEL_ID}_step1.pdf` per model.

If the run was paused, it exits with code 42 and records the run as paused. Relaunch the same module on the same project to resume: Step 0 skips transects already marked `Step 0 complete=True`, and Step 1 skips models already marked `Step 1 complete=True`, so both steps are idempotent at the transect and model level.

Phase 1 is complete only after the manual gate. Open each PSX in the Metashape GUI, straighten and crop every model, confirm the coded targets and scale bars are visible, and save. Only then run `3D_phase2` on the same workspace.

## Provenance and links

- Repo: `vicarius/modules/3D_phase1/github_repo`. Upstream project: https://github.com/laurenkolinger/3D_vicarius.
- Related studies: S2_3D_structure.
- Related modules: `3D_phase2` (Phase 2 scaling and export, consumes this workspace), and the downstream reef point segmentation work in the survey chain.
- Related docs: the 2026-05 FLC T6 incident write-up at `vicarius/_DOCS/archive/incidents/INCIDENT_2026-05_parallel_step1_flc_t6.md`, the module system overview at `vicarius/_DOCS/MODULE_REGISTRY_GUIDE.md`, and the top-level catalog at `vicarius/modules/MODULE_REGISTRY.md`.
- Data-dictionary descriptors:
  - [3D_phase1_extracted_frames](../../../_METADATA/dictionary/datasets/3D_phase1_extracted_frames.yaml)
  - [3D_phase1_psx_files](../../../_METADATA/dictionary/datasets/3D_phase1_psx_files.yaml)
  - [3D_phase1_step1_reports](../../../_METADATA/dictionary/datasets/3D_phase1_step1_reports.yaml)
  - [3D_phase1_tracking_csv](../../../_METADATA/dictionary/datasets/3D_phase1_tracking_csv.yaml)

## Changelog

- v0.1.0 (2026-02-13): Initial VICARIUS integration, split from `3D_vicarius`.
- v0.2.0 (2026-05-15): Added the temp free-space preflight, exclusive project lock, save-then-verify per PSX, raise-on-missing-model/texture, and post-batch completeness sweep, driven by the 2026-05 FLC T6 incident.
