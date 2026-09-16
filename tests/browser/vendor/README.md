# Vendored: axe-core

- **Version:** 4.13.0
- **License:** MPL-2.0. This file is unmodified, and its license header is
  kept at the top of `axe.min.js`.
- **Source:** https://registry.npmjs.org/axe-core/-/axe-core-4.13.0.tgz
  (`package/axe.min.js`)
- **SHA-256:** `c24f097bd2f451d4f933e8bc7d8d539f8672a2ebcb5cc9f9f3eec8ca9470a0c1`

It is vendored rather than installed so CI never needs npm. It is pinned by
checksum so the outside WCAG opinion is the exact one that was reviewed:
`tests/browser/conftest.py` refuses any other file.

## Updating

1. Download the new tarball and extract `package/axe.min.js` over this file.
2. Change `AXE_VERSION` and `AXE_SHA256` in `tests/browser/conftest.py`, and
   the two lines above, in the same commit.
3. Run `pytest -m browser`. A new axe version can find new violations. That is
   a finding to fix, not a reason to pin the old version.
