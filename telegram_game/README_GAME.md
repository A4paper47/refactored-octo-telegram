# Telegram UI Notes — v14 Action + Gear UI

## Main improvements

- added `/gearui`
- `/inventory` and `/gearshop` now return gear-focused inline panels
- added inline callbacks for:
  - buy gear
  - open staff card
  - train balanced / skill / speed
  - rest staff
  - equip picker
  - equip / unequip
- `/staff <name>` now opens a richer action panel
- main menu now has **Gear UI** button

## Recommended flow

```text
/start
/menu
/gearui
/staff Alya
/train Alya skill
/rest Alya
/gearshop
/buygear focus_notes
/equip Alya focus_notes
/mission
/accept
/autocast
/team
/submit
```

## Button layout

- Home / Mission / Missions
- Board / Assign UI / Team
- Accept / Auto Cast / Submit
- Studio / Market / Clients
- Roster / Bench / Goals
- Inventory / Gear UI / Gear Shop
- Rep / Log / Help
- Rest All / Sync DB / DB Mission
- Next Day
