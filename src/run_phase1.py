#!/usr/bin/env python3
"""
run_phase1.py - Interactive CLI runner for 3D Phase 1 processing.

Handles setup, input detection, naming validation, environment setup,
frame extraction (step0), and initial 3D processing (step1).

Usage:
    python src/run_phase1.py
    python src/run_phase1.py --input /path/to/input --project /path/to/project
"""

import argparse
import concurrent.futures
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from naming import (
    parse_model_name,
    group_multipart,
    strip_proxy,
    check_unknown_values,
    KNOWN_PROJECT_TYPES,
)

# ---------------------------------------------------------------------------
# VICARIUS logging integration
# ---------------------------------------------------------------------------
VICARIUS_ROOT = os.environ.get("VICARIUS_ROOT", "/mnt/vicarius_drive/vicarius")
sys.path.insert(0, os.path.join(VICARIUS_ROOT, "_logging", "src"))
try:
    from vicarius_log import get_log

    VICARIUS_LOGGING = True
except ImportError:
    VICARIUS_LOGGING = False

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SRC_DIR = Path(__file__).resolve().parent  # github_repo/src/
GITHUB_REPO_DIR = SRC_DIR.parent  # github_repo/
MODULE_DIR = GITHUB_REPO_DIR.parent  # modules/3D_phase1/
TEMPLATE_PARAMS = GITHUB_REPO_DIR / "analysis_params.yaml"

# Metashape detection paths (ordered)
METASHAPE_SEARCH_PATHS = [
    "/home/bizon/applications/metashape-pro_2_2_2_amd64/metashape-pro/metashape",
]

VIDEO_EXTENSIONS = {".mov", ".mp4", ".mkv", ".avi"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".tiff", ".tif", ".png"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def fast_copy_file(src: Path, dst: Path) -> None:
    """Copy a file using rsync (with progress) or fall back to cp.

    rsync is typically faster than shutil.copy2 for large files and
    supports resume on interruption.

    Args:
        src: Source file path.
        dst: Destination file path.
    """
    rsync = shutil.which("rsync")
    if rsync:
        subprocess.run(
            [rsync, "-ah", "--progress", "--partial", str(src), str(dst)],
            check=True,
        )
    else:
        # Fall back to cp which is still faster than shutil for large files
        subprocess.run(["cp", "--preserve=timestamps", str(src), str(dst)], check=True)


def parallel_copy_files(file_pairs: list, max_workers: int = 4) -> None:
    """Copy multiple files in parallel using fast_copy_file.

    Args:
        file_pairs: List of (src_path, dst_path) tuples.
        max_workers: Maximum concurrent copy operations.
    """
    if len(file_pairs) == 1:
        src, dst = file_pairs[0]
        print(f"    Copying {src.name}...")
        fast_copy_file(src, dst)
        return

    workers = min(max_workers, len(file_pairs))
    print(f"    Copying {len(file_pairs)} files ({workers} parallel workers)...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for src, dst in file_pairs:
            futures[executor.submit(fast_copy_file, src, dst)] = src.name
        for future in concurrent.futures.as_completed(futures):
            name = futures[future]
            try:
                future.result()
                print(f"    Copied {name}")
            except Exception as exc:
                print(f"    ERROR copying {name}: {exc}")
                raise


def banner(text: str) -> None:
    """Print a section banner."""
    print()
    print("=" * 60)
    print(f"  {text}")
    print("=" * 60)


def detect_metashape() -> str:
    """Find the Metashape executable. Returns path or raises RuntimeError."""
    # 1. Environment variable override
    env_path = os.environ.get("METASHAPE_PATH")
    if env_path and os.path.isfile(env_path) and os.access(env_path, os.X_OK):
        return env_path

    # 2. Known paths
    for path in METASHAPE_SEARCH_PATHS:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    # 3. Search $PATH
    found = shutil.which("metashape")
    if found:
        return found

    # 4. Prompt user
    print("\nMetashape Pro executable not found automatically.")
    print("Set $METASHAPE_PATH or provide the path below.")
    user_path = input("Metashape executable path: ").strip().strip("'\"")
    if user_path and os.path.isfile(user_path):
        return user_path

    raise RuntimeError(
        "Metashape Pro not found. Install it or set $METASHAPE_PATH."
    )


def detect_input_type(input_path: Path) -> str:
    """Determine if input folder contains video files or frame directories.

    Returns 'video' or 'frames'.
    """
    # Check for video files
    videos = [
        f
        for f in input_path.iterdir()
        if f.is_file()
        and f.suffix.lower() in VIDEO_EXTENSIONS
        and not f.name.startswith("._")
    ]
    if videos:
        return "video"

    # Check for subdirectories containing images
    for subdir in input_path.iterdir():
        if subdir.is_dir():
            images = [
                f for f in subdir.iterdir() if f.suffix.lower() in IMAGE_EXTENSIONS
            ]
            if images:
                return "frames"

    raise ValueError(
        f"No video files or frame directories found in {input_path}\n"
        f"Expected: video files ({', '.join(VIDEO_EXTENSIONS)}) "
        f"or subdirectories of images ({', '.join(IMAGE_EXTENSIONS)})"
    )


def collect_input_names(input_path: Path, input_type: str) -> list:
    """Collect filenames or directory names from the input path."""
    if input_type == "video":
        return [
            f.name
            for f in sorted(input_path.iterdir())
            if f.is_file()
            and f.suffix.lower() in VIDEO_EXTENSIONS
            and not f.name.startswith("._")
        ]
    else:
        return [
            d.name
            for d in sorted(input_path.iterdir())
            if d.is_dir() and not d.name.startswith(".")
        ]


def validate_and_group(names: list, input_type: str) -> dict:
    """Validate naming conventions and group multipart items.

    Prints warnings for non-conforming names and unknown project types.
    Returns grouped dict from group_multipart().
    """
    # Check for unknown project types
    unknowns = check_unknown_values(names)
    if unknowns["project_types"]:
        print(f"\n  WARNING: Unknown project type(s): {', '.join(unknowns['project_types'])}")
        for pt in unknowns["project_types"]:
            definition = input(f"  Define '{pt}' (or press Enter to accept): ").strip()
            if definition:
                print(f"  Noted: {pt} = {definition}")

    # Check for names that don't match the pattern at all
    bad_names = []
    for name in names:
        stem = name.rsplit(".", 1)[0] if "." in name else name
        clean = strip_proxy(stem)
        if parse_model_name(clean) is None:
            bad_names.append(name)

    if bad_names:
        print(f"\n  WARNING: {len(bad_names)} name(s) don't match expected pattern:")
        print(f"  Pattern: {{PROJECTTYPE}}{{YYYYMMDD}}_3D_{{SITE}}_{{REPLICATE}}[_n][_PROXY]")
        for bn in bad_names[:10]:
            print(f"    - {bn}")
        resp = input("\n  Continue anyway? (y/n): ").strip().lower()
        if resp != "y":
            print("Aborted.")
            sys.exit(1)

    groups = group_multipart(names)
    return groups


def prompt_inputs() -> tuple:
    """Interactive prompts for input_path and project_dir."""
    banner("3D Phase 1 - Setup + Frame Extraction + Initial 3D Processing")
    print()
    print("This tool will guide you through:")
    print("  1. Detecting and validating your input files")
    print("  2. Setting up the project directory structure")
    print("  3. Running frame extraction (if video input)")
    print("  4. Running initial 3D processing in Metashape")
    print()

    input_path = input("Input folder path (videos OR frame folders): ").strip().strip("'\"")
    if not input_path:
        print("Error: Input path required.")
        sys.exit(1)

    project_dir = input("Project directory path (will be created if needed): ").strip().strip("'\"")
    if not project_dir:
        print("Error: Project directory required.")
        sys.exit(1)

    return Path(input_path).expanduser().resolve(), Path(project_dir).expanduser().resolve()


def create_venv(project_dir: Path) -> None:
    """Create Python 3.9 venv and install requirements."""
    venv_dir = project_dir / ".venv"
    if venv_dir.exists():
        print("  Virtual environment already exists, skipping creation.")
        return

    python39 = shutil.which("python3.9")
    if not python39:
        raise RuntimeError(
            "python3.9 not found on PATH.\n"
            "Install with: sudo apt install python3.9 python3.9-venv python3.9-dev"
        )

    print(f"  Creating venv with {python39}...")
    subprocess.run([python39, "-m", "venv", str(venv_dir)], check=True)

    requirements_file = GITHUB_REPO_DIR / "requirements.txt"
    pip = venv_dir / "bin" / "pip"

    print("  Installing requirements...")
    subprocess.run([str(pip), "install", "-r", str(requirements_file)], check=True)


def setup_project(
    project_dir: Path,
    input_path: Path,
    input_type: str,
    model_groups: dict,
    copy_frames: bool = False,
    copy_videos: bool = False,
) -> None:
    """Create project directory structure and populate with inputs.

    Args:
        project_dir: Root project directory to create.
        input_path: Path containing source videos or frame directories.
        input_type: Either 'video' or 'frames'.
        model_groups: Dict from validate_and_group().
        copy_frames: If True, copy frame dirs instead of symlinking.
        copy_videos: If True, copy videos instead of symlinking.
    """
    banner("PROJECT SETUP")

    # Create directories
    print("  Creating directory structure...")
    for subdir in ["video_source", "processing", "output"]:
        (project_dir / subdir).mkdir(parents=True, exist_ok=True)

    # Create venv
    print("  Setting up Python environment...")
    create_venv(project_dir)

    # Copy analysis_params.yaml
    params_dst = project_dir / "analysis_params.yaml"
    if not params_dst.exists():
        shutil.copy2(str(TEMPLATE_PARAMS), str(params_dst))
        print(f"  Copied analysis_params.yaml to {project_dir}")
    else:
        print(f"  analysis_params.yaml already exists, keeping existing.")

    # Copy or link input files
    if input_type == "video":
        video_source = project_dir / "video_source"
        if copy_videos:
            print("  Copying video files to video_source/ (rsync)...")
            copy_pairs = []
            for base_id, parts in model_groups.items():
                for part_info in parts:
                    src_file = input_path / part_info["original_name"]
                    if src_file.exists():
                        dst_file = video_source / part_info["original_name"]
                        if not dst_file.exists():
                            copy_pairs.append((src_file, dst_file))
                        else:
                            print(f"    {part_info['original_name']} already exists, skipping.")
                    else:
                        print(f"    WARNING: {src_file} not found, skipping.")
            if copy_pairs:
                parallel_copy_files(copy_pairs)
        else:
            print("  Symlinking video files to video_source/...")
            for base_id, parts in model_groups.items():
                for part_info in parts:
                    src_file = input_path / part_info["original_name"]
                    if src_file.exists():
                        dst_file = video_source / part_info["original_name"]
                        if not dst_file.exists():
                            print(f"    Linking {part_info['original_name']}...")
                            os.symlink(str(src_file.resolve()), str(dst_file))
                        else:
                            print(f"    {part_info['original_name']} already exists, skipping.")
                    else:
                        print(f"    WARNING: {src_file} not found, skipping.")
    else:
        frames_dir = project_dir / "processing" / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        action = "Copying" if copy_frames else "Linking"
        print(f"  {action} frame directories to processing/frames/...")
        for subdir in sorted(input_path.iterdir()):
            if subdir.is_dir() and not subdir.name.startswith("."):
                dst = frames_dir / subdir.name
                if not dst.exists():
                    if copy_frames:
                        print(f"    Copying {subdir.name}/...")
                        shutil.copytree(str(subdir), str(dst))
                    else:
                        print(f"    Linking {subdir.name}/...")
                        os.symlink(str(subdir.resolve()), str(dst))
                else:
                    print(f"    {subdir.name} already exists, skipping.")

    print("  Project setup complete.")


def open_params_for_editing(project_dir: Path) -> None:
    """Open analysis_params.yaml in vim with instructions."""
    params_file = project_dir / "analysis_params.yaml"

    banner("EDIT ANALYSIS PARAMETERS")
    print()
    print(f"  File: {params_file}")
    print()
    print("  Key settings to review/edit:")
    print("    processing.frames_per_transect  (default: 1000)")
    print("    processing.chunk_size           (default: 1000)")
    print("    processing.use_gpu              (default: true)")
    print("    processing.max_chunks_per_psx   (default: 4)")
    print("    model_processing.scale_bars     (set your scale bar markers/distances)")
    print()
    print("  Vim quick reference:")
    print("    i          - Enter insert mode (to edit text)")
    print("    Esc        - Exit insert mode")
    print("    :wq Enter  - Save and quit")
    print("    :q! Enter  - Quit without saving")
    print("    /text      - Search for 'text'")
    print()

    input("  Press Enter to open in vim...")
    subprocess.run(["vim", str(params_file)])


def run_step0(project_dir: Path) -> None:
    """Run frame extraction (step 0)."""
    banner("STEP 0: Frame Extraction")
    print()

    venv_python = project_dir / ".venv" / "bin" / "python"
    step0_script = SRC_DIR / "step0.py"

    cmd = [str(venv_python), str(step0_script), str(project_dir)]
    print(f"  Running: {' '.join(cmd)}")
    print()

    process = subprocess.run(cmd, cwd=str(GITHUB_REPO_DIR))

    if process.returncode != 0:
        raise RuntimeError(f"Step 0 failed with return code {process.returncode}")

    print("\n  Step 0 complete.")


def run_step1(project_dir: Path, metashape_path: str) -> None:
    """Run initial 3D processing via Metashape (step 1)."""
    banner("STEP 1: Initial 3D Processing (Metashape)")
    print()

    venv_packages = project_dir / ".venv" / "lib" / "python3.9" / "site-packages"
    step1_script = SRC_DIR / "step1.py"

    env = os.environ.copy()
    env["PYTHONPATH"] = str(venv_packages)

    cmd = [metashape_path, "-r", str(step1_script), str(project_dir)]
    print(f"  Running: {' '.join(cmd)}")
    print()

    process = subprocess.run(cmd, env=env, cwd=str(GITHUB_REPO_DIR))

    if process.returncode != 0:
        raise RuntimeError(f"Step 1 failed with return code {process.returncode}")

    print("\n  Step 1 complete.")


def print_manual_instructions(project_dir: Path) -> None:
    """Print instructions for the manual GUI step."""
    banner("PHASE 1 COMPLETE - MANUAL STEP REQUIRED")
    print()
    print("  Before running Phase 2, you must manually straighten and prepare")
    print("  each model in the Metashape GUI.")
    print()
    print(f"  PSX files location: {project_dir / 'processing' / 'psxraw'}/")
    print()
    print("  For each chunk in each PSX file:")
    print()
    print("  STRAIGHTENING (always required):")
    print("    1. Load the textured model")
    print("    2. Auto-adjust brightness/contrast on an image to improve texture")
    print("    3. Switch to rotate model view")
    print("    4. Rotate the model so it aligns horizontally at the top of the view")
    print("    5. Use 'Model > Region > Rotate Region to View' to set alignment")
    print("    6. Resize the region to crop to the model area (use top XY & side views)")
    print("    7. Use the rectangular crop tool to crop within the region bounds")
    print()
    print("  SCALING PREPARATION:")
    print("    1. Ensure coded targets are visible and properly positioned")
    print("    2. Verify at least 2 scale bars worth of targets are clearly visible")
    print()
    print("  3. Save the project and quit Metashape")
    print()
    print("  When done with all models, run Phase 2 (3D_phase2) for automatic")
    print("  scaling and export.")
    print()
    print("=" * 60)


def create_vicarius_run(purpose: str, study: str) -> Path:
    """Create a VICARIUS run in inprocess/ for metadata tracking."""
    sys.path.insert(0, str(SRC_DIR))
    from init_run import init_run

    run_name = f"run_{time.strftime('%Y%m%d_%H%M%S')}"
    run_dir = init_run(
        run_name=run_name,
        module_path=MODULE_DIR,
        purpose=purpose,
        study=study,
    )
    return run_dir


def main():
    parser = argparse.ArgumentParser(
        description="3D Phase 1: Setup + Frame Extraction + Initial 3D Processing"
    )
    parser.add_argument(
        "--input", "-i", type=Path, help="Input folder path (videos OR frame folders)"
    )
    parser.add_argument(
        "--project", "-p", type=Path, help="Project directory path"
    )
    parser.add_argument(
        "--copy-videos",
        action="store_true",
        help="Copy videos instead of symlinking (default: symlink). "
        "Uses rsync with parallel workers for faster transfers.",
    )
    parser.add_argument(
        "--skip-vim",
        action="store_true",
        help="Skip opening analysis_params.yaml in vim",
    )
    args = parser.parse_args()

    start_time = time.time()

    # ---- Get input/output paths ----
    if args.input and args.project:
        input_path = args.input.expanduser().resolve()
        project_dir = args.project.expanduser().resolve()
    else:
        input_path, project_dir = prompt_inputs()

    # Validate input path exists
    if not input_path.exists() or not input_path.is_dir():
        print(f"Error: Input path does not exist or is not a directory: {input_path}")
        sys.exit(1)

    # ---- Detect Metashape ----
    print("\nDetecting Metashape installation...")
    metashape_path = detect_metashape()
    print(f"  Found: {metashape_path}")

    # ---- Detect input type ----
    print("\nDetecting input type...")
    input_type = detect_input_type(input_path)
    print(f"  Detected: {input_type}")

    if input_type == "video":
        print("  Will run: Step 0 (frame extraction) -> Step 1 (3D processing)")
    else:
        print("  Will run: Step 1 (3D processing) [skipping Step 0]")

    # ---- Ask about video transfer mode (interactive only) ----
    # Frames are always copied to maintain consistent filesystem structure.
    copy_videos = args.copy_videos
    copy_frames = True
    if input_type == "video" and not args.copy_videos:
        print()
        print("  Video transfer mode:")
        print("    1. Symlink (default) - instant, videos stay in original location")
        print("    2. Copy - slower, but needed if source will be disconnected")
        transfer = input("  Choose [1/2]: ").strip()
        if transfer == "2":
            copy_videos = True
            print("  Will copy videos (rsync, parallel).")
        else:
            print("  Will symlink videos.")

    # ---- Validate names ----
    print("\nValidating naming conventions...")
    names = collect_input_names(input_path, input_type)
    print(f"  Found {len(names)} item(s)")
    model_groups = validate_and_group(names, input_type)
    print(f"  Grouped into {len(model_groups)} model(s):")
    for base_id, parts in model_groups.items():
        if len(parts) > 1:
            print(f"    {base_id} ({len(parts)} parts)")
        else:
            print(f"    {base_id}")

    # ---- VICARIUS: Purpose (Commandment VI) ----
    banner("PURPOSE (Commandment VI)")
    purpose = input("  Why are you running this? ").strip()
    if not purpose:
        purpose = "3D Phase 1 processing"

    # ---- Create VICARIUS run ----
    vicarius_run_dir = None
    try:
        vicarius_run_dir = create_vicarius_run(purpose, "")
        print(f"\n  VICARIUS run created: {vicarius_run_dir.name}")
    except Exception as e:
        print(f"\n  Warning: Could not create VICARIUS run: {e}")
        print("  Continuing without VICARIUS run tracking.")

    # ---- Log process start ----
    start_event = None
    if VICARIUS_LOGGING:
        try:
            log = get_log()
            start_event = log.process_start(
                module="3D_phase1",
                purpose=purpose,
                inputs=[str(input_path)],
            )
        except Exception as e:
            print(f"  Warning: VICARIUS logging failed: {e}")

    # ---- Confirmation ----
    if input_type == "video":
        transfer_mode = "copy (rsync)" if copy_videos else "symlink"
    else:
        transfer_mode = "copy" if copy_frames else "symlink"

    banner("SUMMARY")
    print(f"  Input:       {input_path}")
    print(f"  Input type:  {input_type}")
    print(f"  Transfer:    {transfer_mode}")
    print(f"  Models:      {len(model_groups)}")
    print(f"  Project dir: {project_dir}")
    print(f"  Purpose:     {purpose}")
    print()
    confirm = input("  Proceed? (y/n): ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        sys.exit(0)

    # ---- Setup project ----
    try:
        setup_project(
            project_dir, input_path, input_type, model_groups,
            copy_frames=copy_frames,
            copy_videos=copy_videos,
        )
    except Exception as e:
        print(f"\nError during project setup: {e}")
        sys.exit(1)

    # ---- Symlink project in VICARIUS run ----
    if vicarius_run_dir:
        try:
            outputs_dir = vicarius_run_dir / "outputs"
            project_link = outputs_dir / "project"
            if not project_link.exists():
                os.symlink(str(project_dir), str(project_link))
        except Exception:
            pass  # Non-critical

    # ---- Edit analysis params ----
    if not args.skip_vim:
        open_params_for_editing(project_dir)

    # ---- Run processing ----
    try:
        if input_type == "video":
            run_step0(project_dir)

        run_step1(project_dir, metashape_path)

    except RuntimeError as e:
        elapsed = time.time() - start_time
        print(f"\nERROR: {e}")

        if VICARIUS_LOGGING and start_event:
            try:
                log = get_log()
                log.process_end(
                    module="3D_phase1",
                    status="failed",
                    duration_sec=elapsed,
                    parent_event_id=start_event,
                    notes=str(e),
                )
            except Exception:
                pass

        sys.exit(1)

    # ---- Print manual instructions ----
    print_manual_instructions(project_dir)

    # ---- Log completion ----
    elapsed = time.time() - start_time
    if VICARIUS_LOGGING and start_event:
        try:
            log = get_log()
            log.process_end(
                module="3D_phase1",
                status="success",
                duration_sec=elapsed,
                outputs=[str(project_dir)],
                parent_event_id=start_event,
                notes=f"Processed {len(model_groups)} model(s), input_type={input_type}",
            )
        except Exception:
            pass

    hours = elapsed / 3600
    if hours >= 1:
        print(f"\n  Total runtime: {hours:.1f} hours")
    else:
        minutes = elapsed / 60
        print(f"\n  Total runtime: {minutes:.1f} minutes")


if __name__ == "__main__":
    main()
