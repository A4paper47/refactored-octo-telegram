# Telegram UI Notes — v18 Modal + Assign Filter Update

## New in v18

- `/assignui` now supports richer filtering through inline buttons
  - translator filters: `all`, `fresh`, `calm`
  - role list filters: `all`, `male`, `female`
- role picker now supports energy filters
  - `all`, `fresh`, `tired`
- website dashboard now includes:
  - mission detail modal
  - quick assign templates
  - copy-ready translator and role assignment actions

## Suggested flow

```text
/start
/missionsui
/accept
/assignui
/team
/submit
```

## Gear / staff flow

```text
/rosterui
/staff Alya
/gearui
/gearshop
```
