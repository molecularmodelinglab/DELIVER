"""Tests for postprocess CLI commands."""

import json

import polars as pl
import pytest

from deliver.postprocess.build_library_dict import main as build_library_dict
from deliver.postprocess.common import validate_common_format
from deliver.postprocess.deduplicate import main as deduplicate
from deliver.postprocess.enrichment import main as enrichment
from deliver.postprocess.normalize import main as normalize, normalize as normalize_df


@pytest.fixture
def deli_data_dir(tmp_path):
    """Minimal DELi data directory with two libraries, each with two BB positions."""
    libraries_dir = tmp_path / "libraries"
    bb_dir = tmp_path / "building_blocks"
    libraries_dir.mkdir()
    bb_dir.mkdir()

    # L01: 3 real + 1 null in BBA, 2 real in BBB
    (libraries_dir / "L01.json").write_text(json.dumps({
        "bb_sets": [
            {"cycle": 1, "bb_set_name": "L01_BBA"},
            {"cycle": 2, "bb_set_name": "L01_BBB"},
        ]
    }))
    (bb_dir / "L01_BBA.csv").write_text("id,tag\n1,AAA\n2,BBB\n3,CCC\nnull_cap,TTT\n")
    (bb_dir / "L01_BBB.csv").write_text("id,tag\n1,GGG\n2,CCC\n")

    # L02: 2 real in BBA only
    (libraries_dir / "L02.json").write_text(json.dumps({
        "bb_sets": [
            {"cycle": 1, "bb_set_name": "L02_BBA"},
        ]
    }))
    (bb_dir / "L02_BBA.csv").write_text("id,tag\n1,AAA\n2,TTT\n")

    return tmp_path


@pytest.fixture
def deli_counts_parquet(tmp_path):
    """Minimal DELi counts parquet (3-cycle libraries)."""
    df = pl.DataFrame({
        "library_id":   ["L01", "L01", "L02"],
        "bb_ids":       ["206,31,642", "206,31,643", "100,200,300"],
        "count":        [10, 5, 8],
        "raw_count":    [12, 6, 9],
        "dedup_count":  [10, 5, 8],
    })
    path = tmp_path / "counts.parquet"
    df.write_parquet(path)
    return path


@pytest.fixture
def counts_parquet(tmp_path):
    """Minimal counts parquet file."""
    df = pl.DataFrame({
        "library_id": ["L01", "L01", "L02"],
        "bb_ids":     ["1,2,3", "1,2,4", "1,2,3"],
        "count":      [10, 5, 8],
    })
    path = tmp_path / "counts.parquet"
    df.write_parquet(path)
    return path


class TestNormalize:
    def test_runs_and_writes_output(self, deli_counts_parquet, tmp_path):
        out = tmp_path / "normalized.parquet"
        normalize(["--input", str(deli_counts_parquet), "--output", str(out)])
        assert out.exists()

    def test_output_columns(self, deli_counts_parquet, tmp_path):
        out = tmp_path / "normalized.parquet"
        normalize(["--input", str(deli_counts_parquet), "--output", str(out)])
        df = pl.read_parquet(out)
        assert set(df.columns) == {"compound_id", "library_id", "A", "B", "C", "raw_count", "corrected_count"}

    def test_compound_id_format(self, deli_counts_parquet, tmp_path):
        out = tmp_path / "normalized.parquet"
        normalize(["--input", str(deli_counts_parquet), "--output", str(out)])
        df = pl.read_parquet(out)
        assert "L01-206-31-642" in df["compound_id"].to_list()

    def test_bb_ids_split_correctly(self, deli_counts_parquet, tmp_path):
        out = tmp_path / "normalized.parquet"
        normalize(["--input", str(deli_counts_parquet), "--output", str(out)])
        df = pl.read_parquet(out).filter(pl.col("compound_id") == "L01-206-31-642")
        assert df["A"][0] == "206"
        assert df["B"][0] == "31"
        assert df["C"][0] == "642"

    def test_count_renamed(self, deli_counts_parquet, tmp_path):
        out = tmp_path / "normalized.parquet"
        normalize(["--input", str(deli_counts_parquet), "--output", str(out)])
        df = pl.read_parquet(out)
        assert "corrected_count" in df.columns
        assert "count" not in df.columns

    def test_dedup_count_dropped(self, deli_counts_parquet, tmp_path):
        out = tmp_path / "normalized.parquet"
        normalize(["--input", str(deli_counts_parquet), "--output", str(out)])
        df = pl.read_parquet(out)
        assert "dedup_count" not in df.columns

    def test_works_without_dedup_count(self, tmp_path):
        df = pl.DataFrame({
            "library_id": ["L01"],
            "bb_ids":     ["1,2,3"],
            "count":      [5],
            "raw_count":  [6],
        })
        inp = tmp_path / "counts.parquet"
        df.write_parquet(inp)
        out = tmp_path / "normalized.parquet"
        normalize(["--input", str(inp), "--output", str(out)])
        assert out.exists()

    def test_missing_input_fails(self, tmp_path):
        with pytest.raises(SystemExit):
            normalize(["--input", str(tmp_path / "nonexistent.parquet"), "--output", str(tmp_path / "out.parquet")])

    def test_missing_required_args_fails(self):
        with pytest.raises(SystemExit):
            normalize([])


class TestValidateCommonFormat:
    def test_valid_dataframe_passes(self):
        df = pl.DataFrame({
            "compound_id":      ["L01-1-2-3", "L01-1-2-4"],
            "library_id":       ["L01", "L01"],
            "raw_count":        [5, 3],
            "corrected_count":  [4, 3],
        })
        validate_common_format(df)  # should not raise

    def test_missing_column_fails(self):
        df = pl.DataFrame({
            "compound_id": ["L01-1-2-3"],
            "library_id":  ["L01"],
        })
        with pytest.raises(SystemExit):
            validate_common_format(df)

    def test_duplicate_compound_id_fails(self):
        df = pl.DataFrame({
            "compound_id":      ["L01-1-2-3", "L01-1-2-3"],
            "library_id":       ["L01", "L01"],
            "raw_count":        [5, 5],
            "corrected_count":  [4, 4],
        })
        with pytest.raises(SystemExit):
            validate_common_format(df)


class TestBuildLibraryDict:
    def test_runs_and_writes_output(self, deli_data_dir, tmp_path):
        out = tmp_path / "library_dict.json"
        build_library_dict(["--deli-data-dir", str(deli_data_dir), "--output", str(out)])
        assert out.exists()

    def test_output_is_valid_json(self, deli_data_dir, tmp_path):
        out = tmp_path / "library_dict.json"
        build_library_dict(["--deli-data-dir", str(deli_data_dir), "--output", str(out)])
        data = json.loads(out.read_text())
        assert isinstance(data, dict)
        assert len(data) > 0

    def test_counts_correct(self, deli_data_dir, tmp_path):
        out = tmp_path / "library_dict.json"
        build_library_dict(["--deli-data-dir", str(deli_data_dir), "--output", str(out)])
        data = json.loads(out.read_text())
        assert data["L01"]["A"] == 3   # 3 real, 1 null excluded
        assert data["L01"]["B"] == 2
        assert data["L02"]["A"] == 2

    def test_null_blocks_excluded(self, deli_data_dir, tmp_path):
        out = tmp_path / "library_dict.json"
        build_library_dict(["--deli-data-dir", str(deli_data_dir), "--output", str(out)])
        data = json.loads(out.read_text())
        assert data["L01"]["A"] == 3   # not 4

    def test_missing_deli_data_dir_fails(self, tmp_path):
        with pytest.raises(SystemExit):
            build_library_dict(["--deli-data-dir", str(tmp_path / "nonexistent"), "--output", str(tmp_path / "out.json")])

    def test_missing_required_args_fails(self):
        with pytest.raises(SystemExit):
            build_library_dict([])


class TestDeduplicate:
    def test_runs_and_writes_output(self, counts_parquet, tmp_path):
        out = tmp_path / "dedup.parquet"
        deduplicate(["--input", str(counts_parquet), "--output", str(out)])
        assert out.exists()

    def test_output_is_valid_parquet(self, counts_parquet, tmp_path):
        out = tmp_path / "dedup.parquet"
        deduplicate(["--input", str(counts_parquet), "--output", str(out)])
        df = pl.read_parquet(out)
        assert len(df) > 0

    def test_missing_input_fails(self, tmp_path):
        with pytest.raises(SystemExit):
            deduplicate(["--input", str(tmp_path / "nonexistent.parquet"), "--output", str(tmp_path / "out.parquet")])

    def test_missing_required_args_fails(self):
        with pytest.raises(SystemExit):
            deduplicate([])


class TestEnrichment:
    def test_runs_and_writes_output(self, counts_parquet, tmp_path):
        out = tmp_path / "enrichment.parquet"
        enrichment(["--input", str(counts_parquet), "--output", str(out)])
        assert out.exists()

    def test_output_is_valid_parquet(self, counts_parquet, tmp_path):
        out = tmp_path / "enrichment.parquet"
        enrichment(["--input", str(counts_parquet), "--output", str(out)])
        df = pl.read_parquet(out)
        assert len(df) > 0

    def test_missing_input_fails(self, tmp_path):
        with pytest.raises(SystemExit):
            enrichment(["--input", str(tmp_path / "nonexistent.parquet"), "--output", str(tmp_path / "out.parquet")])

    def test_missing_required_args_fails(self):
        with pytest.raises(SystemExit):
            enrichment([])
