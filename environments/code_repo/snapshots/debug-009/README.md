# boardstore

A small JSON-file backed store. Each entity *kind* owns one collection file,
one id sequence, one bundle section and one card renderer.

## Registering a new kind

There is no single source of truth. A kind is only fully supported when every
consumer below knows about it; the consumers never cross-check each other, so a
half registered kind keeps working for the paths that were mirrored and
silently loses records for the paths that were not.

| # | place | consumed by |
|---|-------|-------------|
| 1 | `store/registry.py: KINDS` | `JsonStore._doc()` - accept the kind |
| 2 | `store/registry.py: COLLECTION_ORDER` | store init, `counts()`, `sequences()` |
| 3 | `store/registry.py: LABELS` | board group header, `boardstore list` |
| 4 | `store/db.py: COLLECTION_FILES` | the collection file on disk |
| 5 | `store/portable.py: BUNDLE_SECTIONS` | what `export_bundle()` writes out |
| 6 | `store/portable.py: IMPORT_SECTIONS` | what `import_bundle()` merges back |
| 7 | `store/state.py: COUNT_KINDS` | `daemon_state()` heartbeat payload |
| 8 | `store/state.py: GROUP_KINDS` | `board_state()` columns |
| 9 | `store/render.py: RENDERERS` | `render_card()` |
| 10 | `store/cli.py: KIND_CHOICES`, `USAGE` | accepted `--kind`, help text |

A kind registered at 1-4 only is readable from the local store but disappears
on export, so a `export -> import -> read` round trip loses it without any
error being raised anywhere.

## Layout

```
store/
  registry.py  kind lists
  db.py        JsonStore: create / put / get / update / list / count / sequence
  portable.py  export_bundle / write_bundle / read_bundle / import_bundle
  state.py     daemon_state / board_state
  render.py    render_card
  cli.py       argparse front end
tests/         visible suite, run with `python -m pytest tests`
```
