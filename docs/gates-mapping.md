# Gate Taxonomy: Deck vs. Framework

The client deck (`psq-framework-deck.html`) and this framework both talk about
"gates G1–G4", but they mean two different (compatible) things. This page is
the mapping so nobody has to guess which taxonomy a conversation is using.

## Deck gates = milestone phases

The deck describes **one pass through a project milestone**, from the team's
point of view:

| Deck gate | Phase | Green means |
|---|---|---|
| G1 | Build (UI + unit tests) | UI built from the contract, unit tests pass |
| G2 | Validate (Playwright + shots) | E2E suite passes, screenshots match design |
| G3 | Backend wired | Same suite re-run against real backend, still green |
| G4 | Client UAT | Client approves; minor tweaks looped |

Four green deck gates = one milestone = invoice.

## Framework gates = per-issue lifecycle

The framework's `gates.yml` describes **what every individual issue/PR goes
through**, regardless of which milestone phase it belongs to:

| Framework gate | Name | Green means |
|---|---|---|
| G0 | scope | Contract present, assumptions recorded |
| G1 | build | install / lint / typecheck / build pass |
| G2 | behavior | tests + screenshot suite pass |
| G3 | proof | proof report collected (logs, screenshots, gate results) |
| G4 | review_ready | report validates, decision surface prepared |

## How they compose

Every issue inside **any** deck phase runs the full framework G0–G4 loop.
A deck gate goes green when all of its issues have cleared framework G4 and
the phase-level check (e.g. the Playwright suite for deck-G2) is green in CI.

```text
Deck:      [ G1 Build ]───[ G2 Validate ]───[ G3 Backend ]───[ G4 UAT ] → milestone
                │                │                 │
Framework:  issue loop       issue loop        issue loop
            G0→G1→G2→G3→G4   G0→G1→G2→G3→G4    G0→G1→G2→G3→G4
```

If this dual naming causes friction in practice, rename the deck gates to
P1–P4 (phases) — the framework gate IDs are wired into `harness.json`,
`gate-status.json`, and CI, so they are the ones to keep stable.
