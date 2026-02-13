#!/usr/bin/env python3
"""
init_run.py - Initialize a new processing run with auto-populated metadata

Creates a new run folder in the module's inprocess/ directory with:
- analysis_params.yaml populated with git version, timestamps, and user info
- Proper folder structure ready for processing outputs

Usage:
    python init_run.py <run_name> [--purpose "description"] [--study S1_historical]

Example:
    python init_run.py jan2026_flat_fieldtrip --purpose "Process 12 transects from FLAT"
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


def get_git_info(repo_path: Path) -> dict:
    """Extract git version information from the repository."""
    git_info = {
        "git_commit_hash": "",
        "git_commit_short": "",
        "git_branch": "",
        "git_remote_url": "",
        "git_tag": "",
        "is_clean": False,
    }

    try:
        # Get current commit hash
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        git_info["git_commit_hash"] = result.stdout.strip()
        git_info["git_commit_short"] = git_info["git_commit_hash"][:7]

        # Get branch name
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        git_info["git_branch"] = result.stdout.strip()

        # Get remote URL (if exists)
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            git_info["git_remote_url"] = result.stdout.strip()

        # Get tag if HEAD is tagged
        result = subprocess.run(
            ["git", "describe", "--tags", "--exact-match", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            git_info["git_tag"] = result.stdout.strip()

        # Check if working directory is clean
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        git_info["is_clean"] = len(result.stdout.strip()) == 0

    except subprocess.CalledProcessError as e:
        print(f"Warning: Could not get git info: {e}", file=sys.stderr)
    except FileNotFoundError:
        print("Warning: git not found in PATH", file=sys.stderr)

    return git_info


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


def init_run(
    run_name: str,
    module_path: Path,
    purpose: str = "",
    study: str = "",
    data_description: str = "",
) -> Path:
    """
    Initialize a new processing run.

    Args:
        run_name: Name for the run folder (e.g., "jan2026_flat_fieldtrip")
        module_path: Path to the module root (parent of github_repo, inprocess, misc)
        purpose: Brief description of the run's purpose
        study: Associated study (e.g., "S1_historical")
        data_description: Description of the data being processed

    Returns:
        Path to the created run folder
    """
    inprocess_dir = module_path / "inprocess"
    github_repo_dir = module_path / "github_repo"
    template_file = inprocess_dir / "_analysis_params_template.yaml"

    # Validate paths
    if not inprocess_dir.exists():
        raise FileNotFoundError(f"inprocess directory not found: {inprocess_dir}")
    if not github_repo_dir.exists():
        raise FileNotFoundError(f"github_repo directory not found: {github_repo_dir}")
    if not template_file.exists():
        raise FileNotFoundError(f"Template file not found: {template_file}")

    # Create run folder
    run_dir = inprocess_dir / run_name
    if run_dir.exists():
        raise FileExistsError(f"Run folder already exists: {run_dir}")

    run_dir.mkdir(parents=True)

    # Load template
    with open(template_file) as f:
        params = yaml.safe_load(f)

    # Get git info from the github_repo
    git_info = get_git_info(github_repo_dir)

    # Get current timestamp
    now = datetime.now()
    iso_timestamp = now.isoformat()
    iso_date = now.strftime("%Y-%m-%d")

    # Get module name from path
    module_name = module_path.name

    # Populate auto-generated fields
    params["run"]["name"] = run_name
    params["run"]["module_name"] = module_name
    params["run"]["created_at"] = iso_timestamp
    params["run"]["created_by"] = getpass.getuser()
    params["run"]["status"] = "active"

    params["version"] = git_info

    params["temporal"]["start_date"] = iso_date

    # Populate user-provided context
    if purpose:
        params["context"]["purpose"] = purpose
    if study:
        params["context"]["study"] = study
    if data_description:
        params["context"]["data_description"] = data_description

    # Write the populated params file
    params_file = run_dir / "analysis_params.yaml"
    with open(params_file, "w") as f:
        yaml.dump(params, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    # Create standard subdirectories for the run
    (run_dir / "outputs").mkdir()
    (run_dir / "logs").mkdir()

    # Log to VICARIUS
    log_message = f"Initialized run '{run_name}' in module '{module_name}'"
    if purpose:
        log_message += f" - {purpose}"
    log_to_vicarius(log_message)

    return run_dir


def main():
    parser = argparse.ArgumentParser(
        description="Initialize a new processing run with auto-populated metadata"
    )
    parser.add_argument("run_name", help="Name for the run folder")
    parser.add_argument(
        "--purpose", "-p", default="", help="Brief description of the run's purpose"
    )
    parser.add_argument(
        "--study",
        "-s",
        default="",
        help="Associated study (e.g., S1_historical, S2_3D_structure)",
    )
    parser.add_argument(
        "--data", "-d", default="", help="Description of the data being processed"
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
        # Try to find module root by looking for inprocess/ directory
        current = Path.cwd()
        while current != current.parent:
            if (current / "inprocess").exists() and (current / "github_repo").exists():
                args.module_path = current
                break
            # Also check if we're inside github_repo
            if current.name == "github_repo" and (current.parent / "inprocess").exists():
                args.module_path = current.parent
                break
            current = current.parent

        if args.module_path is None:
            print("Error: Could not auto-detect module path.", file=sys.stderr)
            print("Run from within a module directory or specify --module-path", file=sys.stderr)
            sys.exit(1)

    try:
        run_dir = init_run(
            run_name=args.run_name,
            module_path=args.module_path,
            purpose=args.purpose,
            study=args.study,
            data_description=args.data,
        )
        print(f"Created run: {run_dir}")
        print(f"  - analysis_params.yaml (edit processing parameters)")
        print(f"  - outputs/ (for processing results)")
        print(f"  - logs/ (for run-specific logs)")
        print()
        print("Next steps:")
        print("  1. Edit analysis_params.yaml to set your processing parameters")
        print("  2. Run your processing scripts")
        print("  3. When done, use shelve_run.py to archive the run")

    except FileExistsError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
