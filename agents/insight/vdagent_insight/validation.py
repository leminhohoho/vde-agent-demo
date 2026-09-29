"""Validator (pipeline step 7, spec §5.3 GR-01→GR-08 and the related BR). Phase P2.

Pure: checks every LLM draft item against the candidates it was given and returns coded errors
(E10 NUMERIC_BINDING_VIOLATION, E11 EVIDENCE_MISSING, E12 FORBIDDEN_LANGUAGE, LANGUAGE_MISMATCH, …)
so the pipeline can repair once and fall back to TEMPLATE per item."""
