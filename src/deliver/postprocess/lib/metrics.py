"""Scientific metrics for DEL postprocessing."""

import math

import polars as pl
from scipy.stats import poisson as _poisson


# -- z-score ------------------------------------------------------------------

def z_score(corrected_count: pl.Series, n_compounds: int) -> pl.Series:
    """Binomial enrichment z-score for a series of counts.

    Returns NaN when z-score is undefined (n_compounds == 1).
    http://dx.doi.org/10.1021/acscombsci.8b00116

    Formula from the paper:
        zn = sqrt(pi / (1 - pi)) * (po / pi - 1)

    where:
        po = corrected_count / n_total   (observed probability)
        pi = 1 / n_compounds             (expected probability under uniform null)

    Substituting pi = 1 / n_compounds:
        zn = sqrt(1 / (n_compounds - 1)) * (corrected_count * n_compounds / n_total - 1)
           = (corrected_count * n_compounds / n_total - 1) / sqrt(n_compounds - 1)

    corrected_count - counts for a feature
    n_compounds - total number of possible feature values
    """
    n_total = corrected_count.sum()
    denom = math.sqrt(n_compounds - 1)
    if denom == 0:
        return pl.Series([float("nan")] * len(corrected_count))
    return (corrected_count * (n_compounds / n_total) - 1) / denom


def z_score_expr(corrected_count: pl.Expr, n_total: int, n_compounds: int) -> pl.Expr:
    """z_score as a polars expression, for streaming tables too large to load.

    Same formula and the same floating-point operations in the same order as
    z_score, so the values are identical; n_total (the sum of the counts the
    z-score is over) must be computed beforehand, since an expression over a
    stream cannot see the whole column.
    """
    denom = math.sqrt(n_compounds - 1)
    if denom == 0:
        return pl.lit(float("nan"), dtype=pl.Float64)
    return (corrected_count * (n_compounds / n_total) - 1) / denom


# -- polyO --------------------------------------------------------------------

class PolyO:
    """PolyO enrichment metric for DEL compounds and disynthons.

    https://doi.org/10.1093/nar/gkac173
    d          - expected corrected reads per possible compound globally
    n_reads    - total corrected reads in library (used for s_bar)
    n_obs      - number of unique compounds observed
    n_possible - total possible compounds in library
    n_features - feature space for c_cpd: n_possible (compound) or n_disynthons (disynthon)
    """

    def __init__(self, d: float, n_reads: int, n_obs: int, n_possible: int, n_features: int):
        self.d = d
        self.s_bar = n_reads / n_possible
        self.c_read = self._poisson_threshold(self.s_bar)
        self.c_cpd = self._poisson_threshold(n_obs / n_features)

    @staticmethod
    def _poisson_threshold(mu: float) -> int:
        """Smallest k above floor(mu) where Poisson PMF first drops to <= 0.01."""
        k = math.floor(mu)
        p = 1.0
        while p > 0.01:
            k += 1
            p = _poisson.pmf(k, mu)
        return k

    def raw(self, count: pl.Series) -> pl.Series:
        """Raw polyO: -log10(P(count; d)) under Poisson(d) null."""
        return pl.Series(-_poisson.logpmf(count.to_numpy(), self.d) / math.log(10))

    def score(self, raw: pl.Series) -> pl.Series:
        """Normalized polyO score relative to calibrated baseline."""
        denom = self.c_cpd * (-_poisson.logpmf(self.c_read, self.s_bar) / math.log(10))
        return raw / denom

    def score_expr(self, count: pl.Expr) -> pl.Expr:
        """score(raw(count)) as a streaming polars expression.

        Applies the same two functions batch by batch; both are element-wise,
        so the values are identical to calling them on the whole column.
        """
        return count.map_batches(
            lambda batch: self.score(self.raw(batch)), return_dtype=pl.Float64, is_elementwise=True,
        )
