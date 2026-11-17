# Architecture and state contract

## Pipeline

```text
Foreground HWND + physical window bounds
  → visible screenshot (MSS / Win32)
  → content fingerprint
  → unchanged: reuse recognition, update observation heartbeat
  → changed: UI Automation + local OCR on changed rectangle
  → merge text + candidate image regions
  → atomic screen.json + versioned PNG
  → skill CLI (local named pipe) / optional MCP stdio
  → live window and pixel check → SendInput → observe again
```

UI Automation exposes application-provided labels, control roles and bounds. OCR is still run on changes to supply text in canvases, images and unsupported controls. `auto` retains OCR outside the changed rectangle and expands that rectangle to cover any intersecting previous text line. A window identity or position change forces a full pass. No AI model is invoked by the collector.

The collector has its own thread; state publication is protected by a lock, and input actions are serialized. An OS-backed output lock permits one collector per directory and releases after a crash. CLI clients share the same collector through an authenticated Windows named pipe. The connection record remains local. MCP owns a separate collector and output directory.

## Screen state

The JSON format is versioned with `schema_version: 1`. Coordinates are physical virtual-desktop pixels and boxes use `[x, y, width, height]`. Negative x/y are valid. OCR line coordinates and UI Automation control coordinates are normalized to this same coordinate system. `window.bbox` is the native window rectangle; `window.capture_bbox` is the actual visible screenshot rectangle, clipped to the desktop and excluding decorative edges.

```json
{
  "schema_version": 1,
  "status": "ready",
  "snapshot_id": "opaque-version-id",
  "captured_at": "2026-11-17T12:00:00.000+00:00",
  "observed_at": "2026-11-17T12:00:00.350+00:00",
  "processed_at": "2026-11-17T12:00:00.250+00:00",
  "coordinate_system": "physical_virtual_desktop_pixels",
  "bbox_format": "x,y,width,height",
  "window": {
    "hwnd": 123,
    "pid": 456,
    "title": "Example window",
    "bbox": [90, 90, 820, 650],
    "capture_bbox": [98, 98, 804, 634]
  },
  "elements": [
    {"id": "el_example", "text": "Save", "role": "button", "bbox": [400, 300, 80, 30], "source": "uia", "password": false}
  ],
  "regions": [
    {"id": "region_example", "bbox": [120, 200, 200, 160], "kind": "visual_candidate", "source": "heuristic", "name": "", "content_hash": "opaque-content-hash", "description": null, "description_source": null}
  ],
  "fingerprint": "opaque-screen-content-hash",
  "image_path": "C:/local-state/snapshots/opaque-version-id.png",
  "ocr_mode": "auto",
  "dirty_bbox": [98, 98, 804, 634],
  "warnings": [],
  "timings_ms": {"capture": 4.0, "uia": 50.0, "ocr": 180.0, "processing": 250.0}
}
```

The values above illustrate the format; they are not benchmark results. OCR elements additionally include a confidence estimate. Supported element roles depend on the application. An empty element list or nonempty warnings may indicate degraded recognition rather than an empty window.

`captured_at` belongs to the saved image; `processed_at` is when extraction finished. `observed_at` refreshes when later captures have identical pixels, without changing `snapshot_id`. Static file heartbeats write at most once a second. A collection failure publishes `status: unavailable` and a message, and tools refuse to operate using the old state. A clean stop publishes `status: stopped`. Neither status should be interpreted as a live actionable screen.

## Image descriptions

Image controls are recorded from UI Automation when available. Additional candidates come from local edge analysis with known text masked out. These candidates may combine objects or pick up UI decoration. A requested crop can always be created explicitly inside `capture_bbox`.

Each crop is hashed from its dimensions and RGB pixels. `get_region_image` / `image` returns the crop and region metadata. The consuming AI inspects it and submits `describe_region` / `describe`; that description is recorded with `description_source: ai_annotation`, never as verified ground truth. The service remembers the last 256 issued crops, so it can accept an interpretation of one of those immutable crops even after the foreground window changes. A description applies to the current screen only if its content hash matches. The persistent cache reuses descriptions only for identical crop content, including after restart.

## Input checks

Every action requires the current `snapshot_id`. The service then captures the live foreground window and compares window identity, bounds and pixel fingerprint. A mismatch returns `STALE_SNAPSHOT` before sending input. Coordinate and element targets must lie within the captured rectangle. Input uses physical screen coordinates and Windows SendInput; Unicode text uses UTF-16 events rather than the clipboard.

Returned `status: sent` means only that Windows accepted input events. The caller must read the next state or application result to verify success. Physical events are not transactional; permissions, target behavior and a last-moment user/window change can still prevent or redirect their effect.

## Resource behavior and limits

Default polling is 350 ms. Full OCR initialization and changed-region recognition can exceed that interval; the service processes work sequentially rather than accumulating an unbounded frame queue. UI Automation traversal stops after 1,000 controls, depth 20 or its elapsed budget. It runs in a reusable helper process so a blocked application provider cannot indefinitely stall collection: the first request allows 1.5 seconds including process startup, subsequent requests allow 400 ms, and an unresponsive helper is terminated before falling back to OCR. Helper shutdown may add a short process-reaping delay.

The store retains five versioned full screenshots. Requested region files and image descriptions are kept locally until removed by the owner. Only PNG/JSON state is retained; no video is recorded. File publication retries short Windows sharing violations. The raw JSON reader also retries those transient violations.

The first release intentionally favors explicit stale-action failures over accepting a different frame. Fast animation can therefore limit usability. Future improvements can evaluate target-local validity checks and richer visual tracking against measured misclick rates rather than weakening the current check implicitly.
