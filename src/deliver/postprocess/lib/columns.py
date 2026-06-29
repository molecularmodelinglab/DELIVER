"""Column name constants for DEL postprocessing."""

COMPOUND_ID = "compound_id"
LIBRARY_ID = "library_id"
RAW_READS = "raw_reads"              # per-compound raw read count (after normalization)
RAW_READS_SUM = "raw_reads_sum"      # disynthon: sum of raw reads in group
CORRECTED_COUNT = "corrected_count"
CORRECTED_COUNT_SUM = "corrected_count_sum"  # disynthon: sum of corrected counts in group

Z_SCORE = "z_score"                 # pre-supplied z-score (carried through, not recalculated)
Z_SCORE_LIB = "z_score_lib_normalized"
Z_SCORE_GLOBAL = "z_score_global_normalized"

LINE_SIZE = "line_size"              # disynthon: number of singletons in the group (product of remaining cycles)
LINE_STRENGTH = "line_strength"      # disynthon: mean corrected count per singleton
LINE_STRENGTH_STD = "line_strength_std"  # disynthon: std of corrected count within group

POLYO = "polyo"
