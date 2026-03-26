# Telegram UI Notes — v21 Compact Mission Cards + Roster Quick Actions

## New in v21

- selected mission replies are now more compact and more readable
  - client / tier / language / priority on one line
  - reward / XP / rep / deadline on one line
  - cast progress + translator + preset hint together
- dashboard mission detail now includes **roster-backed quick actions**
  - recommended translator command
  - recommended per-role assign commands
  - top alternatives from the synced roster

## Suggested flow

```text
/start
/missionsui
/accept
[tap preset button]
/team
/submit
```

## Website operator flow

```text
/dashboard
select a mission row
copy a roster-backed translator or role command
paste into Telegram
```
