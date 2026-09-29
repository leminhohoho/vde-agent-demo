"""Confidence, KEY/SUPPORTING and artifact status (pipeline step 9, spec §5.2, §4.4). Phase P2.

Pure: applies the confidence table, the KEY rule (evidence + two-branch lineage + confidence ≠ LOW
+ no open CONFLICT, at most `max_key_insights`) and the VALID/PARTIAL/INVALID rules. The LLM never
assigns these."""
