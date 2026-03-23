# Telegram UI Notes — v19 Simulator + Assign Presets

## New in v19

- `/assignpreset <recommended|lang|workload|trait>` auto-fills translator and role picks using a smart preset
- `/assignui` now shows and preserves an active preset across:
  - translator filters
  - role filters
  - role picker pages
- role picker and assign UI now surface the active preset in text for faster operator testing
- website dashboard now includes:
  - mission simulator panel
  - recommended preset playbook
  - copy-ready preset workflow

## Suggested flow

```text
/start
/missionsui
/accept
/assignpreset recommended
/team
/submit
```

## Manual control flow

```text
/assignui
/assignpreset lang
/assignpreset workload
/assignpreset trait
```
