# Gates: screen-bridge v0.1

OWNS: src/**, tests/**, scripts/**, skills/**, docs/**, .github/**, README.md, pyproject.toml, uv.lock, .python-version, .gitignore, GATES.md

Scope: self-contained installable skill with Windows screen state, OCR, image annotation, guarded input, CLI and optional MCP.

- [x] G1: state persistence, incremental extraction, image annotations and input guards pass regression tests
  CHECK: uv run --locked pytest -q
  EXPECT: /^\d+ passed(?:, \d+ warnings?)? in /m
  EVIDENCE: automatic-evidence=v1; definition-sha256=d6d25207ad3486135892ee2ff4b6d733f191b6050f2f311e14923eeffecd5d6c; exit=0; EXPECT=matched; output-sha256=0db88f73425196e4d0b9cbb89fe6eeec291799501fd7342376528e97f022ea02; output-bytes=101; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\ruanjing123\OneDrive\文档\RJ_projections\ai自动读取屏幕中间层; path=463280a33470/41 entries

- [x] G2: a real Windows fixture is read through OCR, MCP and the skill CLI, with input verified and stale actions rejected
  CHECK: uv run --locked python scripts/smoke_desktop.py
  EXPECT: DESKTOP_MCP_SMOKE_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=9aabfa9d7abeeb742ee6a4ab29876b0cf561a7faed79cca72a4c1e4644f1305e; exit=0; EXPECT=matched; output-sha256=bce1fc17f44b8b80bbfdb0900fa149168ef3359cc452ee4cebbfb8821c0d2036; output-bytes=165; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\ruanjing123\OneDrive\文档\RJ_projections\ai自动读取屏幕中间层; path=463280a33470/41 entries

- [x] G3: all Python sources satisfy static checks
  CHECK: uv run --locked ruff check .
  EXPECT: All checks passed!
  EVIDENCE: automatic-evidence=v1; definition-sha256=b9c43d6cee81aaae09a27bc1c77bcb5038dee5dc0034222abfa49f19617e1e39; exit=0; EXPECT=matched; output-sha256=82b3e6a6c090a57601d22943bd23fca9218d1031dbe5a7b754092f9a156b4f18; output-bytes=19; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\ruanjing123\OneDrive\文档\RJ_projections\ai自动读取屏幕中间层; path=463280a33470/41 entries

- [x] G4: source and wheel distributions build successfully
  CHECK: uv build
  EXPECT: Successfully built
  EVIDENCE: automatic-evidence=v1; definition-sha256=efa5b7bd848b44bff501e96fd71cf22807e5290e63be307ec2204906381ef744; exit=0; EXPECT=matched; output-sha256=44ddc6352cfa6f831cba1af02f9a18f17932fc2136408b1ba09215bb1994f53b; output-bytes=187; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\ruanjing123\OneDrive\文档\RJ_projections\ai自动读取屏幕中间层; path=463280a33470/41 entries

- [x] G5: the skill ZIP boots its bundled locked runtime outside the source checkout
  CHECK: uv run --locked python scripts/verify_skill.py
  EXPECT: SKILL_PACKAGE_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=58328684f297b38f5a17d678b25944cb1b3772bc8d4b0d8e3448438fcccd45d9; exit=0; EXPECT=matched; output-sha256=ee374a0ef9e3e5ef0e5445ca58d90c5794b50932742d70289dc12ee42d5f88b8; output-bytes=18; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\ruanjing123\OneDrive\文档\RJ_projections\ai自动读取屏幕中间层; path=463280a33470/41 entries

- [ ] G6: event queue and wait_for_event deliver ordered state changes and stop cleanly
  CHECK: uv run --locked pytest -q tests/test_events.py
  EXPECT: /^\d+ passed(?:, \d+ warnings?)? in /m

- [ ] G7: generic visual components and task adapters expose the documented contracts
  CHECK: uv run --locked pytest -q tests/test_components.py
  EXPECT: /^\d+ passed(?:, \d+ warnings?)? in /m

- [ ] G8: MCP exposes real-time task tools and preserves existing tools
  CHECK: uv run --locked pytest -q tests/test_mcp_tools.py
  EXPECT: /^\d+ passed(?:, \d+ warnings?)? in /m

- [ ] G9: all sources and documentation pass final validation
  CHECK: uv run --locked ruff check .
  EXPECT: /^All checks passed!/m
