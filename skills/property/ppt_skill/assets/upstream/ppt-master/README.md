# Bundled PPT Master Runtime

Source: https://github.com/hugohe3/ppt-master

Vendored version: 6.7.2 (2026-10-19)

The runtime required by `skills/property/ppt_skill` now lives directly in the
skill: the complete upstream `scripts/` tree is merged into
`skills/property/ppt_skill/scripts/`, templates live in
`skills/property/ppt_skill/templates/`, and the upstream license/sponsor files
stay in the skill root for `attribution_guard.py`.

To upgrade, replace the upstream files from the same version, keep the
first-party `build_property_deck.py`, `property_metrics.py`, and `svg_render.py`
intact, then run:

```powershell
python skills/property/ppt_skill/scripts/attribution_guard.py
python scripts/ops/generate_property_deck.py --project "鸣X园"
```
