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
    mg.world.set_llm(lambda p: {"verdicts": [verdict], "note": "re-checked"}, role)


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


def test_runner_header_is_first_line_and_only_comment_before_code():
    lines = CONTRACT.read_text().splitlines()
    assert lines[0].startswith('# { "Depends": "py-genlayer:')
    json.loads(lines[0][1:])
    assert lines[1] == ""                                        # nothing directly after the header
    top = lines[: lines.index("import json")]
    assert [l for l in top if l.startswith("#")] == [lines[0]]   # the description is a docstring


def next_day():
    mg.world.now += 86400


def deck(c, sec="world"):
    return json.loads(c.get_deck(c.get_day(), sec))


def picks_for(c, deck_ids, wrong=()):
    out = []
    for i, cid in enumerate(deck_ids):
        v = json.loads(c.get_card(cid))["verdict"]
        if v not in ("TRUE", "FALSE"):
            out.append("x")
            continue
        right = "T" if v == "TRUE" else "F"
        out.append(("F" if right == "T" else "T") if i in wrong else right)
    return "".join(out)


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


def test_leader_unclear_is_accepted_as_retire():
    mod, c = fresh()
    llm_batch(["TRUE", "UNCLEAR"], "leader")
    llm_batch(["TRUE", "FALSE"], "validator")
    as_(ALICE)
    out = json.loads(c.add_cards(SRC, "genlayer", "Basics", "First statement here\nSecond statement here"))
    assert out["verdicts"] == ["TRUE", "UNCLEAR"]
    assert json.loads(c.get_card(1))["verdict"] == "RETIRED"


def test_validator_unclear_rejects_decisive_leader():
    mod, c = fresh()
    llm_batch(["TRUE", "TRUE"], "leader")
    llm_batch(["TRUE", "UNCLEAR"], "validator")
    as_(ALICE)
    expect_error(lambda: c.add_cards(SRC, "world", "Geo", "First statement here\nSecond statement here"), "validators agreed")


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


def test_rng_and_shuffle_match_game_javascript():
    mod, c = fresh()
    r = mod.rng(20736 * 7919 + 13)
    assert [r(), r(), r()] == [0.07030845386907458, 0.23167398641817272, 0.914388056145981]   # values from node
    assert mod.shuffle(range(10), mod.rng(20736 * 7919 + 71)) == [2, 9, 6, 1, 3, 0, 8, 4, 7, 5]


def test_score_picks_matches_game_rules():
    mod, c = fresh()
    v = ["TRUE" if i % 2 == 0 else "FALSE" for i in range(20)]
    right = "".join("T" if x == "TRUE" else "F" for x in v)
    wrong = "".join("F" if x == "T" else "T" for x in right)
    r = mod.score_picks(v, right[:12])
    assert r["score"] == 5 * 100 + 5 * 200 + 2 * 300 and r["best_streak"] == 12, r
    r2 = mod.score_picks(v, wrong[:6] + right[6:])              # 6th crack ends the run
    assert r2["caved"] and r2["score"] == 0 and r2["answered"] == 6
    r3 = mod.score_picks(v, wrong[:5] + right[5:10] + wrong[10])  # 5 in a row clears a crack
    assert not r3["caved"] and r3["correct"] == 5
    assert mod.score_picks(["TRUE", None, "FALSE"], "TTF")["answered"] == 2   # retired card skipped
    assert mod.score_picks(["TRUE", "FALSE"], "--")["correct"] == 0           # misses count as wrong


def test_cards_join_the_deck_next_utc_day():
    mod, c = fresh()
    seed(c, ("TRUE", "FALSE", "TRUE"))
    assert deck(c) == []
    as_(BOB)
    expect_error(lambda: c.submit_run(c.get_day(), "world", "T"), "no cards yet")
    next_day()
    assert sorted(deck(c)) == [0, 1, 2]


def test_legitimate_run_scores_and_ranks():
    mod, c = fresh()
    seed(c, ("TRUE", "FALSE", "TRUE"))
    next_day()
    d, day = deck(c), c.get_day()
    as_(BOB)
    res = json.loads(c.submit_run(day, "world", picks_for(c, d, wrong={2})))
    assert res["score"] == 200 and res["correct"] == 2 and res["attempts_left"] == 2
    as_(ALICE)
    c.submit_run(day, "world", picks_for(c, d))
    board = json.loads(c.get_leaderboard(day, "world"))
    assert [e["score"] for e in board] == [300, 200] and board[0]["who"] == ALICE.as_hex.lower()
    as_(BOB)
    c.submit_run(day, "world", "F")                              # a worse run keeps the best
    assert json.loads(c.get_player(day, BOB.as_hex))["best"]["world"] == 200


def test_adversarial_right_answers_in_card_id_order_do_not_score_perfect():
    mod, c = fresh()
    seed(c, ("TRUE", "TRUE", "FALSE", "FALSE", "TRUE"))
    next_day()
    d = deck(c)
    assert d != sorted(d)                                        # the deck is shuffled
    by_id = "".join("T" if json.loads(c.get_card(i))["verdict"] == "TRUE" else "F" for i in sorted(d))
    as_(BOB)
    assert json.loads(c.submit_run(c.get_day(), "world", by_id))["correct"] < 5


def test_adversarial_bad_or_extra_picks_rejected():
    mod, c = fresh()
    seed(c, ("TRUE", "FALSE"))
    next_day()
    day = c.get_day()
    as_(BOB)
    expect_error(lambda: c.submit_run(day, "world", "TFT"), "more picks")
    expect_error(lambda: c.submit_run(day, "world", "0:T,1:F"), "picks must be")
    expect_error(lambda: c.submit_run(day, "world", ""), "picks must be")
    expect_error(lambda: c.submit_run(day - 5, "world", "T"), "today or yesterday")


def test_adversarial_attempts_are_capped_per_section():
    mod, c = fresh()
    seed(c, ("TRUE", "FALSE"), "world")
    seed(c, ("TRUE",), "genlayer")
    next_day()
    day = c.get_day()
    as_(BOB)
    for _ in range(3):
        c.submit_run(day, "world", "TT")
    expect_error(lambda: c.submit_run(day, "world", "TF"), "all 3 attempts")
    left = json.loads(c.get_player(day, BOB.as_hex))["attempts_left"]
    assert left == {"world": 0, "genlayer": 3}
    c.submit_run(day, "genlayer", "T")                           # other section has its own attempts


def test_adversarial_picks_after_cave_in_do_not_count():
    mod, c = fresh()
    seed(c, ("TRUE",) * 5)
    seed(c, ("TRUE",) * 5)
    next_day()
    d = deck(c)
    as_(BOB)
    res = json.loads(c.submit_run(c.get_day(), "world", "F" * 6 + "T" * (len(d) - 6)))
    assert res["caved"] and res["score"] == 0 and res["answered"] == 6


def test_sections_have_separate_decks_and_boards():
    mod, c = fresh()
    seed(c, ("TRUE", "FALSE"), "world")            # ids 0,1
    seed(c, ("TRUE", "TRUE"), "genlayer")          # ids 2,3
    next_day()
    day = c.get_day()
    assert sorted(deck(c, "world")) == [0, 1] and sorted(deck(c, "genlayer")) == [2, 3]
    as_(BOB)
    r = json.loads(c.submit_run(day, "genlayer", "TT"))
    assert r["score"] == 200 and r["answered"] == 2
    assert json.loads(c.get_leaderboard(day, "world")) == []
    assert json.loads(c.get_leaderboard(day, "genlayer"))[0]["score"] == 200
    assert json.loads(c.get_card(2))["section"] == "genlayer"
    assert json.loads(c.get_player(day, BOB.as_hex))["best"] == {"world": 0, "genlayer": 200}
    expect_error(lambda: c.submit_run(day, "sports", "T"), "world or genlayer")
    llm_batch(["TRUE"])
    as_(ALICE)
    expect_error(lambda: c.add_cards(SRC, "misc", "t", "A statement long enough"), "world or genlayer")


def test_owner_can_move_and_retire_cards():
    mod, c = fresh()                                   # deployed by ALICE (mock default sender)
    seed(c, ("TRUE", "FALSE", "TRUE"), "world")        # ids 0,1,2
    as_(BOB)
    expect_error(lambda: c.curate("0", "genlayer"), "only the owner")
    as_(ALICE)
    c.curate("0, 1", "genlayer")
    assert json.loads(c.get_card(0))["section"] == "genlayer"
    c.curate("2", "retire")
    assert json.loads(c.get_card(2))["verdict"] == "RETIRED"
    expect_error(lambda: c.curate("9", "retire"), "does not exist")
    expect_error(lambda: c.curate("0", "sports"), "world or genlayer")
    assert c.get_owner() == ALICE.as_hex.lower()


def test_owner_can_hand_over_curation():
    mod, c = fresh()
    seed(c, ("TRUE",), "world")
    as_(BOB)
    expect_error(lambda: c.set_owner(BOB), "only the owner")
    as_(ALICE)
    c.set_owner(BOB)
    assert c.get_owner() == BOB.as_hex.lower()
    expect_error(lambda: c.curate("0,", "retire"), "only the owner")   # ALICE no longer owner
    as_(BOB)
    c.curate("0,", "genlayer")
    assert json.loads(c.get_card(0))["section"] == "genlayer"


def test_retired_cards_do_not_score():
    mod, c = fresh()
    seed(c, ("UNCLEAR", "TRUE"))
    next_day()
    as_(BOB)
    res = json.loads(c.submit_run(c.get_day(), "world", "TT"))
    assert res["score"] == 100 and res["answered"] == 1


def test_appeal_needs_deposit_and_active_card():
    mod, c = fresh()
    seed(c, ("TRUE", "UNCLEAR"))
    as_(BOB, 0)
    expect_error(lambda: c.appeal(0, "page says otherwise", ""), "0.1 GEN")
    as_(BOB, GEN // 10)
    expect_error(lambda: c.appeal(1, "x", ""), "active cards")


def test_appeal_won_flips_card_and_refunds():
    mod, c = fresh()
    seed(c, ("TRUE",))
    llm_single("FALSE")
    as_(BOB, GEN // 10)
    res = json.loads(c.appeal(0, "the page says the opposite", ""))
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
    res = json.loads(c.appeal(0, "i disagree", ""))
    assert not res["won"] and res["verdict"] == "TRUE"
    assert mg.world.transfers == []


def test_appeal_unclear_retires_card():
    mod, c = fresh()
    seed(c, ("FALSE",))
    llm_single("UNCLEAR")
    as_(BOB, GEN // 10)
    res = json.loads(c.appeal(0, "page does not say", ""))
    assert res["won"] and res["verdict"] == "RETIRED"


EV = "https://cdn.jsdelivr.net/gh/genlayerlabs/genlayer-docs@main/pages/staking.mdx"


def test_appeal_outcome_rules():
    mod, c = fresh()
    o = mod.appeal_outcome
    assert o("TRUE", None) == "TRUE" and o("TRUE", "UNCLEAR") == "TRUE"
    assert o("TRUE", "TRUE") == "TRUE" and o("UNCLEAR", "FALSE") == "FALSE"
    assert o("TRUE", "FALSE") == "UNCLEAR"                       # conflicting sources: not settled


def test_appeal_with_conflicting_evidence_retires_and_refunds():
    mod, c = fresh()
    seed(c, ("TRUE",))                                           # card 0 is TRUE from an outdated source
    mg.world.set_web(EV, "Official docs: the minimum delegation is 42 GEN.")
    mg.world.set_llm(lambda p: {"verdicts": ["TRUE", "FALSE"], "note": "new source contradicts"})
    as_(BOB, GEN // 10)
    res = json.loads(c.appeal(0, "the official docs say otherwise", EV))
    assert res["won"] and res["verdict"] == "RETIRED" and res["refunded"] == GEN // 10
    assert mg.world.transfers == [(BOB.as_hex.lower(), GEN // 10)]


def test_appeal_with_agreeing_evidence_keeps_card_and_deposit():
    mod, c = fresh()
    seed(c, ("TRUE",))
    mg.world.set_web(EV, "agrees")
    mg.world.set_llm(lambda p: {"verdicts": ["TRUE", "TRUE"], "note": "both agree"})
    as_(BOB, GEN // 10)
    res = json.loads(c.appeal(0, "x", EV))
    assert not res["won"] and res["refunded"] == 0 and mg.world.transfers == []


def test_adversarial_leader_cannot_fake_a_winning_appeal():
    mod, c = fresh()
    seed(c, ("TRUE",))
    mg.world.set_web(EV, "agrees")
    mg.world.set_llm(lambda p: {"verdicts": ["TRUE", "FALSE"], "note": "fake"}, "leader")
    mg.world.set_llm(lambda p: {"verdicts": ["TRUE", "TRUE"], "note": "real"}, "validator")
    as_(BOB, GEN // 10)
    expect_error(lambda: c.appeal(0, "x", EV), "validators agreed")
    assert json.loads(c.get_card(0))["verdict"] == "TRUE" and mg.world.transfers == []


def test_appeal_evidence_rules():
    mod, c = fresh()
    seed(c, ("TRUE",))
    as_(BOB, GEN // 10)
    expect_error(lambda: c.appeal(0, "x", SRC), "different page")
    expect_error(lambda: c.appeal(0, "x", "http://insecure.example"), "https://")


def test_unreadable_evidence_counts_as_unclear():
    mod, c = fresh()
    seed(c, ("TRUE",))
    mg.world.set_llm(lambda p: {"verdicts": ["TRUE"] * p.count("<<<SOURCE"), "note": "only the original loaded"})
    as_(BOB, GEN // 10)
    res = json.loads(c.appeal(0, "x", "https://missing.example/page"))
    assert not res["won"] and res["verdict"] == "TRUE"


def test_appeal_validator_disagreement_fails():
    mod, c = fresh()
    seed(c, ("TRUE",))
    llm_single("FALSE", "leader")
    llm_single("TRUE", "validator")
    as_(BOB, GEN // 10)
    expect_error(lambda: c.appeal(0, "x", ""), "validators agreed")
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
