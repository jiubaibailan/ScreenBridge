# v0.1 validation

Verified locally on Windows 11 build 26200 with Python 3.12.14; record updated 2026-11-18 (UTC+8).

- 31 regression cases cover incremental OCR retention, duplicate text, image crop bounds and description invalidation, immutable image annotations after window changes, unchanged-frame reuse, negative coordinates, stale-window/pixel rejection, concurrent file readers, exclusive process locks, failure recovery, stopped-state publication and termination/reuse of accessibility helper processes.
- The real desktop integration test launches a controlled Tk window. It validates both OCR text and application-provided UI Automation elements, reads a PNG over MCP, saves an image description, clicks a real button, types Chinese and emoji, uses Ctrl+A, scrolls and rejects an old snapshot. It also starts/reuses/stops the skill daemon and reads, crops and clicks through the standalone CLI.
- The skill package check extracts the ZIP outside the source checkout, prepares its bundled locked environment, runs its version command and initializes OCR successfully.
- Static checks and source/wheel builds are required by `GATES.md`. GitHub Actions runs the non-interactive checks; the real desktop test requires a visible interactive Windows session and is run locally.

## Controlled-window timing sample

Command: `uv run python scripts/benchmark_fixture.py`. Ten sequential observations, with a 100 ms target interval, produced:

| Measure | Observed value |
|---|---:|
| Distinct processed snapshots | 2 |
| First complete collection, including initialization and file publication | 1472.15 ms |
| Median of the subsequent nine collection calls | 32.07 ms |
| Maximum of the subsequent nine collection calls | 1183.62 ms |
| OCR processing for the two changed snapshots | 1028.41 ms / 1128.31 ms |

This is a single small-window sample on this machine. The later calls include a second changed frame; their median mainly reflects reuse of already recognized content. These numbers exclude AI inference, image interpretation, CLI startup and network latency. They do not establish a speedup over Computer Use or any other complete agent workflow.

## Coverage boundaries

No broad application compatibility matrix, end-to-end AI latency comparison, real multi-monitor test, or full display-scaling sweep has been completed. Negative-origin and bounds behavior is covered with synthetic state; physical coordinate input is checked against a real local fixture. Arbitrary image segmentation and semantic descriptions remain dependent on candidate quality and the consuming AI's vision ability. Rapid animations and inaccessible/protected windows retain the limitations documented in the README.
