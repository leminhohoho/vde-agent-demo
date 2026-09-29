"""Sufficiency Gate (pipeline step 3, spec §5.5). Phase P1.

Pure and deterministic (luật 11): no I/O, no LLM. Computes valid/missing rates, coverage, n_eff,
IQR outliers, freshness and the MNAR gap from the input artifacts, and returns `dq_flags` and the
starting confidence per subject. Thresholds come from `SemanticConfig.params`."""
