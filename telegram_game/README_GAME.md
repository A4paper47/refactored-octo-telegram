# Telegram UI Notes — v12 Gameplay Loop

## Main improvements

- added `/goals` for achievement and milestone tracking
- added `/staff <name>` for detailed staff card
- added `/train <name> [balanced|skill|speed]`
- added `/rest <name>` and `/restall`
- home panel now shows unlocked goals count
- inline menu now includes **Goals** and **Rest All**

## Recommended flow

```text
/start
/menu
/mission
/accept
/assignui
/team
/goals
/train Ray speed
/rest Sara
/submit
/nextday
```

## Button layout

- Home / Mission / Missions
- Board / Assign UI / Team
- Accept / Auto Cast / Submit
- Studio / Market / Clients
- Roster / Bench / Goals
- Rep / Rest All / Log
- Sync DB / DB Mission / Help
- Next Day
