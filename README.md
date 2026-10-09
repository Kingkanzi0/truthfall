# Truthfall

A fast true-or-false arcade game whose cards are fact-checked by GenLayer validators.

Statements fall down the screen. Swipe each one into **TRUE** or **FALSE** before it hits the floor. Every card in the daily deck was checked by GenLayer validators reading a real web page, and any player can **appeal** a card they think is wrong.

Plays on mobile (swipe or tap) and desktop (arrow keys, A/D, or buttons).

## Two sections

| Section | What the cards are about | Typical source |
| --- | --- | --- |
| 🌍 **World** | capitals, space, science, football, crypto, culture | Wikipedia articles |
| **GenLayer** | Optimistic Democracy, the Equivalence Principle, appeals, staking, GenVM | docs.genlayer.com |

Each section has its own daily deck, its own leaderboard and its own best score per player. GenLayer cards are checked by validators against the official docs, so the game doubles as a way to learn how GenLayer works.

## Why it needs GenLayer

A normal smart contract cannot read a web page, so a quiz game has to trust whoever wrote the answers. In Truthfall, the contract makes only two judgments under GenLayer consensus:

1. **Fact-check** (`add_cards`). Anyone can submit up to five statements with one source page, for one section. Validators each open the page with `gl.nondet.web.render`, decide TRUE, FALSE or UNCLEAR for every statement, and must agree on the full list of verdicts. Cards the page does not settle are retired and never reach the deck.
2. **Appeal** (`appeal`). A player who thinks a verdict is wrong deposits 0.1 GEN and gives a reason. Validators re-read the source independently. If they agree the verdict was wrong, the card is fixed (or retired) and the deposit is refunded with `emit_transfer`. This mirrors GenLayer's own appeal process inside a game.

Everything else is deterministic Python. **Scores are recomputed on-chain** from the player's answers (`submit_run`), so the leaderboard cannot be typed in by hand.

## Consensus design

| Step | Evidence | Pattern | Compared by validators | Not compared |
| --- | --- | --- | --- | --- |
| Fact-check | `gl.nondet.web.render(source, mode="text")` | `gl.vm.run_nondet_unsafe(leader_fn, validator_fn)` | the list of verdicts | the note |
| Appeal | same source page, re-read | `gl.vm.run_nondet_unsafe(leader_fn, validator_fn)` | the single verdict | the note |

- Each validator re-runs the task itself and compares only decision fields. Free text never decides consensus.
- For a batch, a validator accepts the leader's list when every verdict matches its own, or when the leader said UNCLEAR (that card is only retired, so it is safe). A TRUE/FALSE conflict, or a decisive leader verdict the validator could not confirm, rejects the batch.
- Errors are classified as `[EXPECTED]`, `[TRANSIENT]` and `[LLM_ERROR]`. Validators agree on identical expected errors and on transient errors (for example, the page failed to load). LLM errors always disagree, which forces leader rotation.
- Prompt-injection hardening: page text is wrapped in markers and the model is told to treat it as data only. Verdicts are normalised against a fixed set.
- Storage uses flat `TreeMap`s only. There are no dataclass storage objects.

## Game rules (identical in the app and the contract)

- Correct answer: 100 points × multiplier. The multiplier rises by 1 every 5 correct answers in a row, up to ×5.
- A wrong answer or a missed card resets the streak and adds a crack to the floor. Six cracks end the run.
- Every 5-in-a-row clears one crack.
- Each section's daily deck uses the same shuffled order for every player, seeded by the day number.
- Practice mode uses a built-in deck (34 World cards, 25 GenLayer cards) that is not on-chain, so it works offline and isn't scored on-chain.

## Contract API — `contracts/truthfall.py`

| Method | Type | Notes |
| --- | --- | --- |
| `add_cards(source_url, section, topic, statements)` | write | section is `world` or `genlayer`; 1–5 statements, one per line, 12–160 characters each; returns ids and verdicts |
| `appeal(card_id, reason)` | write, payable | 0.1 GEN deposit, refunded if the appeal wins |
| `submit_run(day, section, answers)` | write | answers like `12:T,7:F`; only cards in that section count; for today or yesterday; keeps your best score |
| `get_cards(offset, limit)` | view | JSON, includes verdict, validator note, appeals and flips |
| `get_card(card_id)` | view | JSON |
| `get_leaderboard(day, section)` | view | JSON top 10 for that section |
| `get_player(day, user)` | view | best score per section that day and appeals won |
| `curate(card_ids, action)` | write, owner only | `card_ids` like `5,6,7`; `action` is `world` or `genlayer` (move) or `retire` |
| `set_owner(new_owner)` | write, owner only | hands curation to another wallet |
| `get_owner()` | view | current curator address (the deployer until handed over) |
| `get_day()` / `get_card_count()` | view | int |

## Repository layout

```
contracts/truthfall.py      the Intelligent Contract
frontend/index.html         the game (single static page, genlayer-js from a CDN)
tests/test_truthfall.py     offline tests
tests/mock_genlayer.py      minimal SDK stand-in used by the tests
```

## Testing

```
python3 tests/test_truthfall.py
```

The offline tests cover:
- fact-check batches, including UNCLEAR cards being retired, a leader UNCLEAR being accepted as a retire, and input validation
- a lying leader being rejected
- notes being ignored by validators while verdicts are compared
- transient load errors
- deterministic scoring and the leaderboard
- separate decks, scores and leaderboards for the two sections
- owner-only curation (move between sections, retire) and owner handover
- appeals that win, lose or retire a card, including the deposit refund

The mock is not GenVM, so behaviour must also be confirmed live on Bradbury.

## Deploy (Bradbury)

```
genlayer network set                      # pick Testnet Bradbury
genlayer deploy --contract contracts/truthfall.py
```

Then put the address in `DEFAULT_CONTRACT` at the top of the script in `frontend/index.html`, and enable GitHub Pages (Settings → Pages → branch `main`, folder `/ (root)`). The game is served at `https://<user>.github.io/truthfall/frontend/`.

Seed the decks from the app with **+ Cards**. Pick the section, then give one source page and 2–5 statements from its opening paragraphs (a Wikipedia article for World, a docs.genlayer.com page for GenLayer).

## Curation

Anyone can add cards, so the deployer can tidy the deck with `curate`: move cards that went into the wrong section, or retire spam and outdated cards. Curation cannot change a verdict (only validators do that, through `add_cards` and `appeal`), and every change is a public transaction. In the app, the **Curate** button appears only for the owner's wallet. The CLI account that deploys becomes the first owner; `set_owner` hands curation to a browser wallet:

```
genlayer write <contract> set_owner --args 0xYourBrowserWallet
```

## Known limits

- Card verdicts are public on-chain, so a determined player could look them up before playing. The daily order and on-chain scoring keep the leaderboard comparable. A commit-reveal deck is a possible future milestone.
- Validators read the first 8,000 characters of a page, so statements should come from the top of the source.
- Heavy websites can make the leader run out of time (`LeaderTimeout`). For the GenLayer section, cite the plain-text docs files through jsDelivr (`https://cdn.jsdelivr.net/gh/genlayerlabs/genlayer-docs@main/pages/...mdx`); raw.githubusercontent.com also works but was throttled during seeding. They carry the same text as docs.genlayer.com without menus or scripts.

## Deployment record

| Item | Value |
| --- | --- |
| Network | Testnet Bradbury (chain 4221) |
| Contract | `0x357771AC011E9428119eC61f063Ea2e71ff9Eb86` |
| Deploy tx | `0x83e5ef2ecc6aa9631b959765ab3ecbe4e323466336f6c2c480b8e93fad7f1e8b` |
| Deploy result | ACCEPTED, validators AGREE, FINISHED_WITH_RETURN (9 Oct 2026) |
| Earlier test contracts | `0xf45d7E8a654364751687c12b456D8aFAe30b7064` (first batch hit LeaderTimeout on the heavy docs site, which led to plain-text sources and the tolerant validator); `0xAa9312bA7aCdEbB38FD44248A606eC33Ee1C6d9A` (worked, but had no way to fix a batch saved to the wrong section, which led to `curate`); `0xF0236229896895F1564f34C9d5a4BF978cE2791b` (owner was the CLI deployer with no handover, which led to `set_owner`) |
| Live game | https://kingkanzi0.github.io/truthfall/frontend/ |
