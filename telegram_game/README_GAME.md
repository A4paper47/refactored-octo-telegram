# Telegram UI Notes — v20 Live Simulator Actions + Preset Buttons

## New in v20

- mission flow now includes direct preset apply buttons on selected mission cards
  - `recommended`
  - `lang`
  - `workload`
  - `trait`
- `/accept` now keeps you inside the mission flow instead of dropping back to the generic menu
- dashboard simulator now includes:
  - simulator action deck
  - preset action deck
  - operator summary card
  - copy-ready recommended preset actions

## Suggested flow

```text
/start
/missionsui
/accept
[tap a preset button]
/team
/submit
```

## Manual control flow

```text
/assignui
/assignpreset recommended
/assignpreset lang
/assignpreset workload
/assignpreset trait
```
