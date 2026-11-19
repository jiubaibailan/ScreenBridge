# Runtime commands

## Setup and lifecycle

`scripts/run.py` uses `uv run --locked --no-dev` to run the bundled Python 3.12 package. It also works from the source repository before packaging. If uv is missing, install it using its official instructions: https://docs.astral.sh/uv/getting-started/installation/ .

```powershell
python scripts/run.py doctor --check-ocr
python scripts/run.py start
python scripts/run.py status
python scripts/run.py stop
```

The default state directory is `%LOCALAPPDATA%/screen-bridge/runtime`. It contains `screen.json`, retained screenshots, image-description cache, and local service data. It is separate from the repository. The background service stays local and uses an authenticated Windows named pipe, a local inter-process connection that is not a network port. Never publish its `session.json` or runtime directory.

Every command accepts `--output PATH` for a separate instance; use the same absolute path throughout that instance's lifetime. `start --interval 0.35 --ocr auto` controls polling and OCR. The interval is a target; OCR processing can take longer. `--ocr always` processes the complete window on changes; `auto` restricts OCR to the changed bounding rectangle. Unchanged pixels reuse results in either mode. `--ocr off` uses only application-provided information, and `--no-uia` uses only screenshot-based recognition.

`watch` runs the collector in the foreground and `watch --once` writes one snapshot. These commands and optional `mcp` each own their output directory; stop an existing service before reusing that directory. `benchmark --samples 10` measures local collection time only; stop the running service or choose another output directory first.

## Observations

```powershell
python scripts/run.py screen
python scripts/run.py screen --after SNAPSHOT_ID --timeout 1500
python scripts/run.py find "Save" --exact
python scripts/run.py read
python scripts/run.py image --snapshot SNAPSHOT_ID --region REGION_ID
python scripts/run.py image --snapshot SNAPSHOT_ID --bbox 100 200 400 300 --save local.png
python scripts/run.py describe --snapshot SNAPSHOT_ID --region REGION_ID --description-file description.txt
```

`captured_at` records when the screenshot was taken, `processed_at` when recognition finished, and `observed_at` the latest observation of unchanged pixels. The file heartbeat updates at most once a second on static screens. The state includes processing durations and recognition warnings, so do not assume all text was recognized simply because `status` is `ready`.

## Input

```powershell
python scripts/run.py click --snapshot SNAPSHOT_ID --element ELEMENT_ID
python scripts/run.py click --snapshot SNAPSHOT_ID --x 640 --y 400 --button right
python scripts/run.py type --snapshot SNAPSHOT_ID --text-file text.txt
python scripts/run.py key --snapshot SNAPSHOT_ID Shift+Tab
python scripts/run.py scroll --snapshot SNAPSHOT_ID down --x 640 --y 400 --amount 3
```

Coordinates are physical virtual-desktop pixels, including negative monitor origins. The captured rectangle excludes decorative one-pixel borders and normalizes rounded window corners to avoid tracking animated background pixels. Click either an element or a coordinate, not both. Wheel amount is 1–20 notches. Key names support letters, digits, arrows, Enter, Tab, Escape, Backspace, Delete, Home, End, PageUp, PageDown and F1–F12, with Ctrl/Alt/Shift/Win modifiers.

`STALE_SNAPSHOT`, `OUT_OF_BOUNDS`, `ELEMENT_NOT_FOUND` and `NOT_RUNNING` are explicit failures, returned with a nonzero command exit code. No input is sent for a stale snapshot or out-of-bounds target. Pixel checks cannot eliminate the small race between the final observation and Windows accepting the input.

## Optional MCP

The basic tools and real-time task tools are exposed through `mcp` over standard input/output. A client can start the bundled environment's Python with `-m screen_bridge mcp --output ABSOLUTE_PATH`. This starts its own collector; it does not attach to an existing skill service. The skill CLI requires no MCP configuration.

Real-time tools are `subscribe_task`, `wait_for_event`, `get_current_state`, `act`, `verify_action`, and `stop_task`. The recommended sequence is subscribe, wait, read state, act with the returned `snapshot_id`, and verify the next state. `auto_execute` is opt-in.
