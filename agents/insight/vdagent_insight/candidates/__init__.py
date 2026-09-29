"""Insight Candidate Engine (pipeline step 4, spec §6.2). Phase P1.

Pure (luật 11), `decimal.Decimal` throughout: one module per task (t1_unit, t2_distribution,
t3_pattern, t5_market, t7_limitation), `stats` (Wilson interval, bootstrap with a fixed seed) and
`priority` (§6.4.1, memory `recent_subject_boost`). Candidates lacking evidence go to
`rejected_candidates`."""
