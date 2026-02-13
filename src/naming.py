#!/usr/bin/env python3
"""
naming.py - Naming convention validation and parsing for 3D models.

Pattern: {PROJECTTYPE}{YYYYMMDD}_3D_{SITE}_{REPLICATE}[_n][_PROXY]

Examples:
    TCRMP20241014_3D_BWR_T2
    RBTEST20250301_3D_DOCK_TRY1_1
    HYDRUSMAPPING20250115_3D_FLC_RUN2_3_PROXY
"""

import re
from typing import Optional, Dict, List


NAMING_PATTERN = re.compile(
    r"^(?P<projecttype>[A-Za-z]+)"
    r"(?P<date>\d{8})"
    r"_3D_"
    r"(?P<site>[A-Za-z0-9]+)"
    r"_(?P<replicate>(?:T|TRY|RUN)\d+)"
    r"(?:_(?P<part>\d+))?"
    r"(?:_PROXY)?$",
    re.IGNORECASE,
)

KNOWN_PROJECT_TYPES = {
    "RBTEST",
    "RBMAPPING",
    "HYDRUSTEST",
    "HYDRUSMAPPING",
    "HYDRUSTCRMP",
    "TCRMP",
    "MISC",
}


def strip_proxy(name: str) -> str:
    """Remove _PROXY suffix from a name."""
    if name.upper().endswith("_PROXY"):
        return name[:-6]
    return name


def parse_model_name(name: str) -> Optional[Dict[str, Optional[str]]]:
    """Parse a model/video name and return components.

    Returns None if name doesn't match the expected pattern.
    Strips file extension and _PROXY suffix before parsing.
    """
    # Strip file extension if present
    if "." in name:
        name = name.rsplit(".", 1)[0]

    # Strip _PROXY suffix
    name = strip_proxy(name)

    match = NAMING_PATTERN.match(name)
    if not match:
        return None

    return {
        "projecttype": match.group("projecttype").upper(),
        "date": match.group("date"),
        "site": match.group("site").upper(),
        "replicate": match.group("replicate").upper(),
        "part": match.group("part"),
        "base_id": (
            f"{match.group('projecttype').upper()}{match.group('date')}"
            f"_3D_{match.group('site').upper()}_{match.group('replicate').upper()}"
        ),
    }


def group_multipart(names: List[str]) -> Dict[str, List[dict]]:
    """Group filenames by base model ID.

    Args:
        names: List of filenames (with or without extensions).

    Returns:
        Dict mapping base_id to a list of dicts sorted by part number:
            {"original_name": str, "clean_name": str, "part": int}
        Names that don't match the pattern are grouped under their own name.
    """
    groups: Dict[str, List[dict]] = {}

    for name in names:
        # Strip extension for parsing
        stem = name.rsplit(".", 1)[0] if "." in name else name
        clean = strip_proxy(stem)
        parsed = parse_model_name(clean)

        if parsed:
            base = parsed["base_id"]
            if base not in groups:
                groups[base] = []
            groups[base].append(
                {
                    "original_name": name,
                    "clean_name": clean,
                    "part": int(parsed["part"]) if parsed["part"] else 0,
                }
            )
        else:
            # Fallback: use entire stem as base ID
            groups[stem] = [{"original_name": name, "clean_name": clean, "part": 0}]

    # Sort each group by part number
    for base_id in groups:
        groups[base_id].sort(key=lambda x: x["part"])

    return groups


def check_unknown_values(names: List[str]) -> Dict[str, set]:
    """Check for unknown project types and sites in a list of names.

    Returns dict with 'project_types' and 'sites' sets of unknown values.
    """
    unknown_project_types: set = set()
    unknown_sites: set = set()

    for name in names:
        parsed = parse_model_name(name)
        if parsed:
            if parsed["projecttype"] not in KNOWN_PROJECT_TYPES:
                unknown_project_types.add(parsed["projecttype"])

    return {
        "project_types": unknown_project_types,
        "sites": unknown_sites,
    }
