"""
Offline tests for contracts/truthfall.py.

Run:  python3 tests/test_truthfall.py

Uses tests/mock_genlayer.py (a minimal SDK stand-in), so these tests prove the
deterministic logic and the validator comparison rules. Live behaviour must
also be checked on Studio / Bradbury - see README.
"""

import importlib.util
import json
import pathlib
import sys
import traceback

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mock_genlayer as mg  # noqa: E402

mg.install()

CONTRACT = HERE.parent / "contracts" / "truthfall.py"
GEN = 10**18
SRC = "https://en.wikipedia.org/wiki/Mount_Everest"
ALICE = mg.Address("0x" + "a" * 40)
BOB = mg.Address("0x" + "b" * 40)


def load():
    spec = importlib.util.spec_from_file_location("truthfall", CONTRACT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.datetime = mg._FrozenDatetime
    return mod


def fresh():
    mg.world = mg.World()
    vars(mg)["world"] = mg.world
    mod = load()
    c = mod.Truthfall()
    c.__init__()
    mg.world.set_web(SRC, "Mount Everest is Earth's highest mountain above sea level, in the Himalayas.")
    return mod, c


def as_(addr, value=0):
    mg.world.sender = addr
    mg.world.value = value


def llm_batch(verdicts, role=None):
    mg.world.set_llm(lambda p: {"verdicts": list(verdicts), "note": "checked against the page"}, role)


def llm_single(verdict, role=None):
    mg.world.set_llm(lambda p: {"verdict": verdict, "note": "re-checked"}, role)


def seed(c, verdicts=("TRUE", "FALSE", "TRUE"), section="world"):
    llm_batch(verdicts)
    as_(ALICE)
    stmts = "\n".join(f"Statement number {i} about Everest" for i in range(len(verdicts)))
    return json.loads(c.add_cards(SRC, section, "Geography", stmts))


def expect_error(fn, contains):
    try:
        fn()
    except (mg.UserError, mg.ConsensusFailure) as e:
        msg = getattr(e, "message", str(e))
        assert contains in msg, f"expected {contains!r} in {msg!r}"
        return
    raise AssertionError(f"expected error containing {contains!r}")


def test_runner_header_is_alone():
    lines = CONTRACT.read_text().splitlines()
    assert lines[0].startswith('# { "Depends": "py-genlayer:')
    json.loads(lines[0][1:])
    assert not lines[1].startswith("#")


def test_no_forbidden_patterns_and_size():
    src = CONTRACT.read_text()
    assert "web.get" not in src and "strict_eq" not in src
    assert "@dataclass" not in src and "allow_storage" not in src
    assert "web.render" in src and "run_nondet_unsafe" in src
    assert len(src.encode()) < 18000, "Bradbury deploy gas cap: keep source small"


def test_add_cards_stores_verdicts_and_retires_unclear():
    mod, c = fresh()
    out = seed(c, ("TRUE", "FALSE", "UNCLEAR"))
    assert out["ids"] == [0, 1, 2]
    cards = json.loads(c.get_cards(0, 10))["cards"]
    assert [x["verdict"] for x in cards] == ["TRUE", "FALSE", "RETIRED"]
    assert cards[0]["source"] == SRC and cards[0]["topic"] == "Geography"


def test_add_cards_input_validation():
    mod, c = fresh()
    llm_batch(["TRUE"])
    as_(ALICE)
    expect_error(lambda: c.add_cards("http://x.com", "world", "t", "A valid long statement"), "https://")
    expect_error(lambda: c.add_cards(SRC, "world", "t", "short"), "characters")
    expect_error(lambda: c.add_cards(SRC, "world", "t", "\n".join(["Statement long enough"] * 6)), "1 to 5")
    expect_error(lambda: c.add_cards(SRC, "world", "t", "   "), "1 to 5")


def test_validators_compare_verdicts_not_notes():
    mod, c = fresh()
    mg.world.set_llm(lambda p: {"verdicts": ["TRUE", "FALSE"], "note": "leader wording"}, "leader")
    mg.world.set_llm(lambda p: {"verdicts": ["TRUE", "FALSE"], "note": "totally different words"}, "validator")
    as_(ALICE)
    c.add_cards(SRC, "world", "Geo", "First statement here\nSecond statement here")
    assert json.loads(c.get_card(1))["verdict"] == "FALSE"


def test_lying_leader_is_rejected():
    mod, c = fresh()
    llm_batch(["TRUE", "TRUE"], "leader")
    llm_batch(["TRUE", "FALSE"], "validator")
    as_(ALICE)
    expect_error(lambda: c.add_cards(SRC, "world", "Geo", "First statement here\nSecond statement here"), "validators agreed")
    assert c.get_card_count() == 0


def test_wrong_verdict_count_is_llm_error():
    mod, c = fresh()
    llm_batch(["TRUE"])
    as_(ALICE)
    expect_error(lambda: c.add_cards(SRC, "world", "Geo", "First statement here\nSecond statement here"), "validators agreed")


def test_unreachable_source_agrees_on_transient_error():
    mod, c = fresh()
    llm_batch(["TRUE"])
    as_(ALICE)
    bad = "https://example.com/missing"
    expect_error(lambda: c.add_cards(bad, "world", "Geo", "A statement long enough"), "TRANSIENT")


def test_score_run_matches_spec():
    mod, c = fresh()
    v = {i: ("TRUE" if i % 2 == 0 else "FALSE") for i in range(20)}
    answers = [(i, "T" if i % 2 == 0 else "F") for i in range(12)]
    r = mod.score_run(answers, v.get)
    # streak 1-5 x1, 6-10 x2, 11-12 x3
    assert r["score"] == 5 * 100 + 5 * 200 + 2 * 300, r
    assert r["best_streak"] == 12 and r["correct"] == 12
    r2 = mod.score_run([(0, "F"), (2, "T"), (2, "T"), (99, "T")], v.get)
    assert r2 == {"score": 100, "correct": 1, "answered": 2, "best_streak": 1}


def test_submit_run_scores_on_chain_and_builds_leaderboard():
    mod, c = fresh()
    seed(c, ("TRUE", "FALSE", "TRUE"))
    day = c.get_day()
    as_(BOB)
    res = json.loads(c.submit_run(day, "world", "0:T,1:F,2:F"))
    assert res["score"] == 200 and res["correct"] == 2
    as_(ALICE)
    json.loads(c.submit_run(day, "world", "0:T,1:F,2:T"))
    board = json.loads(c.get_leaderboard(day, "world"))
    assert [e["score"] for e in board] == [300, 200]
    assert board[0]["who"] == ALICE.as_hex.lower()
    as_(BOB)
    c.submit_run(day, "world", "0:F")   # lower score keeps the best
    assert json.loads(c.get_player(day, BOB.as_hex))["best"]["world"] == 200


def test_submit_run_validation():
    mod, c = fresh()
    seed(c)
    day = c.get_day()
    as_(BOB)
    expect_error(lambda: c.submit_run(day - 5, "world", "0:T"), "today or yesterday")
    expect_error(lambda: c.submit_run(day, "world", "0:yes"), "12:T")
    expect_error(lambda: c.submit_run(day, "world", ""), "1 to 200")


def test_sections_have_separate_decks_and_boards():
    mod, c = fresh()
    seed(c, ("TRUE", "FALSE"), "world")            # ids 0,1
    seed(c, ("TRUE", "TRUE"), "genlayer")          # ids 2,3
    day = c.get_day()
    as_(BOB)
    r = json.loads(c.submit_run(day, "genlayer", "0:T,1:F,2:T,3:T"))
    assert r["score"] == 200 and r["answered"] == 2, r    # world cards ignored
    assert json.loads(c.get_leaderboard(day, "world")) == []
    assert json.loads(c.get_leaderboard(day, "genlayer"))[0]["score"] == 200
    assert json.loads(c.get_card(2))["section"] == "genlayer"
    best = json.loads(c.get_player(day, BOB.as_hex))["best"]
    assert best == {"world": 0, "genlayer": 200}
    expect_error(lambda: c.submit_run(day, "sports", "0:T"), "world or genlayer")
    llm_batch(["TRUE"]); as_(ALICE)
    expect_error(lambda: c.add_cards(SRC, "misc", "t", "A statement long enough"), "world or genlayer")


def test_retired_cards_do_not_score():
    mod, c = fresh()
    seed(c, ("UNCLEAR", "TRUE"))
    as_(BOB)
    res = json.loads(c.submit_run(c.get_day(), "world", "0:T,1:T"))
    assert res["score"] == 100 and res["answered"] == 1


def test_appeal_needs_deposit_and_active_card():
    mod, c = fresh()
    seed(c, ("TRUE", "UNCLEAR"))
    as_(BOB, 0)
    expect_error(lambda: c.appeal(0, "page says otherwise"), "0.1 GEN")
    as_(BOB, GEN // 10)
    expect_error(lambda: c.appeal(1, "x"), "active cards")


def test_appeal_won_flips_card_and_refunds():
    mod, c = fresh()
    seed(c, ("TRUE",))
    llm_single("FALSE")
    as_(BOB, GEN // 10)
    res = json.loads(c.appeal(0, "the page says the opposite"))
    assert res["won"] and res["verdict"] == "FALSE"
    card = json.loads(c.get_card(0))
    assert card["appeals"] == 1 and card["flips"] == 1
    assert mg.world.transfers == [(BOB.as_hex.lower(), GEN // 10)]
    assert json.loads(c.get_player(0, BOB.as_hex))["appeals_won"] == 1


def test_appeal_lost_keeps_card_and_deposit():
    mod, c = fresh()
    seed(c, ("TRUE",))
    llm_single("TRUE")
    as_(BOB, GEN // 10)
    res = json.loads(c.appeal(0, "i disagree"))
    assert not res["won"] and res["verdict"] == "TRUE"
    assert mg.world.transfers == []


def test_appeal_unclear_retires_card():
    mod, c = fresh()
    seed(c, ("FALSE",))
    llm_single("UNCLEAR")
    as_(BOB, GEN // 10)
    res = json.loads(c.appeal(0, "page does not say"))
    assert res["won"] and res["verdict"] == "RETIRED"


def test_appeal_validator_disagreement_fails():
    mod, c = fresh()
    seed(c, ("TRUE",))
    llm_single("FALSE", "leader")
    llm_single("TRUE", "validator")
    as_(BOB, GEN // 10)
    expect_error(lambda: c.appeal(0, "x"), "validators agreed")
    assert json.loads(c.get_card(0))["verdict"] == "TRUE"


def test_web_render_settings():
    mod, c = fresh()
    seed(c, ("TRUE",))
    renders = [x for x in mg.world.nondet_calls if x[0] == "render"]
    assert renders and all(x[3] == "text" and x[4] == "2s" for x in renders)


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
