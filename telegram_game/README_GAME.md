# Telegram UI Notes — v17 Mission Workflow Update

## Main improvements

- added `/missionsui [page]` for a paged mission browser
- selected mission replies now open with a faster action keyboard
- assign UI now supports pagination for translator picks and role pages
- role picker now supports candidate pagination
- website mission detail now generates a Telegram-ready workflow block

## Recommended flow

```text
/start
/menu
/missionsui
/pick BN-260320-01
/accept
/assignui
/team
/submit
/gearui
/rosterui
```

## Button layout highlights

- Mission UI for paged mission selection
- Assign UI with translator and role pages
- Roster UI for staff browsing
- Gear UI for equipment and training loop
