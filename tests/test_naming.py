"""Tests for naming convention parsing and grouping."""

import sys
from pathlib import Path

# Add src to path so we can import naming module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from naming import parse_model_name, strip_proxy, group_multipart, check_unknown_values


class TestStripProxy:
    def test_strips_proxy_suffix(self):
        assert strip_proxy("TCRMP20240215_3D_FLC_T5_PROXY") == "TCRMP20240215_3D_FLC_T5"

    def test_strips_proxy_case_insensitive(self):
        assert strip_proxy("TCRMP20240215_3D_FLC_T5_proxy") == "TCRMP20240215_3D_FLC_T5"
        assert strip_proxy("TCRMP20240215_3D_FLC_T5_Proxy") == "TCRMP20240215_3D_FLC_T5"

    def test_no_proxy(self):
        assert strip_proxy("TCRMP20240215_3D_FLC_T5") == "TCRMP20240215_3D_FLC_T5"

    def test_proxy_with_part_number(self):
        assert strip_proxy("TCRMP20240215_3D_FLC_T5_2_PROXY") == "TCRMP20240215_3D_FLC_T5_2"


class TestParseModelName:
    def test_standard_tcrmp(self):
        result = parse_model_name("TCRMP20240215_3D_FLC_T5")
        assert result is not None
        assert result["projecttype"] == "TCRMP"
        assert result["date"] == "20240215"
        assert result["site"] == "FLC"
        assert result["replicate"] == "T5"
        assert result["part"] is None
        assert result["base_id"] == "TCRMP20240215_3D_FLC_T5"

    def test_with_file_extension(self):
        result = parse_model_name("TCRMP20240215_3D_FLC_T5.mov")
        assert result is not None
        assert result["projecttype"] == "TCRMP"

    def test_multipart(self):
        result = parse_model_name("RBTEST20240215_3D_BWR_T1_1")
        assert result is not None
        assert result["part"] == "1"
        assert result["base_id"] == "RBTEST20240215_3D_BWR_T1"

    def test_multipart_with_extension(self):
        result = parse_model_name("RBTEST20240215_3D_BWR_T1_2.mp4")
        assert result is not None
        assert result["part"] == "2"

    def test_proxy_stripped(self):
        result = parse_model_name("TCRMP20240215_3D_FLC_T5_PROXY")
        assert result is not None
        assert result["replicate"] == "T5"
        assert result["part"] is None

    def test_multipart_with_proxy(self):
        result = parse_model_name("RBTEST20240215_3D_BWR_T1_2_PROXY.mp4")
        assert result is not None
        assert result["part"] == "2"
        assert result["base_id"] == "RBTEST20240215_3D_BWR_T1"

    def test_try_replicate(self):
        result = parse_model_name("MISC20240215_3D_TST_TRY3")
        assert result is not None
        assert result["replicate"] == "TRY3"

    def test_run_replicate(self):
        result = parse_model_name("HYDRUSMAPPING20240215_3D_BWR_RUN2")
        assert result is not None
        assert result["replicate"] == "RUN2"
        assert result["projecttype"] == "HYDRUSMAPPING"

    def test_hydrustest(self):
        result = parse_model_name("HYDRUSTEST20250101_3D_DOCK_T1")
        assert result is not None
        assert result["projecttype"] == "HYDRUSTEST"
        assert result["site"] == "DOCK"

    def test_invalid_name(self):
        assert parse_model_name("random_video_file") is None

    def test_missing_3d_marker(self):
        assert parse_model_name("TCRMP20240215_FLC_T5") is None

    def test_case_insensitive(self):
        result = parse_model_name("tcrmp20240215_3d_flc_t5")
        assert result is not None
        assert result["projecttype"] == "TCRMP"
        assert result["site"] == "FLC"


class TestGroupMultipart:
    def test_single_files(self):
        names = [
            "TCRMP20240215_3D_FLC_T5.mov",
            "TCRMP20240215_3D_BWR_T1.mov",
        ]
        groups = group_multipart(names)
        assert len(groups) == 2
        assert "TCRMP20240215_3D_FLC_T5" in groups
        assert "TCRMP20240215_3D_BWR_T1" in groups
        assert len(groups["TCRMP20240215_3D_FLC_T5"]) == 1

    def test_multipart_grouped(self):
        names = [
            "TCRMP20240215_3D_FLC_T5_1.mov",
            "TCRMP20240215_3D_FLC_T5_2.mov",
            "TCRMP20240215_3D_FLC_T5_3.mov",
        ]
        groups = group_multipart(names)
        assert len(groups) == 1
        assert "TCRMP20240215_3D_FLC_T5" in groups
        assert len(groups["TCRMP20240215_3D_FLC_T5"]) == 3

    def test_multipart_sorted_by_part(self):
        names = [
            "TCRMP20240215_3D_FLC_T5_3.mov",
            "TCRMP20240215_3D_FLC_T5_1.mov",
            "TCRMP20240215_3D_FLC_T5_2.mov",
        ]
        groups = group_multipart(names)
        parts = groups["TCRMP20240215_3D_FLC_T5"]
        assert parts[0]["part"] == 1
        assert parts[1]["part"] == 2
        assert parts[2]["part"] == 3

    def test_mixed_single_and_multipart(self):
        names = [
            "TCRMP20240215_3D_FLC_T5_1.mov",
            "TCRMP20240215_3D_FLC_T5_2.mov",
            "TCRMP20240215_3D_BWR_T1.mov",
        ]
        groups = group_multipart(names)
        assert len(groups) == 2
        assert len(groups["TCRMP20240215_3D_FLC_T5"]) == 2
        assert len(groups["TCRMP20240215_3D_BWR_T1"]) == 1

    def test_proxy_stripped_before_grouping(self):
        names = [
            "TCRMP20240215_3D_FLC_T5_1_PROXY.mov",
            "TCRMP20240215_3D_FLC_T5_2_PROXY.mov",
        ]
        groups = group_multipart(names)
        assert len(groups) == 1
        assert "TCRMP20240215_3D_FLC_T5" in groups

    def test_non_matching_names_kept(self):
        names = ["random_file.mov"]
        groups = group_multipart(names)
        assert len(groups) == 1
        assert "random_file" in groups


class TestCheckUnknownValues:
    def test_known_project_types(self):
        names = ["TCRMP20240215_3D_FLC_T5", "RBTEST20240215_3D_BWR_T1"]
        unknowns = check_unknown_values(names)
        assert len(unknowns["project_types"]) == 0

    def test_unknown_project_type(self):
        names = ["NEWPROJECT20240215_3D_FLC_T5"]
        unknowns = check_unknown_values(names)
        assert "NEWPROJECT" in unknowns["project_types"]

    def test_misc_is_known(self):
        names = ["MISC20240215_3D_TST_TRY1"]
        unknowns = check_unknown_values(names)
        assert len(unknowns["project_types"]) == 0
