"""frontier 纯图算法：依赖解锁 / deferred 阻塞 / 排序。直接调用模块函数。"""
from __future__ import annotations

import dd as ddmod  # scripts/dd.py


def q(qid, status="open", deps=(), round_=1):
    return {"id": qid, "status": status, "depends_on": list(deps), "round": round_,
            "title": f"t{qid}", "dimension": "scope"}


def test_no_deps_all_open_in_frontier():
    qs = [q("Q001"), q("Q002"), q("Q003")]
    frontier, blocked = ddmod.compute_frontier(qs)
    assert [x["id"] for x in frontier] == ["Q001", "Q002", "Q003"]
    assert blocked == []


def test_chain_blocks_until_dep_answered():
    qs = [q("Q001"), q("Q002", deps=["Q001"]), q("Q003", deps=["Q002"], round_=3)]
    frontier, blocked = ddmod.compute_frontier(qs)
    assert [x["id"] for x in frontier] == ["Q001"]
    assert [x["id"] for x, _ in blocked] == ["Q002", "Q003"]
    # 阻塞源正确传导
    assert [d["id"] for d in blocked[1][1]] == ["Q002"]


def test_dep_answered_unblocks_downstream():
    qs = [q("Q001", "answered"), q("Q002", deps=["Q001"])]
    frontier, blocked = ddmod.compute_frontier(qs)
    assert [x["id"] for x in frontier] == ["Q002"]
    assert blocked == []


def test_deferred_dep_blocks_and_is_flagged():
    qs = [q("Q001", "deferred"), q("Q002", deps=["Q001"])]
    frontier, blocked = ddmod.compute_frontier(qs)
    assert frontier == []
    assert len(blocked) == 1
    q2, deps = blocked[0]
    assert q2["id"] == "Q002" and deps[0]["status"] == "deferred"


def test_answered_and_deferred_not_tracked():
    qs = [q("Q001", "answered"), q("Q002", "deferred")]
    frontier, blocked = ddmod.compute_frontier(qs)
    assert frontier == [] and blocked == []


def test_frontier_sorted_by_round_then_id():
    qs = [q("Q005"), q("Q002", round_=2), q("Q001"), q("Q004", round_=2)]
    frontier, _ = ddmod.compute_frontier(qs)
    assert [x["id"] for x in frontier] == ["Q001", "Q005", "Q002", "Q004"]


def test_mixed_statuses_partial_frontier():
    qs = [
        q("Q001", "answered"),
        q("Q002", deps=["Q001"]),
        q("Q003", "deferred"),
        q("Q004", deps=["Q003"]),
        q("Q005", deps=["Q999"]),  # 悬空依赖按不存在处理，不进 deps
    ]
    frontier, blocked = ddmod.compute_frontier(qs)
    assert {x["id"] for x in frontier} == {"Q002", "Q005"}
    assert [x["id"] for x, _ in blocked] == ["Q004"]
