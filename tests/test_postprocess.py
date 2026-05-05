"""Tests for postprocess CLI commands."""

import json

import polars as pl
import pytest

from deliver.postprocess.build_library_dict import main as build_library_dict
from deliver.postprocess.deduplicate import main as deduplicate
from deliver.postprocess.enrichment import main as enrichment


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
