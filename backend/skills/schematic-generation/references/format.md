# Artifact contract

The input contains `components[]` and `connections[]`. Every component has a stable `id`, `name`, `type`, `library_type` (`public` or `private`) and `pins[]`; every pin has `id`, `name`, and `direction` (`input`, `output`, `bidirectional`, `power`). A connection contains `source` and `target` component/pin references and a non-empty `net`.

Generated files:

- `01_signal_interfaces.json`: component blocks grouped by input/output/bidirectional/power pins and their nets.
- `02_cbb_classification.json`: public/private classification for every component.
- `components/<id>.json`: one generated component artifact.
- `schematic.json`: packaged project with components and wires.
- `events.json`: component task start/finish lifecycle records.
- `manifest.json`: file inventory, counts and generation status.

The final JSON must keep all component, pin, connection and net identities from the input. Layout coordinates may vary and are not part of topology correctness.
