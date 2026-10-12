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
2. **Appeal** (`appeal`). A player who thinks a verdict is wrong deposits 0.1 GEN, gives a reason and can attach **new evidence** (a second page). Validators re-read the card's source and the evidence independently. The outcome is fixed code: if both sources agree, that verdict stands; if one is unclear, the other decides; if they **conflict**, the card is not settled and is retired. If the outcome differs from the current verdict, the card is updated and the deposit is **refunded** with `emit_transfer`; otherwise the deposit stays with the contract. This mirrors GenLayer's own appeal process inside a game.

Everything else is deterministic Python. **Scores are recomputed on-chain** from the player's answers (`submit_run`), so the leaderboard cannot be typed in by hand.

## Consensus design

| Step | Evidence | Pattern | Compared by validators | Not compared |
| --- | --- | --- | --- | --- |
| Fact-check | `gl.nondet.web.render(source, mode="text")` | `gl.vm.run_nondet_unsafe(leader_fn, validator_fn)` | the list of verdicts | the note |
| Appeal | same source page, re-read | `gl.vm.run_nondet_unsafe(leader_fn, validator_fn)` | the single verdict | the note |

- Each validator re-runs the task itself and compares only decision fields. Free text never decides consensus.
- For an appeal, validators compare the **outcome** (`appeal_outcome` of the per-source verdicts), so a leader cannot fake a winning appeal by misreporting the evidence (`test_adversarial_leader_cannot_fake_a_winning_appeal`). Evidence that fails to load counts as UNCLEAR.
- For a batch, a validator accepts the leader's list when every verdict matches its own, or when the leader said UNCLEAR (that card is only retired, so it is safe). A TRUE/FALSE conflict, or a decisive leader verdict the validator could not confirm, rejects the batch.
- Errors are classified as `[EXPECTED]`, `[TRANSIENT]` and `[LLM_ERROR]`. Validators agree on identical expected errors and on transient errors (for example, the page failed to load). LLM errors always disagree, which forces leader rotation.
- Prompt-injection hardening: page text is wrapped in markers and the model is told to treat it as data only. Verdicts are normalised against a fixed set.
- Storage uses flat `TreeMap`s only. There are no dataclass storage objects.

## Game rules (identical in the app and the contract)

- Correct answer: 100 points × multiplier. The multiplier rises by 1 every 5 correct answers in a row, up to ×5.
- A wrong answer or a missed card resets the streak and adds a crack to the floor. Six cracks end the run.
- Every 5-in-a-row clears one crack.
- **The contract builds the daily deck** (`get_deck`): the section's cards created before 00:00 UTC that day (newest 60), shuffled with mulberry32 seeded by the day. The game fetches this list, so everyone plays the same cards in the same order. Cards added today join tomorrow's deck. Python and JavaScript produce bit-identical shuffles (tested against values from Node).
- **The contract replays the run** (`submit_run`): the player sends one pick per deck position (`T`, `F`, `-` for a missed card, `x` for a retired card that was skipped). The contract applies the same rules as the game, in deck order, and stops at the sixth crack; picks after a cave-in are ignored.
- Three scored attempts per player, per section, per day. The best one counts.
- Practice mode uses a built-in deck (34 World cards, 25 GenLayer cards) that is not on-chain, so it works offline and isn't scored on-chain.

## Contract API — `contracts/truthfall.py`

| Method | Type | Notes |
| --- | --- | --- |
| `add_cards(source_url, section, topic, statements)` | write | section is `world` or `genlayer`; 1–5 statements, one per line, 12–160 characters each; returns ids and verdicts |
| `appeal(card_id, reason, evidence_url)` | write, payable | 0.1 GEN deposit; `evidence_url` optional (empty string for none); refunded if the outcome changes |
| `submit_run(day, section, picks)` | write | picks like `TF-TxF`, one per deck position; for today or yesterday; 3 attempts per day per section; keeps your best score |
| `get_deck(day, section)` | view | JSON list of card ids in play order |
| `get_cards(offset, limit)` | view | JSON, includes verdict, validator note, appeals, flips and `created_at` |
| `get_card(card_id)` | view | JSON |
| `get_leaderboard(day, section)` | view | JSON top 10 for that section |
| `get_player(day, user)` | view | best score and attempts left per section that day, appeals won |
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
- the deck: contract-side shuffle matching the game's JavaScript, cards joining the next UTC day
- the game rules replayed on-chain: multiplier, cracks, crack clearing, cave-in, misses, skipped retired cards
- adversarial runs: right answers sent in card-id order instead of deck order, too many or malformed picks, answers after a cave-in, more than 3 attempts, old days
- adversarial appeals: a leader faking a winning appeal, evidence equal to the source, non-https evidence, unreadable evidence
- legitimate runs and the leaderboard
- separate decks, scores and leaderboards for the two sections
- owner-only curation (move between sections, retire) and owner handover
- appeals that win, lose or retire a card, with and without new evidence, including the deposit refund

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

## What the leaderboard guarantees (and what it does not)

**Guaranteed by the contract**
- A score cannot be typed in. It is computed by the contract from the player's picks.
- Picks are scored against the contract's own daily deck, in its order, with the game's rules, including the cave-in at six cracks.
- The deck is fixed at 00:00 UTC, so cards added or appealed during the day cannot be used to reshape it for one player (an appeal can retire a card, which is then skipped for everyone).
- Three attempts per player, per section, per day; the best one counts. One entry per wallet on each board.

**Not guaranteed**
- **Verdicts are public on-chain**, as all contract state is. A player who reads them can answer correctly without knowing the facts. The board therefore measures accuracy and streaks for honest play; it is a learning game with no prizes, and it should not be used for anything of value.
- **Reaction time is not verified.** The contract cannot see how fast someone swiped.
- Hiding verdicts until a run ends would need a commit-reveal deck. That is a planned milestone.

## Known limits

- Validators read the first 8,000 characters of a page, so statements should come from the top of the source.
- Heavy websites can make the leader run out of time (`LeaderTimeout`). For the GenLayer section, cite the plain-text docs files through jsDelivr (`https://cdn.jsdelivr.net/gh/genlayerlabs/genlayer-docs@main/pages/...mdx`); raw.githubusercontent.com also works but was throttled during seeding. They carry the same text as docs.genlayer.com without menus or scripts.

## Live proof (Bradbury)

Contract page with every transaction: https://explorer-bradbury.genlayer.com/address/0xEBA590341D0C04BE889bb1647e9c5B867Ea9c80f

The README lists transaction links for: a card batch accepted, an evidence appeal that retired a card and refunded 0.1 GEN, and a legitimate daily run scored by the contract. All three were run on 11 and 12 October 2026.

The appeal demo uses `demo/outdated-staking-note.md`, a deliberately outdated test fixture in this repo. A card checked against it says "The minimum delegation is 10 GEN" is TRUE; an appeal that adds the official staking docs (42 GEN) as evidence makes the sources conflict, which retires the card and refunds the deposit.

| Proof | Transaction |
| --- | --- |
| Card batch accepted (5 GenLayer cards, 3 TRUE / 2 FALSE) | [0xf4e83e72…988f32](https://explorer-bradbury.genlayer.com/tx/0xf4e83e72a36eebaa8e78b890fe059fef503ff186fabf08939337821b4a988f32) |
| Demo card accepted (from the outdated fixture, TRUE) | [0x7ec788a0…9e26c2](https://explorer-bradbury.genlayer.com/tx/0x7ec788a03ed6158b9bdaeaf8a017fe30cc35665cce88d8eb219ae5e8069e26c2) |
| Evidence appeal on the demo card: sources conflict (10 GEN vs 42 GEN), card retired, 0.10 GEN refunded by internal transfer | [0x4e566f89…f210a5](https://explorer-bradbury.genlayer.com/tx/0x4e566f893cb14f74f478049cf8aae415d1504e0cb4f2103c641e496862f210a5) |
| Daily GenLayer run (6-card deck) submitted, replayed and scored by the contract: 400 points | [0x55ffe3ca…710341](https://explorer-bradbury.genlayer.com/tx/0x55ffe3ca7394e5b1a1e28d3997b08825732bcfa92de6b83d6f46551e7f710341) |

## Deployment record

| Item | Value |
| --- | --- |
| Network | Testnet Bradbury (chain 4221) |
| Contract | `0xEBA590341D0C04BE889bb1647e9c5B867Ea9c80f` |
| Deploy tx | `0x7f93af146057d7d29cb2bf8a0e4a280dbae0747b0d8f7e09b77fffc538fab8a5` |
| Deploy result | ACCEPTED, validators AGREE, FINISHED_WITH_RETURN (9 Oct 2026) |
| Earlier test contracts | `0xf45d7E8a654364751687c12b456D8aFAe30b7064` (first batch hit LeaderTimeout on the heavy docs site, which led to plain-text sources and the tolerant validator); `0xAa9312bA7aCdEbB38FD44248A606eC33Ee1C6d9A` (worked, but had no way to fix a batch saved to the wrong section, which led to `curate`); `0xF0236229896895F1564f34C9d5a4BF978cE2791b` (owner was the CLI deployer with no handover, which led to `set_owner`); `0x357771AC011E9428119eC61f063Ea2e71ff9Eb86` (first reviewed version; steward feedback led to the contract-built deck, replayed runs, attempt limits and evidence appeals) |
| Live game | https://kingkanzi0.github.io/truthfall/frontend/ |
