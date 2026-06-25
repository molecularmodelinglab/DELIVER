"""Tests for postprocess CLI commands."""

import json
import math

import polars as pl
import pytest

from deliver.postprocess.add_smiles import main as add_smiles
from deliver.postprocess.merge_smiles import main as merge_smiles_cli
from deliver.postprocess.build_library_dict import main as build_library_dict
from deliver.postprocess.lib.common import validate_common_format
from deliver.postprocess.deduplicate import main as deduplicate
from deliver.postprocess.disynthons import main as disynthons
from deliver.postprocess.enrichment import main as enrichment
from deliver.postprocess.normalize import main as normalize, normalize as normalize_df
from deliver.postprocess.normalize_custom import main as normalize_custom


@pytest.fixture
def deli_data_dir(tmp_path):
    """Minimal DELi data directory with two libraries, each with two BB positions."""
    libraries_dir = tmp_path / "libraries"
    bb_dir = tmp_path / "building_blocks"
    libraries_dir.mkdir()
    bb_dir.mkdir()

    # L01: 3 real + 1 null in BBA (each id has multiple tags), 2 real in BBB
    (libraries_dir / "L01.json").write_text(json.dumps({
        "bb_sets": [
            {"cycle": 1, "bb_set_name": "L01_BBA"},
            {"cycle": 2, "bb_set_name": "L01_BBB"},
        ]
    }))
    # ids 1,2,3 each appear twice (two tags per building block); null_cap excluded
    (bb_dir / "L01_BBA.csv").write_text("id,tag\n1,AAA\n1,AAA2\n2,BBB\n2,BBB2\n3,CCC\n3,CCC2\nnull_cap,TTT\n")
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

    def test_mixed_cycle_lengths(self, tmp_path):
        # L01 has 3 cycles, L02 has 2 cycles — shorter rows get null for C.
        df = pl.DataFrame({
            "library_id": ["L01", "L02"],
            "bb_ids":     ["1,2,3", "1,2"],
            "count":      [5, 3],
            "raw_count":  [6, 4],
        })
        inp = tmp_path / "counts.parquet"
        df.write_parquet(inp)
        out = tmp_path / "normalized.parquet"
        normalize(["--input", str(inp), "--output", str(out)])
        result = pl.read_parquet(out)
        assert "C" in result.columns
        l02 = result.filter(pl.col("library_id") == "L02")
        assert l02["C"][0] is None

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

    def test_duplicate_tags_per_id_counted_once(self, deli_data_dir, tmp_path):
        # L01_BBA has 2 rows per id (multiple tags); count should be 3, not 6
        out = tmp_path / "library_dict.json"
        build_library_dict(["--deli-data-dir", str(deli_data_dir), "--output", str(out)])
        data = json.loads(out.read_text())
        assert data["L01"]["A"] == 3   # max(id), not row count

    def test_missing_deli_data_dir_fails(self, tmp_path):
        with pytest.raises(SystemExit):
            build_library_dict(["--deli-data-dir", str(tmp_path / "nonexistent"), "--output", str(tmp_path / "out.json")])

    def test_missing_required_args_fails(self):
        with pytest.raises(SystemExit):
            build_library_dict([])


class TestAddSmiles:
    def _make_input(self, tmp_path):
        df = pl.DataFrame({
            "compound_id":     ["L01-1-1", "L01-2-1", "L02-1-1"],
            "library_id":      ["L01", "L01", "L02"],
            "A":               ["1", "2", "1"],
            "B":               ["1", "1", "1"],
            "raw_count":       [3, 1, 2],
            "corrected_count": [3, 1, 2],
        })
        inp = tmp_path / "norm.parquet"
        df.write_parquet(inp)
        return inp

    def _make_smiles_file(self, tmp_path, lib_id: str, entries: list[tuple]) -> str:
        path = tmp_path / f"{lib_id}_smiles.parquet"
        pl.DataFrame({"compound": [e[0] for e in entries],
                      "SMILES":   [e[1] for e in entries]}).write_parquet(path)
        return str(path)

    def test_adds_smiles_column(self, tmp_path):
        inp = self._make_input(tmp_path)
        smiles_file = self._make_smiles_file(tmp_path, "L01",
            [("L01-1-1", "CCO"), ("L01-2-1", "CCC")])
        smiles_map = tmp_path / "map.json"
        smiles_map.write_text(json.dumps({"L01": str(smiles_file)}))
        out = tmp_path / "out.parquet"
        add_smiles(["--input", str(inp), "--smiles-map", str(smiles_map), "--output", str(out)])
        df = pl.read_parquet(out)
        assert "SMILES" in df.columns

    def test_correct_smiles_assigned(self, tmp_path):
        inp = self._make_input(tmp_path)
        smiles_file = self._make_smiles_file(tmp_path, "L01",
            [("L01-1-1", "CCO"), ("L01-2-1", "CCC")])
        smiles_map = tmp_path / "map.json"
        smiles_map.write_text(json.dumps({"L01": str(smiles_file)}))
        out = tmp_path / "out.parquet"
        add_smiles(["--input", str(inp), "--smiles-map", str(smiles_map), "--output", str(out)])
        df = pl.read_parquet(out).sort("compound_id")
        assert df.filter(pl.col("compound_id") == "L01-1-1")["SMILES"][0] == "CCO"
        assert df.filter(pl.col("compound_id") == "L01-2-1")["SMILES"][0] == "CCC"

    def test_missing_library_gets_null(self, tmp_path):
        # L02 not in smiles_map → SMILES should be null
        inp = self._make_input(tmp_path)
        smiles_file = self._make_smiles_file(tmp_path, "L01",
            [("L01-1-1", "CCO"), ("L01-2-1", "CCC")])
        smiles_map = tmp_path / "map.json"
        smiles_map.write_text(json.dumps({"L01": str(smiles_file)}))
        out = tmp_path / "out.parquet"
        add_smiles(["--input", str(inp), "--smiles-map", str(smiles_map), "--output", str(out)])
        df = pl.read_parquet(out)
        assert df.filter(pl.col("library_id") == "L02")["SMILES"][0] is None

    def test_custom_column_names(self, tmp_path):
        inp = self._make_input(tmp_path)
        path = tmp_path / "L01_smiles.parquet"
        pl.DataFrame({"cpd_id": ["L01-1-1", "L01-2-1"], "smi": ["CCO", "CCC"]}).write_parquet(path)
        smiles_map = tmp_path / "map.json"
        smiles_map.write_text(json.dumps({"L01": str(path)}))
        out = tmp_path / "out.parquet"
        add_smiles(["--input", str(inp), "--smiles-map", str(smiles_map),
                    "--compound-col", "cpd_id", "--smiles-col", "smi", "--output", str(out)])
        df = pl.read_parquet(out)
        assert "smi" in df.columns
        assert df.filter(pl.col("compound_id") == "L01-1-1")["smi"][0] == "CCO"

    def test_row_count_unchanged(self, tmp_path):
        inp = self._make_input(tmp_path)
        smiles_file = self._make_smiles_file(tmp_path, "L01",
            [("L01-1-1", "CCO"), ("L01-2-1", "CCC")])
        smiles_map = tmp_path / "map.json"
        smiles_map.write_text(json.dumps({"L01": str(smiles_file)}))
        out = tmp_path / "out.parquet"
        add_smiles(["--input", str(inp), "--smiles-map", str(smiles_map), "--output", str(out)])
        assert len(pl.read_parquet(out)) == len(pl.read_parquet(inp))

    def test_missing_smiles_raises_error(self, tmp_path):
        inp = self._make_input(tmp_path)
        # Only one of the two L01 compounds has a SMILES entry
        smiles_file = self._make_smiles_file(tmp_path, "L01",
            [("L01-1-1", "CCO")])
        smiles_map = tmp_path / "map.json"
        smiles_map.write_text(json.dumps({"L01": str(smiles_file)}))
        out = tmp_path / "out.parquet"
        with pytest.raises(ValueError, match="L01"):
            add_smiles(["--input", str(inp), "--smiles-map", str(smiles_map), "--output", str(out)])

    def test_library_flag_restricts_to_one_library(self, tmp_path):
        inp = self._make_input(tmp_path)
        smiles_file = self._make_smiles_file(tmp_path, "L01",
            [("L01-1-1", "CCO"), ("L01-2-1", "CCC")])
        smiles_map = tmp_path / "map.json"
        smiles_map.write_text(json.dumps({"L01": str(smiles_file)}))
        out = tmp_path / "out.parquet"
        add_smiles(["--input", str(inp), "--smiles-map", str(smiles_map),
                    "--library", "L01", "--output", str(out)])
        df = pl.read_parquet(out)
        assert set(df["library_id"].to_list()) == {"L01"}
        assert len(df) == 2


class TestMergeSmiles:
    def _write_orig(self, tmp_path):
        df = pl.DataFrame({
            "compound_id":     ["L01-1-1", "L01-2-1", "L02-1-1"],
            "library_id":      ["L01",     "L01",     "L02"],
            "raw_count":       [3,         1,         2],
            "corrected_count": [3,         1,         2],
        })
        p = tmp_path / "normalized.parquet"
        df.write_parquet(p)
        return p

    def _write_partial(self, tmp_path, name, data):
        df = pl.DataFrame(data)
        p = tmp_path / f"{name}.parquet"
        df.write_parquet(p)
        return p

    def test_row_count_preserved(self, tmp_path):
        orig = self._write_orig(tmp_path)
        partial = self._write_partial(tmp_path, "L01", {
            "compound_id": ["L01-1-1", "L01-2-1"], "library_id": ["L01", "L01"],
            "raw_count": [3, 1], "corrected_count": [3, 1], "SMILES": ["CCO", "CCC"],
        })
        out = tmp_path / "merged.parquet"
        merge_smiles_cli(["--input", str(orig), "--partials", str(partial), "--output", str(out)])
        assert len(pl.read_parquet(out)) == 3

    def test_covered_smiles_present(self, tmp_path):
        orig = self._write_orig(tmp_path)
        partial = self._write_partial(tmp_path, "L01", {
            "compound_id": ["L01-1-1", "L01-2-1"], "library_id": ["L01", "L01"],
            "raw_count": [3, 1], "corrected_count": [3, 1], "SMILES": ["CCO", "CCC"],
        })
        out = tmp_path / "merged.parquet"
        merge_smiles_cli(["--input", str(orig), "--partials", str(partial), "--output", str(out)])
        df = pl.read_parquet(out)
        assert df.filter(pl.col("compound_id") == "L01-1-1")["SMILES"][0] == "CCO"
        assert df.filter(pl.col("compound_id") == "L01-2-1")["SMILES"][0] == "CCC"

    def test_uncovered_library_gets_null(self, tmp_path):
        orig = self._write_orig(tmp_path)
        partial = self._write_partial(tmp_path, "L01", {
            "compound_id": ["L01-1-1", "L01-2-1"], "library_id": ["L01", "L01"],
            "raw_count": [3, 1], "corrected_count": [3, 1], "SMILES": ["CCO", "CCC"],
        })
        out = tmp_path / "merged.parquet"
        merge_smiles_cli(["--input", str(orig), "--partials", str(partial), "--output", str(out)])
        df = pl.read_parquet(out)
        assert df.filter(pl.col("library_id") == "L02")["SMILES"][0] is None

    def test_all_covered_no_nulls(self, tmp_path):
        orig = self._write_orig(tmp_path)
        p1 = self._write_partial(tmp_path, "L01", {
            "compound_id": ["L01-1-1", "L01-2-1"], "library_id": ["L01", "L01"],
            "raw_count": [3, 1], "corrected_count": [3, 1], "SMILES": ["CCO", "CCC"],
        })
        p2 = self._write_partial(tmp_path, "L02", {
            "compound_id": ["L02-1-1"], "library_id": ["L02"],
            "raw_count": [2], "corrected_count": [2], "SMILES": ["c1ccccc1"],
        })
        out = tmp_path / "merged.parquet"
        merge_smiles_cli(["--input", str(orig), "--partials", str(p1), str(p2), "--output", str(out)])
        df = pl.read_parquet(out)
        assert df["SMILES"].null_count() == 0

    def test_multiple_uncovered_libraries(self, tmp_path):
        df_orig = pl.DataFrame({
            "compound_id": ["L01-1-1", "L02-1-1", "L03-1-1"],
            "library_id":  ["L01",     "L02",     "L03"],
            "raw_count":   [1,         2,         3],
            "corrected_count": [1,     2,         3],
        })
        orig = tmp_path / "norm.parquet"
        df_orig.write_parquet(orig)
        partial = self._write_partial(tmp_path, "L01", {
            "compound_id": ["L01-1-1"], "library_id": ["L01"],
            "raw_count": [1], "corrected_count": [1], "SMILES": ["CCO"],
        })
        out = tmp_path / "merged.parquet"
        merge_smiles_cli(["--input", str(orig), "--partials", str(partial), "--output", str(out)])
        df = pl.read_parquet(out)
        assert len(df) == 3
        null_libs = set(df.filter(pl.col("SMILES").is_null())["library_id"].to_list())
        assert null_libs == {"L02", "L03"}

    def test_custom_smiles_col_name(self, tmp_path):
        orig = self._write_orig(tmp_path)
        partial = self._write_partial(tmp_path, "L01", {
            "compound_id": ["L01-1-1", "L01-2-1"], "library_id": ["L01", "L01"],
            "raw_count": [3, 1], "corrected_count": [3, 1], "smi": ["CCO", "CCC"],
        })
        out = tmp_path / "merged.parquet"
        merge_smiles_cli(["--input", str(orig), "--partials", str(partial),
                          "--smiles-col", "smi", "--output", str(out)])
        df = pl.read_parquet(out)
        assert "smi" in df.columns
        assert "SMILES" not in df.columns
        assert df.filter(pl.col("library_id") == "L02")["smi"][0] is None

    def test_missing_required_args_fails(self):
        with pytest.raises(SystemExit):
            merge_smiles_cli([])


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


@pytest.fixture
def normalized_parquet(tmp_path):
    """Minimal normalized parquet in common format."""
    df = pl.DataFrame({
        "compound_id":      ["L01-1-2-3", "L01-1-2-4", "L02-1-2-3"],
        "library_id":       ["L01",       "L01",        "L02"],
        "A":                ["1",         "1",          "1"],
        "B":                ["2",         "2",          "2"],
        "C":                ["3",         "4",          "3"],
        "raw_count":        [10,          5,            8],
        "corrected_count":  [10,          5,            8],
    })
    path = tmp_path / "normalized.parquet"
    df.write_parquet(path)
    return path


@pytest.fixture
def library_dict_json(tmp_path):
    """Minimal library dict JSON."""
    data = {
        "L01": {"A": 100, "B": 200, "C": 150},
        "L02": {"A": 80,  "B": 120, "C": 100},
    }
    path = tmp_path / "library_dict.json"
    path.write_text(json.dumps(data))
    return path


class TestEnrichment:
    def test_runs_and_writes_output(self, normalized_parquet, library_dict_json, tmp_path):
        out = tmp_path / "enrichment.parquet"
        enrichment(["--input", str(normalized_parquet), "--library-dict", str(library_dict_json), "--output", str(out)])
        assert out.exists()

    def test_output_has_z_score_columns(self, normalized_parquet, library_dict_json, tmp_path):
        out = tmp_path / "enrichment.parquet"
        enrichment(["--input", str(normalized_parquet), "--library-dict", str(library_dict_json), "--output", str(out)])
        df = pl.read_parquet(out)
        assert "z_score_lib" in df.columns
        assert "z_score_global" in df.columns

    def test_output_has_polyo(self, normalized_parquet, library_dict_json, tmp_path):
        out = tmp_path / "enrichment.parquet"
        enrichment(["--input", str(normalized_parquet), "--library-dict", str(library_dict_json), "--output", str(out)])
        df = pl.read_parquet(out)
        assert "polyo" in df.columns
        assert df["polyo"].is_finite().all()

    def test_polyo_higher_for_enriched_compound(self, tmp_path):
        # One library, two compounds: high count should have higher polyO.
        df = pl.DataFrame({
            "compound_id":     ["LIB-1", "LIB-2"],
            "library_id":      ["LIB", "LIB"],
            "raw_count":       [50, 1],
            "corrected_count": [50, 1],
        })
        inp = tmp_path / "norm.parquet"
        df.write_parquet(inp)
        lib_dict = tmp_path / "lib.json"
        lib_dict.write_text(json.dumps({"LIB": {"A": 100}}))
        out = tmp_path / "enrich.parquet"
        enrichment(["--input", str(inp), "--library-dict", str(lib_dict), "--output", str(out)])
        result = pl.read_parquet(out).sort("compound_id")
        assert result["polyo"][0] > result["polyo"][1]

    def test_polyo_values(self, tmp_path):
        # Library: {"A": 4}, raw_count [10, 2], total=12.
        # d = 12/4 = 3.0; c_cpd=4, c_read=8.
        df = pl.DataFrame({
            "compound_id":     ["LIB-1", "LIB-2"],
            "library_id":      ["LIB",   "LIB"],
            "raw_count":       [10, 2],
            "corrected_count": [10, 2],
        })
        inp = tmp_path / "norm.parquet"
        df.write_parquet(inp)
        lib_dict = tmp_path / "lib.json"
        lib_dict.write_text(json.dumps({"LIB": {"A": 4}}))
        out = tmp_path / "enrich.parquet"
        enrichment(["--input", str(inp), "--library-dict", str(lib_dict), "--output", str(out)])
        result = pl.read_parquet(out).sort("compound_id")

        assert result["polyo"][0] == pytest.approx(0.369535, rel=1e-4)
        assert result["polyo"][1] == pytest.approx(0.077659, rel=1e-4)

    def test_polyo_uses_corrected_count(self, tmp_path):
        # raw_count != corrected_count: calibration uses raw, PMF uses corrected.
        # d = (10+4)/4 = 3.5 (from raw); polyo PMF uses corrected [8, 2].
        # If raw were used instead: polyo would be 0.302210 and 0.082903 (different).
        df = pl.DataFrame({
            "compound_id":     ["LIB-1", "LIB-2"],
            "library_id":      ["LIB",   "LIB"],
            "raw_count":       [10, 4],
            "corrected_count": [8,  2],
        })
        inp = tmp_path / "norm.parquet"
        df.write_parquet(inp)
        lib_dict = tmp_path / "lib.json"
        lib_dict.write_text(json.dumps({"LIB": {"A": 4}}))
        out = tmp_path / "enrich.parquet"
        enrichment(["--input", str(inp), "--library-dict", str(lib_dict), "--output", str(out)])
        result = pl.read_parquet(out).sort("compound_id")

        assert result["polyo"][0] == pytest.approx(0.203030, rel=1e-4)
        assert result["polyo"][1] == pytest.approx(0.083929, rel=1e-4)

    def test_output_row_count_unchanged(self, normalized_parquet, library_dict_json, tmp_path):
        out = tmp_path / "enrichment.parquet"
        enrichment(["--input", str(normalized_parquet), "--library-dict", str(library_dict_json), "--output", str(out)])
        df = pl.read_parquet(out)
        assert len(df) == 3

    def test_missing_input_fails(self, library_dict_json, tmp_path):
        with pytest.raises(SystemExit):
            enrichment(["--input", str(tmp_path / "nonexistent.parquet"), "--library-dict", str(library_dict_json), "--output", str(tmp_path / "out.parquet")])

    def test_missing_library_dict_fails(self, normalized_parquet, tmp_path):
        with pytest.raises(SystemExit):
            enrichment(["--input", str(normalized_parquet), "--library-dict", str(tmp_path / "nonexistent.json"), "--output", str(tmp_path / "out.parquet")])

    def test_z_score_values(self, tmp_path):
        df = pl.DataFrame({
            "compound_id":     ["LIB-1-2-3", "LIB-3-2-1"],
            "library_id":      ["LIB",   "LIB"],
            "raw_count":       [15, 2],
            "corrected_count": [9, 1],
        })
        inp = tmp_path / "norm.parquet"
        df.write_parquet(inp)
        lib_dict = tmp_path / "lib.json"
        lib_dict.write_text(json.dumps({"LIB": {"A": 10, "B": 100, "C": 100}}))
        out = tmp_path / "enrich.parquet"
        enrichment(["--input", str(inp), "--library-dict", str(lib_dict), "--output", str(out)])
        result = pl.read_parquet(out).sort("compound_id")
        z = result["z_score_lib"].to_list()
        assert z[0] == pytest.approx(284.6032)
        assert z[1] == pytest.approx(31.619772)

    def test_z_score_is_per_library(self, tmp_path):
        # Two libraries, each with 2 compounds of equal count.
        # Within each library the z-score should be 0 (counts equal to expected).
        # A global z-score would give non-zero values if library sizes differ.
        df = pl.DataFrame({
            "compound_id":     ["L01-1", "L01-2", "L02-1", "L02-2"],
            "library_id":      ["L01",   "L01",   "L02",   "L02"],
            "raw_count":       [5, 5, 100, 100],
            "corrected_count": [5, 5, 100, 100],
        })
        inp = tmp_path / "norm.parquet"
        df.write_parquet(inp)
        lib_dict = tmp_path / "lib.json"
        lib_dict.write_text(json.dumps({"L01": {"A": 2}, "L02": {"A": 2}}))
        out = tmp_path / "enrich.parquet"
        enrichment(["--input", str(inp), "--library-dict", str(lib_dict), "--output", str(out)])
        result = pl.read_parquet(out)
        assert result["z_score_lib"].to_list() == pytest.approx([0.0, 0.0, 0.0, 0.0])
        # global z-score uses both libraries together, so L01 (count=5) and L02 (count=100)
        # are compared against a shared expected value — result is non-zero
        assert any(z != pytest.approx(0.0) for z in result["z_score_global"].to_list())

    def test_z_score_global_values(self, tmp_path):
        # L01: counts [10, 10] (equal → lib z=0), L02: counts [6, 2].
        # Global: n_compounds=4, n_total=28, c_expected=7,
        #   denom_norm = sqrt(7*0.75) * sqrt(28) = sqrt(147) = 7*sqrt(3).
        df = pl.DataFrame({
            "compound_id":     ["L01-1", "L01-2", "L02-1", "L02-2"],
            "library_id":      ["L01",   "L01",   "L02",   "L02"],
            "raw_count":       [10, 10, 6, 2],
            "corrected_count": [10, 10, 6, 2],
        })
        inp = tmp_path / "norm.parquet"
        df.write_parquet(inp)
        lib_dict = tmp_path / "lib.json"
        lib_dict.write_text(json.dumps({"L01": {"A": 2}, "L02": {"A": 2}}))
        out = tmp_path / "enrich.parquet"
        enrichment(["--input", str(inp), "--library-dict", str(lib_dict), "--output", str(out)])
        result = pl.read_parquet(out).sort("compound_id")
        dn = 7 * math.sqrt(3)
        assert result["z_score_global"].to_list() == pytest.approx([3/dn, 3/dn, -1/dn, -5/dn])

    def test_missing_required_args_fails(self):
        with pytest.raises(SystemExit):
            enrichment([])


class TestDisynthons:
    def _write_input(self, tmp_path, corrected_counts, cycles=3):
        """3-cycle input by default: L01, A in {1,2}, B in {1}, C in {1,2}."""
        if cycles == 3:
            df = pl.DataFrame({
                "compound_id":     ["L01-1-1-1", "L01-1-1-2", "L01-2-1-1", "L01-2-1-2"],
                "library_id":      ["L01"] * 4,
                "A":               ["1", "1", "2", "2"],
                "B":               ["1", "1", "1", "1"],
                "C":               ["1", "2", "1", "2"],
                "raw_count":       corrected_counts,
                "corrected_count": corrected_counts,
            })
            lib_dict = {"L01": {"A": 2, "B": 1, "C": 2}}
        else:
            df = pl.DataFrame({
                "compound_id":     ["L01-1-1", "L01-1-2"],
                "library_id":      ["L01", "L01"],
                "A":               ["1", "1"],
                "B":               ["1", "2"],
                "raw_count":       corrected_counts,
                "corrected_count": corrected_counts,
            })
            lib_dict = {"L01": {"A": 1, "B": 2}}
        inp = tmp_path / "norm.parquet"
        df.write_parquet(inp)
        lib = tmp_path / "lib.json"
        lib.write_text(json.dumps(lib_dict))
        return inp, lib

    def test_runs_and_writes_output(self, tmp_path):
        inp, lib = self._write_input(tmp_path, [4, 2, 1, 1])
        disynthons(["--input", str(inp), "--library-dict", str(lib), "--output-dir", str(tmp_path / "out")])
        assert (tmp_path / "out" / "disynthons_AB.parquet").exists()

    def test_3_cycle_produces_ab_bc_ac(self, tmp_path):
        inp, lib = self._write_input(tmp_path, [4, 2, 1, 1])
        out = tmp_path / "out"
        disynthons(["--input", str(inp), "--library-dict", str(lib), "--output-dir", str(out)])
        assert (out / "disynthons_AB.parquet").exists()
        assert (out / "disynthons_BC.parquet").exists()
        assert (out / "disynthons_AC.parquet").exists()

    def test_2_cycle_produces_only_ab(self, tmp_path):
        inp, lib = self._write_input(tmp_path, [6, 2], cycles=2)
        out = tmp_path / "out"
        disynthons(["--input", str(inp), "--library-dict", str(lib), "--output-dir", str(out)])
        assert (out / "disynthons_AB.parquet").exists()
        assert not (out / "disynthons_BC.parquet").exists()
        assert not (out / "disynthons_AC.parquet").exists()

    def test_ab_aggregates_over_c(self, tmp_path):
        # A1-B1: 4+2=6, A2-B1: 1+1=2
        inp, lib = self._write_input(tmp_path, [4, 2, 1, 1])
        out = tmp_path / "out"
        disynthons(["--input", str(inp), "--library-dict", str(lib), "--output-dir", str(out)])
        df = pl.read_parquet(out / "disynthons_AB.parquet").sort(["A", "B"])
        assert df["corrected_count"].to_list() == [6, 2]

    def test_ab_statistics(self, tmp_path):
        # library: A=2, B=1, C=2 → tot_compounds per AB = C = 2
        # A1-B1: counts [4,2] → sum=6, mean=3.0, sum_sq=20, std=sqrt(20/2-9)=1.0
        # A2-B1: counts [1,1] → sum=2, mean=1.0, sum_sq=2,  std=sqrt(2/2-1)=0.0
        # z-score: n_disynthons=A*B=2, n_total=8, c_expected=4
        # z = (count - 4) / sqrt(2) / sqrt(8) = (count - 4) / 4
        inp, lib = self._write_input(tmp_path, [4, 2, 1, 1])
        out = tmp_path / "out"
        disynthons(["--input", str(inp), "--library-dict", str(lib), "--output-dir", str(out)])
        df = pl.read_parquet(out / "disynthons_AB.parquet").sort(["A", "B"])
        assert df["tot_compounds"].to_list() == [2, 2]
        assert df["mean_count"].to_list() == pytest.approx([3.0, 1.0])
        assert df["std_count"].to_list() == pytest.approx([1.0, 0.0])
        assert df["z_score_lib"].to_list() == pytest.approx([0.5, -0.5])

    def test_multi_library_not_mixed(self, tmp_path):
        # Two libraries both have A="1", B="1" — must stay separate.
        # L01: A=2, B=1, C=2 → AB tot_compounds=2; A1-B1 sum=6
        # L02: A=1, B=1       → AB tot_compounds=1; A1-B1 sum=3
        df = pl.DataFrame({
            "compound_id":     ["L01-1-1-1", "L01-1-1-2", "L02-1-1"],
            "library_id":      ["L01",        "L01",        "L02"],
            "A":               ["1",          "1",          "1"],
            "B":               ["1",          "1",          "1"],
            "C":               ["1",          "2",          None],
            "raw_count":       [4, 2, 3],
            "corrected_count": [4, 2, 3],
        })
        inp = tmp_path / "norm.parquet"
        df.write_parquet(inp)
        lib_dict = {"L01": {"A": 2, "B": 1, "C": 2}, "L02": {"A": 1, "B": 1}}
        lib = tmp_path / "lib.json"
        lib.write_text(json.dumps(lib_dict))
        out = tmp_path / "out"
        disynthons(["--input", str(inp), "--library-dict", str(lib), "--output-dir", str(out)])
        ab = pl.read_parquet(out / "disynthons_AB.parquet").sort(["library_id", "A", "B"])
        assert ab["library_id"].to_list() == ["L01", "L02"]
        assert ab["corrected_count"].to_list() == [6, 3]
        assert ab["tot_compounds"].to_list() == [2, 1]
        # L01: z-score well-defined; L02 has n_disynthons=1 → z-score undefined (NaN)
        assert ab["z_score_lib"][0] == pytest.approx(1.0)
        assert math.isnan(ab["z_score_lib"][1])
        # global z-score: n_disynthons_total=3, n_total=9, c_expected=3,
        #   denom_norm = sqrt(3*2/3) * sqrt(9) = sqrt(2)*3 = 3*sqrt(2).
        # L02 global z-score is 0 (not NaN) even though lib z-score is undefined.
        dn = 3 * math.sqrt(2)
        assert ab["z_score_global"][0] == pytest.approx(3/dn)
        assert ab["z_score_global"][1] == pytest.approx(0.0)

    def test_missing_input_fails(self, tmp_path):
        with pytest.raises(SystemExit):
            disynthons(["--input", str(tmp_path / "nonexistent.parquet"),
                        "--library-dict", str(tmp_path / "lib.json"),
                        "--output-dir", str(tmp_path / "out")])

    def test_missing_required_args_fails(self):
        with pytest.raises(SystemExit):
            disynthons([])

    def test_disynthon_has_polyo(self, tmp_path):
        inp, lib = self._write_input(tmp_path, [4, 2, 1, 1])
        out = tmp_path / "out"
        disynthons(["--input", str(inp), "--library-dict", str(lib), "--output-dir", str(out)])
        df = pl.read_parquet(out / "disynthons_AB.parquet")
        assert "polyo" in df.columns
        assert df["polyo"].is_finite().all()

    def test_polyo_higher_for_enriched_disynthon(self, tmp_path):
        # A1-B1 sum=6, A2-B1 sum=2 — A1-B1 has higher polyO.
        inp, lib = self._write_input(tmp_path, [4, 2, 1, 1])
        out = tmp_path / "out"
        disynthons(["--input", str(inp), "--library-dict", str(lib), "--output-dir", str(out)])
        df = pl.read_parquet(out / "disynthons_AB.parquet").sort(["A", "B"])
        assert df["polyo"][0] > df["polyo"][1]

    def test_polyo_values(self, tmp_path):
        # Library L01: A=2, B=1, C=2 → 4 compounds; raw_count [4,2,1,1], total=8.
        # d = 8/4 = 2.0; _total_disynthons = A*B+A*C+B*C = 8; c_cpd=4, c_read=7.
        # A1-B1 raw = sum(-log10 P(4;2), -log10 P(2;2)); A2-B1 raw = 2*(-log10 P(1;2)).
        inp, lib = self._write_input(tmp_path, [4, 2, 1, 1])
        out = tmp_path / "out"
        disynthons(["--input", str(inp), "--library-dict", str(lib), "--output-dir", str(out)])
        df = pl.read_parquet(out / "disynthons_AB.parquet").sort(["A", "B"])

        assert df["polyo"][0] == pytest.approx(0.163592, rel=1e-4)
        assert df["polyo"][1] == pytest.approx(0.115179, rel=1e-4)

    def test_polyo_uses_corrected_count(self, tmp_path):
        # raw_count != corrected_count: d = 8/4 = 2.0 from raw; PMF uses corrected.
        # A1-B1: corrected [3,1] → polyo 0.133047 (not 0.163592 which raw [4,2] would give).
        # A2-B1: corrected == raw [1,1] → same as always, 0.115179.
        df = pl.DataFrame({
            "compound_id":     ["L01-1-1-1", "L01-1-1-2", "L01-2-1-1", "L01-2-1-2"],
            "library_id":      ["L01"] * 4,
            "A":               ["1", "1", "2", "2"],
            "B":               ["1", "1", "1", "1"],
            "C":               ["1", "2", "1", "2"],
            "raw_count":       [4, 2, 1, 1],
            "corrected_count": [3, 1, 1, 1],
        })
        inp = tmp_path / "norm.parquet"
        df.write_parquet(inp)
        lib = tmp_path / "lib.json"
        lib.write_text(json.dumps({"L01": {"A": 2, "B": 1, "C": 2}}))
        out = tmp_path / "out"
        disynthons(["--input", str(inp), "--library-dict", str(lib), "--output-dir", str(out)])
        result = pl.read_parquet(out / "disynthons_AB.parquet").sort(["A", "B"])

        assert result["polyo"][0] == pytest.approx(0.133047, rel=1e-4)
        assert result["polyo"][1] == pytest.approx(0.115179, rel=1e-4)


class TestDisynthonsComprehensive:
    """Full 3-cycle library: L01 with A=2, B=2, C=2 (all 8 compounds).

    Counts: A1-B1-C1=6, A1-B1-C2=2, A1-B2-C1=4, A1-B2-C2=4,
            A2-B1-C1=1, A2-B1-C2=1, A2-B2-C1=1, A2-B2-C2=1.  n_total=20.

    All three pairs share the same z-score denominator:
      n_disynthons=4, c_expected=5, denom*norm = sqrt(3.75)*sqrt(20) = 5*sqrt(3)
    """

    @pytest.fixture
    def disynthon_tables(self, tmp_path):
        df = pl.DataFrame({
            "compound_id":     ["L01-1-1-1", "L01-1-1-2", "L01-1-2-1", "L01-1-2-2",
                                "L01-2-1-1", "L01-2-1-2", "L01-2-2-1", "L01-2-2-2"],
            "library_id":      ["L01"] * 8,
            "A":               ["1", "1", "1", "1", "2", "2", "2", "2"],
            "B":               ["1", "1", "2", "2", "1", "1", "2", "2"],
            "C":               ["1", "2", "1", "2", "1", "2", "1", "2"],
            "raw_count":       [6, 2, 4, 4, 1, 1, 1, 1],
            "corrected_count": [6, 2, 4, 4, 1, 1, 1, 1],
        })
        inp = tmp_path / "norm.parquet"
        df.write_parquet(inp)
        lib = tmp_path / "lib.json"
        lib.write_text(json.dumps({"L01": {"A": 2, "B": 2, "C": 2}}))
        out = tmp_path / "out"
        disynthons(["--input", str(inp), "--library-dict", str(lib), "--output-dir", str(out)])
        denom_norm = 5 * math.sqrt(3)  # sqrt(3.75) * sqrt(20)
        return {
            "AB": pl.read_parquet(out / "disynthons_AB.parquet").sort(["A", "B"]),
            "BC": pl.read_parquet(out / "disynthons_BC.parquet").sort(["B", "C"]),
            "AC": pl.read_parquet(out / "disynthons_AC.parquet").sort(["A", "C"]),
            "denom_norm": denom_norm,
        }

    def test_ab_counts(self, disynthon_tables):
        # A1-B1: 6+2=8, A1-B2: 4+4=8, A2-B1: 1+1=2, A2-B2: 1+1=2
        ab = disynthon_tables["AB"]
        assert ab["corrected_count"].to_list() == [8, 8, 2, 2]
        assert ab["tot_compounds"].to_list() == [2, 2, 2, 2]
        assert ab["mean_count"].to_list() == pytest.approx([4.0, 4.0, 1.0, 1.0])

    def test_bc_counts(self, disynthon_tables):
        # B1-C1: 6+1=7, B1-C2: 2+1=3, B2-C1: 4+1=5, B2-C2: 4+1=5
        bc = disynthon_tables["BC"]
        assert bc["corrected_count"].to_list() == [7, 3, 5, 5]
        assert bc["tot_compounds"].to_list() == [2, 2, 2, 2]

    def test_ac_counts(self, disynthon_tables):
        # A1-C1: 6+4=10, A1-C2: 2+4=6, A2-C1: 1+1=2, A2-C2: 1+1=2
        ac = disynthon_tables["AC"]
        assert ac["corrected_count"].to_list() == [10, 6, 2, 2]
        assert ac["tot_compounds"].to_list() == [2, 2, 2, 2]

    def test_ab_z_scores(self, disynthon_tables):
        dn = disynthon_tables["denom_norm"]
        z = disynthon_tables["AB"]["z_score_lib"].to_list()
        assert z == pytest.approx([3/dn, 3/dn, -3/dn, -3/dn])

    def test_bc_z_scores(self, disynthon_tables):
        dn = disynthon_tables["denom_norm"]
        z = disynthon_tables["BC"]["z_score_lib"].to_list()
        assert z == pytest.approx([2/dn, -2/dn, 0.0, 0.0])

    def test_ac_z_scores(self, disynthon_tables):
        dn = disynthon_tables["denom_norm"]
        z = disynthon_tables["AC"]["z_score_lib"].to_list()
        assert z == pytest.approx([5/dn, 1/dn, -3/dn, -3/dn])


# ---------------------------------------------------------------------------
# NormalizeCustom
# ---------------------------------------------------------------------------

class TestNormalizeCustom:
    def _write(self, tmp_path, data: dict) -> str:
        p = tmp_path / "input.parquet"
        pl.DataFrame(data).write_parquet(p)
        return str(p)

    def test_compound_col_mode(self, tmp_path):
        inp = self._write(tmp_path, {
            "cpd":   ["L01-1-2-3", "L01-1-2-4"],
            "count": [10, 5],
        })
        out = tmp_path / "out.parquet"
        normalize_custom(["--input", inp, "--output", str(out),
                          "--compound-col", "cpd", "--corrected-count-col", "count"])
        df = pl.read_parquet(out)
        assert "compound_id" in df.columns
        assert "library_id" in df.columns
        assert "A" in df.columns and "B" in df.columns and "C" in df.columns
        assert df["compound_id"].to_list() == ["L01-1-2-3", "L01-1-2-4"]
        assert df["library_id"].to_list() == ["L01", "L01"]
        assert df["A"].to_list() == ["1", "1"]
        assert df["corrected_count"].to_list() == [10, 5]

    def test_library_bb_ids_mode(self, tmp_path):
        inp = self._write(tmp_path, {
            "lib":    ["L01", "L01"],
            "bb_ids": ["1,2,3", "1,2,4"],
            "count":  [10, 5],
        })
        out = tmp_path / "out.parquet"
        normalize_custom(["--input", inp, "--output", str(out),
                          "--library-col", "lib", "--bb-ids-col", "bb_ids",
                          "--corrected-count-col", "count"])
        df = pl.read_parquet(out)
        assert df["compound_id"].to_list() == ["L01-1-2-3", "L01-1-2-4"]
        assert df["library_id"].to_list() == ["L01", "L01"]
        assert df["B"].to_list() == ["2", "2"]
        assert "bb_ids" not in df.columns

    def test_library_cycle_cols_mode(self, tmp_path):
        inp = self._write(tmp_path, {
            "lib":   ["L01", "L01"],
            "cycle1": ["1", "1"],
            "cycle2": ["2", "2"],
            "cycle3": ["3", "4"],
            "count": [10, 5],
        })
        out = tmp_path / "out.parquet"
        normalize_custom(["--input", inp, "--output", str(out),
                          "--library-col", "lib", "--cycle-cols", "cycle1", "cycle2", "cycle3",
                          "--corrected-count-col", "count"])
        df = pl.read_parquet(out)
        assert df["compound_id"].to_list() == ["L01-1-2-3", "L01-1-2-4"]
        assert df["A"].to_list() == ["1", "1"]
        assert df["C"].to_list() == ["3", "4"]

    def test_raw_count_included_when_provided(self, tmp_path):
        inp = self._write(tmp_path, {
            "cpd": ["L01-1-2", "L01-1-3"], "count": [10, 5], "raw": [12, 6],
        })
        out = tmp_path / "out.parquet"
        normalize_custom(["--input", inp, "--output", str(out),
                          "--compound-col", "cpd", "--corrected-count-col", "count",
                          "--raw-count-col", "raw"])
        df = pl.read_parquet(out)
        assert "raw_count" in df.columns
        assert df["raw_count"].to_list() == [12, 6]

    def test_raw_count_absent_when_not_provided(self, tmp_path):
        inp = self._write(tmp_path, {"cpd": ["L01-1-2"], "count": [10]})
        out = tmp_path / "out.parquet"
        normalize_custom(["--input", inp, "--output", str(out),
                          "--compound-col", "cpd", "--corrected-count-col", "count"])
        assert "raw_count" not in pl.read_parquet(out).columns

    def test_z_score_col_renamed(self, tmp_path):
        inp = self._write(tmp_path, {"cpd": ["L01-1-2"], "count": [10], "zscore": [1.5]})
        out = tmp_path / "out.parquet"
        normalize_custom(["--input", inp, "--output", str(out),
                          "--compound-col", "cpd", "--corrected-count-col", "count",
                          "--z-score-col", "zscore"])
        df = pl.read_parquet(out)
        assert "z_score" in df.columns
        assert "zscore" not in df.columns
        assert df["z_score"][0] == pytest.approx(1.5)

    def test_smiles_col_renamed(self, tmp_path):
        inp = self._write(tmp_path, {"cpd": ["L01-1-2"], "count": [10], "smi": ["CCO"]})
        out = tmp_path / "out.parquet"
        normalize_custom(["--input", inp, "--output", str(out),
                          "--compound-col", "cpd", "--corrected-count-col", "count",
                          "--smiles-col", "smi"])
        df = pl.read_parquet(out)
        assert "SMILES" in df.columns
        assert df["SMILES"][0] == "CCO"

    def test_compound_col_library_name_with_dash(self, tmp_path):
        """Library names containing '-' must be preserved when splitting compound_id."""
        inp = self._write(tmp_path, {
            "cpd":   ["LIB-1-1-2-3", "LIB-1-1-2-4"],
            "count": [10, 5],
        })
        out = tmp_path / "out.parquet"
        normalize_custom(["--input", inp, "--output", str(out),
                          "--compound-col", "cpd", "--corrected-count-col", "count",
                          "--num-cycles", "3"])
        df = pl.read_parquet(out)
        assert df["library_id"].to_list() == ["LIB-1", "LIB-1"]
        assert df["A"].to_list() == ["1", "1"]
        assert df["B"].to_list() == ["2", "2"]
        assert df["C"].to_list() == ["3", "4"]

    def test_ambiguous_mode_fails(self, tmp_path):
        inp = self._write(tmp_path, {"cpd": ["L01-1"], "count": [1]})
        out = tmp_path / "out.parquet"
        with pytest.raises(SystemExit):
            normalize_custom(["--input", inp, "--output", str(out),
                              "--compound-col", "cpd",
                              "--library-col", "lib", "--bb-ids-col", "bb_ids",
                              "--corrected-count-col", "count"])

    def test_no_mode_fails(self, tmp_path):
        inp = self._write(tmp_path, {"cpd": ["L01-1"], "count": [1]})
        out = tmp_path / "out.parquet"
        with pytest.raises(SystemExit):
            normalize_custom(["--input", inp, "--output", str(out),
                              "--corrected-count-col", "count"])

    def test_missing_input_fails(self, tmp_path):
        with pytest.raises(SystemExit):
            normalize_custom(["--input", str(tmp_path / "nope.parquet"),
                              "--output", str(tmp_path / "out.parquet"),
                              "--compound-col", "cpd", "--corrected-count-col", "count"])


# ---------------------------------------------------------------------------
# Enrichment — optional raw_count / pre-supplied z_score
# ---------------------------------------------------------------------------

class TestEnrichmentOptionalRaw:
    def _lib_dict(self, tmp_path) -> str:
        p = tmp_path / "lib.json"
        p.write_text(json.dumps({"LIB": {"A": 2}}))
        return str(p)

    def test_no_raw_count_skips_polyo(self, tmp_path):
        df = pl.DataFrame({
            "compound_id":     ["LIB-1", "LIB-2"],
            "library_id":      ["LIB", "LIB"],
            "corrected_count": [10, 2],
        })
        inp = tmp_path / "norm.parquet"
        df.write_parquet(inp)
        out = tmp_path / "enrich.parquet"
        enrichment(["--input", str(inp), "--library-dict", self._lib_dict(tmp_path), "--output", str(out)])
        result = pl.read_parquet(out)
        assert "polyo" not in result.columns
        assert "z_score_lib" in result.columns
        assert "z_score_global" in result.columns

    def test_presupplied_z_score_carried_through(self, tmp_path):
        df = pl.DataFrame({
            "compound_id":     ["LIB-1", "LIB-2"],
            "library_id":      ["LIB", "LIB"],
            "corrected_count": [10, 2],
            "raw_count":       [10, 2],
            "z_score":         [1.5, -0.5],
        })
        inp = tmp_path / "norm.parquet"
        df.write_parquet(inp)
        out = tmp_path / "enrich.parquet"
        enrichment(["--input", str(inp), "--library-dict", self._lib_dict(tmp_path), "--output", str(out)])
        result = pl.read_parquet(out)
        assert "z_score" in result.columns
        assert "z_score_lib" not in result.columns
        assert "z_score_global" not in result.columns
        assert result["z_score"].to_list() == pytest.approx([1.5, -0.5])

    def test_presupplied_z_score_still_calculates_polyo(self, tmp_path):
        df = pl.DataFrame({
            "compound_id":     ["LIB-1", "LIB-2"],
            "library_id":      ["LIB", "LIB"],
            "corrected_count": [10, 2],
            "raw_count":       [10, 2],
            "z_score":         [1.5, -0.5],
        })
        inp = tmp_path / "norm.parquet"
        df.write_parquet(inp)
        out = tmp_path / "enrich.parquet"
        enrichment(["--input", str(inp), "--library-dict", self._lib_dict(tmp_path), "--output", str(out)])
        result = pl.read_parquet(out)
        assert "polyo" in result.columns


# ---------------------------------------------------------------------------
# Disynthons — optional raw_count
# ---------------------------------------------------------------------------

class TestDisynthonsOptionalRaw:
    def _write_input(self, tmp_path, include_raw: bool) -> tuple:
        data = {
            "compound_id":     ["L01-1-1-1", "L01-1-1-2", "L01-2-1-1", "L01-2-1-2"],
            "library_id":      ["L01"] * 4,
            "A":               ["1", "1", "2", "2"],
            "B":               ["1", "1", "1", "1"],
            "C":               ["1", "2", "1", "2"],
            "corrected_count": [4, 2, 1, 1],
        }
        if include_raw:
            data["raw_count"] = [4, 2, 1, 1]
        inp = tmp_path / "norm.parquet"
        pl.DataFrame(data).write_parquet(inp)
        lib = tmp_path / "lib.json"
        lib.write_text(json.dumps({"L01": {"A": 2, "B": 1, "C": 2}}))
        return inp, lib

    def test_no_raw_count_skips_polyo(self, tmp_path):
        inp, lib = self._write_input(tmp_path, include_raw=False)
        out = tmp_path / "out"
        disynthons(["--input", str(inp), "--library-dict", str(lib), "--output-dir", str(out)])
        df = pl.read_parquet(out / "disynthons_AB.parquet")
        assert "polyo" not in df.columns
        assert "z_score_lib" in df.columns

    def test_with_raw_count_includes_polyo(self, tmp_path):
        inp, lib = self._write_input(tmp_path, include_raw=True)
        out = tmp_path / "out"
        disynthons(["--input", str(inp), "--library-dict", str(lib), "--output-dir", str(out)])
        df = pl.read_parquet(out / "disynthons_AB.parquet")
        assert "polyo" in df.columns
