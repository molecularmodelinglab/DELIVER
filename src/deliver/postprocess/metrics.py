"""Scientific metrics for DEL postprocessing."""

import math

import polars as pl


def z_score(corrected_count: pl.Series, n_compounds: int) -> pl.Series:
    """Binomial enrichment z-score for a series of counts.

    Returns NaN when z-score is undefined (n_compounds == 1).
    http://dx.doi.org/10.1021/acscombsci.8b00116
    corrected_count - counts for a feature
    n_compounds - total number of possible feature values
    """
    n_total = corrected_count.sum()
    c_expected = n_total / n_compounds
    denom = math.sqrt(c_expected * (1 - c_expected / n_total))
    if denom == 0:
        return pl.Series([float("nan")] * len(corrected_count))
    return (corrected_count - c_expected) / denom / math.sqrt(n_total)
