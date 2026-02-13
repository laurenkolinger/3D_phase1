#!/usr/bin/env python3
"""
shelve_run.py - Archive a completed processing run

Marks a run as shelved, updates metadata with end dates, and optionally
archives the analysis_params.yaml to a central location for reference.

Usage:
    python shelve_run.py <run_name> [--disposition keep|archive|delete] [--notes "final notes"]

Example:
    python shelve_run.py jan2026_flat_fieldtrip --disposition keep --notes "All 12 transects processed successfully"

Dispositions:
    keep    - Keep run in inprocess/ for now (default)
    archive - Move outputs to processed/, keep params for reference
    delete  - Mark for deletion (manual cleanup required)
"""

import argparse
import getpass
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml


def log_to_vicarius(message: str, category: str = "modules", event_type: str = "user_note"):
    """Log event to VICARIUS logging system if available."""
    vicarius_root = os.environ.get("VICARIUS_ROOT", "/mnt/vicarius_drive/vicarius")
    log_script = Path(vicarius_root) / "_logging" / "src" / "vicarius_cli.py"

    if log_script.exists():
        try:
            subprocess.run(
                ["python3", str(log_script), "note", message],
                capture_output=True,
                check=True,
            )
            return True
        except subprocess.CalledProcessError:
            pass
    return False


def archive_params_to_metadata(run_dir: Path, params: dict) -> Path | None:
    """
    Copy analysis_params.yaml to central metadata archive.

    Archived to: _METADATA/logs/runs/shelved/{module_name}/{run_name}_params.yaml
    """
    vicarius_root = os.environ.get("VICARIUS_ROOT", "/mnt/vicarius_drive/vicarius")
    archive_base = Path(vicarius_root) / "_METADATA" / "logs" / "runs" / "shelved"

    module_name = params.get("run", {}).get("module_name", "unknown_module")
    run_name = params.get("run", {}).get("name", "unknown_run")

    archive_dir = archive_base / module_name
    archive_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d")
    archive_file = archive_dir / f"{run_name}_{timestamp}_params.yaml"

    # Avoid overwriting - add counter if needed
    counter = 1
    while archive_file.exists():
        archive_file = archive_dir / f"{run_name}_{timestamp}_{counter}_params.yaml"
        counter += 1

    with open(archive_file, "w") as f:
        yaml.dump(params, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    return archive_file


def shelve_run(
    run_name: str,
    module_path: Path,
    disposition: str = "keep",
    notes: str = "",
    archive_params: bool = True,
) -> dict:
    """
    Shelve a completed processing run.

    Args:
        run_name: Name of the run folder to shelve
        module_path: Path to the module root
        disposition: What to do with the run ("keep", "archive", "delete")
        notes: Final notes to add to the params file
        archive_params: Whether to copy params to central archive

    Returns:
        dict with shelving results
    """
    inprocess_dir = module_path / "inprocess"
    run_dir = inprocess_dir / run_name
    params_file = run_dir / "analysis_params.yaml"

    # Validate
    if not run_dir.exists():
        raise FileNotFoundError(f"Run folder not found: {run_dir}")
    if not params_file.exists():
        raise FileNotFoundError(f"analysis_params.yaml not found in run: {run_dir}")

    # Load current params
    with open(params_file) as f:
        params = yaml.safe_load(f)

    # Check if already shelved
    if params.get("run", {}).get("status") == "shelved":
        raise ValueError(f"Run '{run_name}' is already shelved")

    # Get timestamps
    now = datetime.now()
    iso_timestamp = now.isoformat()
    iso_date = now.strftime("%Y-%m-%d")

    # Calculate duration
    start_date_str = params.get("temporal", {}).get("start_date", "")
    duration_days = None
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
            duration_days = (now - start_date).days
        except ValueError:
            pass

    # Update params
    params["run"]["status"] = "shelved"
    params["temporal"]["end_date"] = iso_date
    params["temporal"]["duration_days"] = duration_days

    params["shelving"]["shelved_at"] = iso_timestamp
    params["shelving"]["shelved_by"] = getpass.getuser()
    params["shelving"]["disposition"] = disposition
    params["shelving"]["final_notes"] = notes

    results = {
        "run_name": run_name,
        "disposition": disposition,
        "duration_days": duration_days,
        "archive_location": None,
    }

    # Archive params to central location
    if archive_params:
        archive_file = archive_params_to_metadata(run_dir, params)
        if archive_file:
            params["shelving"]["archive_location"] = str(archive_file)
            results["archive_location"] = str(archive_file)

    # Write updated params back
    with open(params_file, "w") as f:
        yaml.dump(params, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    # Log to VICARIUS
    module_name = params.get("run", {}).get("module_name", module_path.name)
    log_message = f"Shelved run '{run_name}' in module '{module_name}' (disposition: {disposition})"
    if duration_days is not None:
        log_message += f" - ran for {duration_days} days"
    if notes:
        log_message += f" - {notes}"
    log_to_vicarius(log_message)

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Archive a completed processing run"
    )
    parser.add_argument("run_name", help="Name of the run folder to shelve")
    parser.add_argument(
        "--disposition",
        "-d",
        choices=["keep", "archive", "delete"],
        default="keep",
        help="What to do with the run (default: keep)",
    )
    parser.add_argument(
        "--notes", "-n", default="", help="Final notes about the run"
    )
    parser.add_argument(
        "--no-archive",
        action="store_true",
        help="Don't copy params to central archive",
    )
    parser.add_argument(
        "--module-path",
        "-m",
        type=Path,
        default=None,
        help="Path to module root (auto-detected if run from within module)",
    )

    args = parser.parse_args()

    # Auto-detect module path if not specified
    if args.module_path is None:
        current = Path.cwd()
        while current != current.parent:
            if (current / "inprocess").exists() and (current / "github_repo").exists():
                args.module_path = current
                break
            if current.name == "github_repo" and (current.parent / "inprocess").exists():
                args.module_path = current.parent
                break
            current = current.parent

        if args.module_path is None:
            print("Error: Could not auto-detect module path.", file=sys.stderr)
            print("Run from within a module directory or specify --module-path", file=sys.stderr)
            sys.exit(1)

    try:
        results = shelve_run(
            run_name=args.run_name,
            module_path=args.module_path,
            disposition=args.disposition,
            notes=args.notes,
            archive_params=not args.no_archive,
        )

        print(f"Shelved run: {args.run_name}")
        print(f"  Disposition: {results['disposition']}")
        if results["duration_days"] is not None:
            print(f"  Duration: {results['duration_days']} days")
        if results["archive_location"]:
            print(f"  Params archived to: {results['archive_location']}")

        print()
        if args.disposition == "keep":
            print("Run files remain in inprocess/ - manually move or delete when ready")
        elif args.disposition == "archive":
            print("Remember to manually move outputs to processed/ directory")
        elif args.disposition == "delete":
            print("Run marked for deletion - manually remove when ready")

    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
