"""Pre-flight (pipeline step 5, spec §6.4): candidate cut, token count, fit to max_input_tokens. TC-24."""

from __future__ import annotations

from ..contracts import InsightCandidate
from ..llm.preflight import preflight
from .builders import candidate


class Counter:
    def __init__(self, per_candidate: int = 10, fail: bool = False) -> None:
        self.per_candidate = per_candidate
        self.fail = fail
        self.calls = 0

    async def count_tokens(self, *, system: str, user: str) -> int:
        self.calls += 1
        if self.fail:
            raise RuntimeError("count endpoint down")
        return self.per_candidate * user.count("|")


def render(cands: list[InsightCandidate]) -> str:
    return "".join(f"{c.candidate_id}|" for c in cands)


def seventy_five() -> list[InsightCandidate]:
    cands = [candidate(f"C-T1-{i:02d}", "T1", f"0.{i:02d}") for i in range(72)]
    return cands + [candidate(f"C-T7-{i}", "T7", "0") for i in range(3)]


async def test_tc24_forty_candidates_reach_the_prompt_every_t7_among_them() -> None:
    pf = await preflight(
        seventy_five(), system="S", render_user=render, max_candidates=40, max_input_tokens=16_000, counter=Counter()
    )
    assert len(pf.kept) == 40 and sum(c.task == "T7" for c in pf.kept) == 3
    assert len(pf.rejected) == 35 and {r.reason_code for r in pf.rejected} == {"CONTEXT_BUDGET"}
    assert (pf.input_tokens, pf.estimated) == (400, False)


async def test_over_the_token_budget_the_lowest_priority_goes_first_but_t7_stays() -> None:
    cands = [
        candidate("C-T1-a", "T1", "0.9"),
        candidate("C-T1-b", "T1", "0.5"),
        candidate("C-T1-c", "T1", "0.1"),
        candidate("C-T7-x", "T7", "0"),
    ]
    pf = await preflight(
        cands, system="S", render_user=render, max_candidates=40, max_input_tokens=250, counter=Counter(per_candidate=100)
    )
    assert [c.candidate_id for c in pf.kept] == ["C-T1-a", "C-T7-x"]
    assert [(r.candidate_id, r.reason_code) for r in pf.rejected] == [("C-T1-c", "CONTEXT_BUDGET"), ("C-T1-b", "CONTEXT_BUDGET")]
    assert pf.input_tokens == 200


async def test_a_failing_count_falls_back_to_chars_over_three() -> None:
    cands = [candidate("C-T1-a", "T1", "0.9")]
    pf = await preflight(
        cands, system="SSS", render_user=render, max_candidates=40, max_input_tokens=16_000, counter=Counter(fail=True)
    )
    assert pf.estimated is True and pf.input_tokens == -(-(3 + len("C-T1-a|")) // 3)
    pf2 = await preflight(cands, system="SSS", render_user=render, max_candidates=40, max_input_tokens=16_000, counter=None)
    assert pf2.estimated is True and pf2.input_tokens == pf.input_tokens
