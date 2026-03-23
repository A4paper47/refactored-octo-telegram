# Telegram UI Notes — v13 Inventory + Gear Loop

## Main improvements

- added `/inventory`
- added `/gearshop`
- added `/buygear <item_key>`
- added `/equip <staff> <item_key>`
- added `/unequip <staff>`
- mission cards now show **modifiers**
- submit result can now give loot items into inventory
- home panel now shows inventory count
- inline menu now includes **Inventory** and **Gear Shop**

## Recommended flow

```text
/start
/menu
/mission
/accept
/autocast
/inventory
/gearshop
/buygear focus_notes
/equip Alya focus_notes
/team
/submit
/nextday
```

## Button layout

- Home / Mission / Missions
- Board / Assign UI / Team
- Accept / Auto Cast / Submit
- Studio / Market / Clients
- Roster / Bench / Goals
- Inventory / Gear Shop / Rep
- Rest All / Log / Help
- Sync DB / DB Mission / Next Day
