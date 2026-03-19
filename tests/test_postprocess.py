"""Tests for postprocess CLI commands."""

import polars as pl
import pytest
from click.testing import CliRunner

from deliver.postprocess.deduplicate import deduplicate
from deliver.postprocess.enrichment import enrichment


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


class TestDeduplicate:
    def test_runs_and_writes_output(self, counts_parquet, tmp_path):
        out = tmp_path / "dedup.parquet"
        result = CliRunner().invoke(deduplicate, [
            "--input",  str(counts_parquet),
            "--output", str(out),
        ])
        assert result.exit_code == 0, result.output
        assert out.exists()

    def test_output_is_valid_parquet(self, counts_parquet, tmp_path):
        out = tmp_path / "dedup.parquet"
        CliRunner().invoke(deduplicate, [
            "--input",  str(counts_parquet),
            "--output", str(out),
        ])
        df = pl.read_parquet(out)
        assert len(df) > 0

    def test_missing_input_fails(self, tmp_path):
        result = CliRunner().invoke(deduplicate, [
            "--input",  str(tmp_path / "nonexistent.parquet"),
            "--output", str(tmp_path / "out.parquet"),
        ])
        assert result.exit_code != 0

    def test_missing_required_args_fails(self):
        result = CliRunner().invoke(deduplicate, [])
        assert result.exit_code != 0


class TestEnrichment:
    def test_runs_and_writes_output(self, counts_parquet, tmp_path):
        out = tmp_path / "enrichment.parquet"
        result = CliRunner().invoke(enrichment, [
            "--input",  str(counts_parquet),
            "--output", str(out),
        ])
        assert result.exit_code == 0, result.output
        assert out.exists()

    def test_output_is_valid_parquet(self, counts_parquet, tmp_path):
        out = tmp_path / "enrichment.parquet"
        CliRunner().invoke(enrichment, [
            "--input",  str(counts_parquet),
            "--output", str(out),
        ])
        df = pl.read_parquet(out)
        assert len(df) > 0

    def test_missing_input_fails(self, tmp_path):
        result = CliRunner().invoke(enrichment, [
            "--input",  str(tmp_path / "nonexistent.parquet"),
            "--output", str(tmp_path / "out.parquet"),
        ])
        assert result.exit_code != 0

    def test_missing_required_args_fails(self):
        result = CliRunner().invoke(enrichment, [])
        assert result.exit_code != 0
