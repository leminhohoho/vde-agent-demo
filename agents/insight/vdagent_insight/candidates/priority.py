"""Candidate priority (spec §6.4 step 1, §9.5).

Priority = attribution_score / severity_rank (else 0.5 for T2/T3, 0.3 for T5), plus recent_subject_boost. Phase P1.
"""
