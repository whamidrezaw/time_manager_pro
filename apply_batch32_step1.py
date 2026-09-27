#!/usr/bin/env python3
"""Batch 32, step 1: the 1.0.0 release (CHANGELOG, one version, release guide).

Run from the repository root, on the feature branch:

    python apply_batch32_step1.py --commit    apply, check, commit and push, in one go
    python apply_batch32_step1.py             apply only

--commit stops at the first problem and then leaves git untouched: on main,
when a file is not the version this step was built on, when ruff fails, or
when the known-failures gate fails. It removes only itself, then commits with
the message below and pushes. A second run reports everything as applied.
Expected afterwards: pytest -> 0 failed, 548 passed.
"""
from __future__ import annotations

import base64
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
SELF = Path(__file__).name
SUBJECT = 'release: 1.0.0'
BODY = "The first release: Batches 19 to 32. CHANGELOG.md, from the commit history in Keep a Changelog's sections, with what waits under known debt. One version, 1.0.0, in app/__init__.py, reported by FastAPI and named in the start-up line; a test keeps it equal to the changelog's top entry. docs/RELEASE.md holds the checklist a version passes before its tag, all ticked for 1.0.0 but the TalkBack pass by hand, and how to tag, check after the deploy and roll back. pip-audit finds no known vulnerability today."
REQUIREMENTS_CHANGED = False

# (path, sha256 before or None if new, sha256 after or None if removed, content)
FILES = [
    ('CHANGELOG.md', None, 'bdbd097f67fb956f1ead586b70c28b4020b97baed0bddaba16674966dce45180',
     'IyBDaGFuZ2Vsb2cKCkV2ZXJ5IHJlbGVhc2Ugb2YgVGltZU1hbmFnZXIgUHJvLCBuZXdlc3QgZmlyc3QuIFRoZSBmb3JtYXQgZm9s'
     'bG93cwpbS2VlcCBhIENoYW5nZWxvZ10oaHR0cHM6Ly9rZWVwYWNoYW5nZWxvZy5jb20vZW4vMS4xLjAvKSBhbmQgdmVyc2lvbnMg'
     'Zm9sbG93CltTZW1hbnRpYyBWZXJzaW9uaW5nXShodHRwczovL3NlbXZlci5vcmcvKS4gVGhlIHJlYXNvbnMgYmVoaW5kIHRoZSBj'
     'aGFuZ2VzCmFyZSBpbiBgZG9jcy9hZHIvYDsgZWFybGllciBoaXN0b3J5IGlzIGluIGBnaXQgbG9nYC4KCiMjIFsxLjAuMF0gLSAy'
     'MDI2LTA5LTI3CgpUaGUgZmlyc3QgcmVsZWFzZTogQmF0Y2hlcyAxOSB0byAzMiwgZnJvbSB0aGUgcmV2aWV3IHRoYXQgZm91bmQg'
     'dHdvCmRhdGEtbG9zcyBidWdzIHRvIGEgbWVhc3VyZWQsIGF1ZGl0ZWQgYW5kIGRvY3VtZW50ZWQgc2VydmljZS4KCiMjIyBBZGRl'
     'ZAotIEFjY2Vzc2liaWxpdHkgdG8gV0NBRyAyLjEgQUE6IGV2ZXJ5IGRpYWxvZyBvbiBvbmUgbW9kYWwgc3RhY2ssIGRhdGUgZmll'
     'bGRzCiAgdGhhdCBvcGVuIGZyb20gdGhlIGtleWJvYXJkLCBhIG5hbWUsIHJvbGUgYW5kIHN0YXRlIGZvciBldmVyeSBjb250cm9s'
     'LCBhCiAgbGlzdCB3aXRoIHN0cnVjdHVyZSwgZXJyb3JzIHRoYXQgc3RheSBvbiB0aGVpciBmaWVsZCwgcmVhZGFibGUgY29sb3Vy'
     'cyBpbgogIGV2ZXJ5IHRoZW1lLCBhbmQgYSBjb3VudGRvd24gcGFnZSBhbmQgc2hhcmUgY2FyZHMgdGhhdCBuYW1lIHRoZW1zZWx2'
     'ZXMKICAoQURSIDAwMDYsIEFEUiAwMDA4KS4KLSBBZG1pbiBjb250cm9sIG92ZXIgZXZlbnQgbGltaXRzIGZyb20gdGhlIGJvdDog'
     'YC9saW1pdHNgLCBgL2xpbWl0YCwKICBgL3NldGJhc2VgLCBgL3NldGJvbnVzYCwgYC9zZXRzdGVwYCwgYW5kIGAvbG9naWRgIHRv'
     'IGZpbmQgYSB1c2VyIGluIHRoZSBsb2dzCiAgKEFEUiAwMDA5LCBBRFIgMDAxOCkuCi0gYC9kZWxldGVteWRhdGFgOiBlcmFzdXJl'
     'IG9uIHJlcXVlc3QsIGNvbmZpcm1lZCBieSBhIGJ1dHRvbiBvbmx5IHRoZSBzZW5kZXIKICBjYW4gcHJlc3MgKEFEUiAwMDE3KS4K'
     'LSBPYnNlcnZhYmlsaXR5OiBKU09OIGxvZ3Mgd2l0aCBhIHJlcXVlc3QgaWQsIG9uZSBSRUQgbGluZSBwZXIgcmVxdWVzdCB3aXRo'
     'CiAgaXRzIHRpbWUgdG8gcmVzcG9uc2UgYW5kIGRhdGFiYXNlIGNvc3QsIHRoZSBwaGFzZXMgb2YgZWFjaCByZW1pbmRlciBydW4s'
     'CiAgYW5kIGFsZXJ0cyBieSBUZWxlZ3JhbSB3aXRoIGEgaGVhbHRoY2hlY2tzLmlvIHBpbmcgKEFEUiAwMDExLCBBRFIgMDAxNCk7'
     'CiAgYGRvY3MvUlVOQk9PSy5tZGAgZm9yIGV2ZXJ5IGFsZXJ0LgotIGBTRUNVUklUWS5tZGAsIGBDT05TVFJBSU5UUy5tZGAsIGEg'
     'RGVmaW5pdGlvbiBvZiBEb25lLCBhbmQgbmluZXRlZW4KICBhcmNoaXRlY3R1cmUgZGVjaXNpb24gcmVjb3Jkcy4KCiMjIyBDaGFu'
     'Z2VkCi0gVGhlIGJhc2UgbGltaXQgaXMgMjUsIGFuZCB0aGUgbGltaXQgbnVkZ2UgYXBwZWFycyBvbmx5IGF0IHRoZSBsaW1pdC4K'
     'LSBTYXZpbmcgYW4gZXZlbnQgbm8gbG9uZ2VyIHdhaXRzIGZvciBUZWxlZ3JhbTogdGhlIGNvbmZpcm1hdGlvbiBmb2xsb3dzIHRo'
     'ZQogIHJlc3BvbnNlLgotIEEgY29tcGFjdCB0b3AgY2FyZCBsZWF2ZXMgcm9vbSBmb3IgdHdvIGV2ZW50cyBhYm92ZSB0aGUgdGFi'
     'IGJhci4KLSBQcm9kdWN0aW9uIHN0YXJ0cyB0aHJvdWdoIHRoZSBgdXZpY29ybi13b3JrZXJgIHBhY2thZ2UgKEFEUiAwMDEzKS4K'
     'LSBPbmUgYm90IGZvciB0aGUgd2hvbGUgYXBwLCBhbmQgdGhlIGhlYWx0aCBjb3VudHMgc2VudCBhdCBvbmNlIChBRFIgMDAxOSku'
     'Ci0gTG9ncyBuYW1lIG5vIG9uZTogaWRzIGJlY29tZSBrZXllZCBwc2V1ZG9ueW1zLCBhbmQgdGl0bGVzIGFuZCBkYXRlcyBzdGF5'
     'CiAgb3V0IChBRFIgMDAxOCkuCgojIyMgRml4ZWQKLSBUd28gZGF0YS1sb3NzIGJ1Z3M6IGV2ZW50cyBkZWxldGVkIGJ5IHRoZSBU'
     'VEwgYWZ0ZXIgYW4gZWRpdCBhbmQgYWZ0ZXIgYQogIHdvcmtlciBvdXRhZ2UgKEFEUiAwMDAzKS4KLSBEdXBsaWNhdGUgcmVtaW5k'
     'ZXJzIChBRFIgMDAwNCksIGFuIHVucmVjb3ZlcmFibGUgZmFpbGVkIHN0YXRlLCBhbmQKICB0aW1lem9uZS1uYWl2ZSBkYXRlcyAo'
     'QURSIDAwMDEpLgotIENvbmZpcm1hdGlvbnMgdGhhdCBjb3VsZCBmaXJlIHR3aWNlLgotIEEgdXNlciByZXN0cmljdGVkIGluIGEg'
     'Z3JvdXAgY291bGQgbGluayBpdCBhZnRlciBsZWF2aW5nIGl0LgotIHV2aWNvcm4ncyBhY2Nlc3MgbGluZXMgcHV0IHRva2VucyBh'
     'bmQgYWRkcmVzc2VzIGluIHRoZSBsb2dzLgotIFRlc3RzIHRoYXQgZmFpbGVkIHVuZGVyIGxvYWQgbm93IHdhaXQgZm9yIGNhdXNl'
     'cywgbm90IGNsb2NrcyAoQURSIDAwMTApLgoKIyMjIFNlY3VyaXR5Ci0gVW5hdXRoZW50aWNhdGVkIHJlcXVlc3RzIGFyZSBib3Vu'
     'ZGVkOiBib2RpZXMgYXQgbW9zdCA2NCBLQiwgYGluaXREYXRhYCBhdAogIG1vc3QgODE5MiBjaGFyYWN0ZXJzLCBmYWlsZWQgYXV0'
     'aGVudGljYXRpb24gdGhyb3R0bGVkIHBlciBhZGRyZXNzIGFuZCBpbgogIHRvdGFsIChBRFIgMDAxNSkuCi0gVGhlIGNsaWVudCdz'
     'IGFkZHJlc3MgaXMgdGFrZW4gcGFzdCBDbG91ZGZsYXJlIGFuZCBSZW5kZXIncyBwcm94aWVzLCBzbyBpdAogIGNhbm5vdCBiZSBm'
     'b3JnZWQgKEFEUiAwMDE2KS4KLSBSYXRlIGxpbWl0cyBvbiBwdWJsaWMgcm91dGVzLCBjb25zdGFudC10aW1lIHNlY3JldCBjb21w'
     'YXJpc29ucywgYQogIGBQZXJtaXNzaW9ucy1Qb2xpY3lgLCBhIGxvZ2dlZCBDU1Agb3ZlcnJpZGUsIGFuZCBubyBwYXJ0IG9mIGEg'
     'aGFzaCBpbiBsb2dzLgotIGBwaXAtYXVkaXRgIGZpbmRzIG5vIGtub3duIHZ1bG5lcmFiaWxpdHkgKDIwMjYtMDktMjcpLgoKIyMj'
     'IFJlbW92ZWQKLSBUaGUgbG9uZy1ydW5uaW5nIHJlbWluZGVyIHdvcmtlciBhbmQgaXRzIGRlcGxveW1lbnQgZmlsZXM6IHJlbWlu'
     'ZGVycyBhcmUKICBzZW50IGJ5IGEgY3JvbiBlbmRwb2ludCwgd2l0aCB0aGUgR2l0SHViIEFjdGlvbiBhcyBmYWxsYmFjayAoQURS'
     'IDAwMDUsCiAgQURSIDAwMTIpLgotIERlYWQgY29kZSwgaW5jbHVkaW5nIGBhcHAvZGVwcy5weWAuCgojIyMgS25vd24gZGVidAot'
     'IFR3byBsb25nIGZ1bmN0aW9ucyBhbmQgdGhlIDI1ODYtbGluZSBgc3RhdGljL2FwcC5qc2AsIGxlZnQgZm9yIGFmdGVyIHRoaXMK'
     'ICByZWxlYXNlIG9uIHB1cnBvc2UgKHNlZSAiS25vd24gZGVidCIgaW4gYENPTlNUUkFJTlRTLm1kYCkuCgpbMS4wLjBdOiBodHRw'
     'czovL2dpdGh1Yi5jb20vd2hhbWlkcmV6YXcvdGltZV9tYW5hZ2VyX3Byby9yZWxlYXNlcy90YWcvdjEuMC4wCg=='),
    ('README.md', '9837a514e4a0e428e3fbc28f6ca3a932f892337bb210f4e9351c1371b1a9a8d1', 'bfe89640efb8571cdf4b678fed9864b8474e8ea7a759f54e7f3c1e946189b869',
     'IyBUaW1lTWFuYWdlciBQcm8KCkEgVGVsZWdyYW0gTWluaSBBcHAgYW5kIGJvdCB0aGF0IHJlbWVtYmVycyBkYXRlcyBmb3IgeW91'
     'LCBvbiB0aGUgR3JlZ29yaWFuIGFuZAp0aGUgSmFsYWxpIGNhbGVuZGFyIGF0IHRoZSBzYW1lIHRpbWUuIEFkZCBhIGJpcnRoZGF5'
     'LCBhIG1lZXRpbmcgb3IgYW4KYXBwb2ludG1lbnQgaW4gdGhlIGFwcCwgYW5kIHRoZSByZW1pbmRlciBhcnJpdmVzIGFzIGEgVGVs'
     'ZWdyYW0gbWVzc2FnZSB3aXRoIGEKb25lLXRhcCBzbm9vemUuCgpbIVtDSV0oaHR0cHM6Ly9naXRodWIuY29tL3doYW1pZHJlemF3'
     'L3RpbWVfbWFuYWdlcl9wcm8vYWN0aW9ucy93b3JrZmxvd3MvY2kueW1sL2JhZGdlLnN2ZyldKGh0dHBzOi8vZ2l0aHViLmNvbS93'
     'aGFtaWRyZXphdy90aW1lX21hbmFnZXJfcHJvL2FjdGlvbnMvd29ya2Zsb3dzL2NpLnltbCkKIVtQeXRob25dKGh0dHBzOi8vaW1n'
     'LnNoaWVsZHMuaW8vYmFkZ2UvcHl0aG9uLTMuMTItYmx1ZSkKIVtMaWNlbnNlXShodHRwczovL2ltZy5zaGllbGRzLmlvL2JhZGdl'
     'L2xpY2Vuc2UtTUlULWdyZWVuKQoKIyMgV2h5IGl0IGV4aXN0cwoKQ2FsZW5kYXIgYXBwcyBhc3N1bWUgeW91IGxpdmUgaW4gb25l'
     'IGNhbGVuZGFyIHN5c3RlbS4gSWYgaGFsZiBvZiB0aGUgZGF0ZXMKdGhhdCBtYXR0ZXIgdG8geW91IGFyZSBKYWxhbGkgYW5kIHRo'
     'ZSBvdGhlciBoYWxmIEdyZWdvcmlhbiwgeW91IGVuZCB1cApjb252ZXJ0aW5nIHRoZW0gaW4geW91ciBoZWFkIG9yIGtlZXBpbmcg'
     'dHdvIGxpc3RzLiBUaW1lTWFuYWdlciBQcm8gc3RvcmVzIG9uZQpldmVudCBhbmQgc2hvd3MgeW91IGJvdGggZGF0ZXMsIHRoZW4g'
     'cmVhY2hlcyB5b3Ugd2hlcmUgeW91IGFscmVhZHkgYXJlOiBpbgpUZWxlZ3JhbSwgd2l0aCBubyBleHRyYSBhcHAgdG8gaW5zdGFs'
     'bCBhbmQgbm8gYWNjb3VudCB0byBjcmVhdGUuCgojIyBLZWVwaW5nIHJlbWluZGVycyBwdW5jdHVhbAoKVGhlIHJlbWluZGVyIHdv'
     'cmtlciBoYXMgdHdvIHRyaWdnZXJzLiBUaGUgR2l0SHViIEFjdGlvbnMgc2NoZWR1bGUgaXMgdGhlCmZhbGxiYWNrOyAqKml0IGlz'
     'IG5vdCBwdW5jdHVhbCoqIOKAlCBHaXRIdWIgZGVsYXlzIHNjaGVkdWxlZCB3b3JrZmxvd3MgdW5kZXIKbG9hZCBhbmQgZHJvcHMg'
     'dGhlbSBvdXRyaWdodCwgd2hpY2ggc2hvd3MgdXAgYXMgcmVtaW5kZXJzIGFycml2aW5nIGFueXdoZXJlCmZyb20gdGVuIG1pbnV0'
     'ZXMgdG8gc2V2ZXJhbCBob3VycyBsYXRlLgoKRm9yIHJlbWluZGVycyB0aGF0IGFycml2ZSBvbiB0aGUgbWludXRlLCBwb2ludCBh'
     'bnkgZXh0ZXJuYWwgY3JvbiBhdCB0aGUgYXBwCm9uY2UgYSBtaW51dGU6CgpgYGAKUE9TVCBodHRwczovLzx5b3VyIGhvc3Q+L3Rh'
     'c2tzL3J1bi1yZW1pbmRlcnMKSGVhZGVyOiBYLVRhc2tzLVNlY3JldDogPFRBU0tTX1NFQ1JFVD4KYGBgCgpCb3RoIHRyaWdnZXJz'
     'IGNhbiBydW4gdG9nZXRoZXIuIEVhY2ggZXZlbnQgaXMgY2xhaW1lZCB3aXRoIGFuIGF0b21pYyBzdGF0dXMKY2hhbmdlIGJlZm9y'
     'ZSBhbnl0aGluZyBpcyBzZW50LCBzbyB3aGljaGV2ZXIgYXJyaXZlcyBzZWNvbmQgZmluZHMgbm90aGluZyB0bwpkby4gQSBzZWNv'
     'bmQgam9iLCBvbmNlIGEgZGF5LCBzZW5kcyB5b3UgYSBzdW1tYXJ5OgoKYGBgClBPU1QgaHR0cHM6Ly88eW91ciBob3N0Pi90YXNr'
     'cy9oZWFsdGgKYGBgCgpJdCByZXBvcnRzIGhvdyBtYW55IHJlbWluZGVycyBhcmUgb3ZlcmR1ZSBhbmQgaG93IGxhdGUgdGhlIHdv'
     'cnN0IG9uZSBpcyDigJQKbGF0ZW5lc3MsIG5vdCBlcnJvcnMsIGJlY2F1c2UgYSB3b3JrZXIgdGhhdCBuZXZlciBydW5zIHJhaXNl'
     'cyBub3RoaW5nLgoKIyMgRmVhdHVyZXMKCi0gKipEdWFsIGNhbGVuZGFyLioqIEV2ZXJ5IGV2ZW50IGNhcnJpZXMgYm90aCBpdHMg'
     'R3JlZ29yaWFuIGFuZCBpdHMgSmFsYWxpIGRhdGUuCi0gKipSZW1pbmRlcnMgdGhhdCBsZWFkIHVwIHRvIHRoZSBldmVudC4qKiBT'
     'ZXQgYW4gZXZlbnQgdHdvIG1vbnRocyBvdXQgYW5kIGJlCiAgbnVkZ2VkIGRhaWx5LCB3ZWVrbHkgb3IgbW9udGhseSB1bnRpbCB0'
     'aGUgZGF5IGFycml2ZXMg4oCUIHRoZW4gdGhlIHNlcmllcyBzdG9wcy4KLSAqKk1vbnRoIHZpZXcgYW5kIGEgeWVhciBncmlkLioq'
     'IFRoZSBtb250aCBncmlkIG1hcmtzIHRvZGF5IGJ5IHNoYXBlIGFzIHdlbGwgYXMKICBjb2xvdXI7IHRoZSBzdHJpcCBhYm92ZSBp'
     'dCBzaG93cyB0aGUgd2hvbGUgeWVhciBieSBldmVudCBkZW5zaXR5LgotICoqU2hhcmVkIGV2ZW50cy4qKiBMaW5rIG9uZSBldmVu'
     'dCBhY3Jvc3Mgc2V2ZXJhbCBwZW9wbGUuIEVhY2gga2VlcHMgdGhlaXIgb3duCiAgY29weSwgc28gZWFjaCBrZWVwcyB0aGVpciBv'
     'd24gdGltZXpvbmUsIHJlbWluZGVyIHRpbWUsIG5vdGUgYW5kIGNoZWNrbGlzdCwKICB3aGlsZSB0aGUgdGl0bGUgYW5kIHRoZSBk'
     'YXRlIHN0YXkgaW4gc3RlcC4KLSAqKkdyb3VwIGFuZCBjaGFubmVsIHJlbWluZGVycy4qKiBBZGQgdGhlIGJvdCB0byBhIGdyb3Vw'
     'IG9yIGNoYW5uZWwgYW5kIGEKICByZW1pbmRlciBnb2VzIHRoZXJlIGFzIHdlbGwgYXMgdG8geW91LgotICoqU2hhcmUgY2FyZHMu'
     'KiogQSByZW5kZXJlZCBpbWFnZSBvZiBhbnkgZXZlbnQsIHdpdGggYSBwdWJsaWMgY291bnRkb3duIHBhZ2UKICBiZWhpbmQgaXQs'
     'IHNvIGEgc2hhcmVkIGxpbmsgcHJldmlld3MgYXMgdGhlIGV2ZW50IHJhdGhlciB0aGFuIGFzIGEgYm90LgotICoqQSBjaGVja2xp'
     'c3Qgb24gZXZlcnkgZXZlbnQqKiwgYmVzaWRlIHRoZSBub3RlLgotICoqSW52aXRlcyB0aGF0IHJhaXNlIHlvdXIgbGltaXQuKiog'
     'RXZlcnkgdGhyZWUgZnJpZW5kcyB3aG8gam9pbiBhbmQgc2F2ZSB0aGVpcgogIGZpcnN0IGV2ZW50IGFkZCB0d2VudHkgZXZlbnRz'
     'IHRvIHlvdXIgYWxsb3dhbmNlLgotICoqUmVtaW5kZXJzIGluIFRlbGVncmFtLioqIEEgbWVzc2FnZSBhcnJpdmVzIGF0IHRoZSBo'
     'b3VyIGFuZCBtaW51dGUgeW91IGNob3NlLAogIGluIHlvdXIgb3duIHRpbWV6b25lLCB3aXRoIGEgKlNub296ZSAxaCogYnV0dG9u'
     'IGFuZCBhIGRlZXAgbGluayBiYWNrIHRvIHRoZSBldmVudC4KLSAqKlJlY3VycmluZyBldmVudHMuKiogRGFpbHksIHdlZWtseSwg'
     'bW9udGhseSBhbmQgeWVhcmx5LCB3aXRoIGFuIG9wdGlvbmFsIGVuZAogIGRhdGUuIE1vbnRoLWVuZCBkYXRlcyByb2xsIGNvcnJl'
     'Y3RseSB0aHJvdWdoIHNob3J0IG1vbnRocywgYW5kIDI5IEZlYnJ1YXJ5CiAgaXMgaGFuZGxlZCBvbiBub24tbGVhcCB5ZWFycy4K'
     'LSAqKkNhdGVnb3JpZXMsIHBpbm5pbmcgYW5kIG5vdGVzLioqIE5pbmUgY2F0ZWdvcmllcywgcGlubmVkIGV2ZW50cyBhdCB0aGUg'
     'dG9wLAogIGFuZCBhIDIwMDAtY2hhcmFjdGVyIG5vdGUgb24gYW55IGV2ZW50LgotICoqU2VhcmNoIGFuZCBmaWx0ZXJzLioqIEZp'
     'bHRlciBieSBjYXRlZ29yeSBvciBwaW5uZWQgc3RhdHVzLCBvciBzZWFyY2ggYWNyb3NzCiAgdGl0bGVzLCBub3RlcyBhbmQgYm90'
     'aCBkYXRlIGZvcm1hdHMuCi0gKipGb2xsb3dzIHlvdXIgVGVsZWdyYW0gdGhlbWUuKiogQ29sb3VycywgZGFyayBtb2RlIGFuZCB0'
     'aGUgbmF0aXZlIGRhdGUKICBwaWNrZXJzIGFsbCB0cmFjayB0aGUgdGhlbWUgeW91IHBpY2tlZCBpbiBUZWxlZ3JhbS4KCiMjIFNj'
     'cmVlbnNob3RzCgo+IFRvIGRvOiBhZGQgc2NyZWVuc2hvdHMgb2YgdGhlIGV2ZW50IGxpc3QsIHRoZSBjb21wb3NlciBzaGVldCBh'
     'bmQgYSByZW1pbmRlcgo+IG1lc3NhZ2UuIEEgc2hvcnQgR0lGIG9mIGFkZGluZyBhbiBldmVudCBhbmQgcmVjZWl2aW5nIHRoZSBy'
     'ZW1pbmRlciBpcyB0aGUKPiBzaW5nbGUgaGlnaGVzdC12YWx1ZSBhZGRpdGlvbiB0byB0aGlzIHBhZ2UuCgojIyBIb3cgaXQgd29y'
     'a3MKCmBgYG1lcm1haWQKZmxvd2NoYXJ0IExSCiAgICBVKFtUZWxlZ3JhbSB1c2VyXSkKICAgIFdbIkZhc3RBUEkgd2ViPGJyLz4v'
     'd2ViYXBwICsgL2FwaS8qIl0KICAgIEhbIkZhc3RBUEkgd2ViaG9vazxici8+L3RlbGVncmFtL3dlYmhvb2siXQogICAgREJbKCJN'
     'b25nb0RCIildCiAgICBSWyJSZW1pbmRlciB3b3JrZXIiXQoKICAgIFUgLS0+fG9wZW5zIE1pbmkgQXBwfCBXCiAgICBVIC0tPnwv'
     'c3RhcnQsIGJ1dHRvbiB0YXBzfCBICiAgICBXIC0tPnxpbml0RGF0YSBITUFDIGNoZWNrfCBEQgogICAgSCAtLT4gREIKICAgIFIg'
     'LS0+fHBvbGxzIGR1ZSBldmVudHN8IERCCiAgICBSIC0tPnxzZW5kcyB0aGUgcmVtaW5kZXJ8IFUKYGBgCgpSZXF1ZXN0cyBmcm9t'
     'IHRoZSBNaW5pIEFwcCBjYXJyeSBUZWxlZ3JhbSdzIGBpbml0RGF0YWAgc3RyaW5nLiBUaGUgc2VydmVyCnJlY29tcHV0ZXMgaXRz'
     'IEhNQUMtU0hBMjU2IHNpZ25hdHVyZSB3aXRoIHRoZSBib3QgdG9rZW4sIGNoZWNrcyB0aGUgdGltZXN0YW1wCmZvciBmcmVzaG5l'
     'c3MsIGFuZCBvbmx5IHRoZW4gdHJ1c3RzIHRoZSB1c2VyIGlkIGluc2lkZS4gRXZlcnkgcmVhZCBhbmQgd3JpdGUgaXMKc2NvcGVk'
     'IHRvIHRoYXQgaWQsIHNvIG9uZSB1c2VyIGNhbiBuZXZlciByZWFjaCBhbm90aGVyIHVzZXIncyBldmVudHMuCgpUaGUgcmVtaW5k'
     'ZXIgd29ya2VyIGNsYWltcyBlYWNoIGR1ZSBldmVudCBieSBmbGlwcGluZyBpdHMgc3RhdHVzIHRvCmBwcm9jZXNzaW5nYCBiZWZv'
     'cmUgc2VuZGluZywgYW5kIHJlLXF1ZXVlcyBhbnl0aGluZyBsZWZ0IHByb2Nlc3NpbmcgZm9yIHRvbwpsb25nLiBUaGF0IG1ha2Vz'
     'IGEgY3Jhc2ggbWlkLXNlbmQgc2FmZSwgYW5kIGxldHMgbW9yZSB0aGFuIG9uZSB3b3JrZXIgcnVuIGF0Cm9uY2Ugd2l0aG91dCBz'
     'ZW5kaW5nIGEgcmVtaW5kZXIgdHdpY2UuCgojIyBUZWNoIHN0YWNrCgp8IExheWVyIHwgQ2hvaWNlIHwKfC0tLXwtLS18CnwgQVBJ'
     'IHwgRmFzdEFQSSwgUHlkYW50aWMgdjIsIFV2aWNvcm4gLyBHdW5pY29ybiB8CnwgRGF0YWJhc2UgfCBNb25nb0RCIHZpYSBNb3Rv'
     'ciwgd2l0aCBUVEwgYW5kIGNvbXBvdW5kIGluZGV4ZXMgfAp8IEJvdCB8IHB5dGhvbi10ZWxlZ3JhbS1ib3QgfAp8IEZyb250IGVu'
     'ZCB8IFZhbmlsbGEgSmF2YVNjcmlwdCwgbm8gYnVpbGQgc3RlcCwgSmluamEyIHRlbXBsYXRlIHwKfCBEYXRlcyB8IGB6b25laW5m'
     'b2AgZm9yIHRpbWV6b25lcywgYGpkYXRldGltZWAgZm9yIHRoZSBKYWxhbGkgY2FsZW5kYXIgfAp8IFF1YWxpdHkgfCBydWZmLCBw'
     'eXRlc3QsIEdpdEh1YiBBY3Rpb25zIHwKCiMjIEdldHRpbmcgc3RhcnRlZAoKUmVxdWlyZW1lbnRzOiBQeXRob24gMy4xMiwgYSBN'
     'b25nb0RCIGluc3RhbmNlIChsb2NhbCBvciBBdGxhcyksIGFuZCBhIGJvdAp0b2tlbiBmcm9tIFtAQm90RmF0aGVyXShodHRwczov'
     'L3QubWUvQm90RmF0aGVyKS4KCmBgYGJhc2gKZ2l0IGNsb25lIGh0dHBzOi8vZ2l0aHViLmNvbS93aGFtaWRyZXphdy90aW1lX21h'
     'bmFnZXJfcHJvLmdpdApjZCB0aW1lX21hbmFnZXJfcHJvCgpweXRob24gLW0gdmVudiAudmVudgpzb3VyY2UgLnZlbnYvYmluL2Fj'
     'dGl2YXRlICAgICAgICAgICMgV2luZG93czogLnZlbnZcU2NyaXB0c1xhY3RpdmF0ZQpwaXAgaW5zdGFsbCAtciByZXF1aXJlbWVu'
     'dHMudHh0CgpjcCAuZW52LmV4YW1wbGUgLmVudiAgICAgICAgICAgICAgICMgdGhlbiBmaWxsIGluIHRoZSByZWFsIHZhbHVlcwpg'
     'YGAKClJ1biB0aGUgd2ViIGFwcCBhbmQgdGhlIHdvcmtlciBpbiB0d28gdGVybWluYWxzOgoKYGBgYmFzaAouL2RlcGxveS9ydW4t'
     'ZGV2LnNoICAgICAgICAgICAgICAgICMgdXZpY29ybiBvbiBodHRwOi8vMTI3LjAuMC4xOjgwMDAKcHl0aG9uIC1tIHdvcmtlci5y'
     'dW5fb25jZSAgICAgICAgICAgIyBvbmUgcmVtaW5kZXIgcGFzcywgYXMgdGhlIEFjdGlvbiBydW5zIGl0CmBgYAoKVGhlIE1pbmkg'
     'QXBwIG9ubHkgcnVucyBpbnNpZGUgVGVsZWdyYW0sIGJlY2F1c2UgaXQgbmVlZHMgdGhlIGBpbml0RGF0YWAgdGhhdApUZWxlZ3Jh'
     'bSBpbmplY3RzLiBUbyB0cnkgaXQgbG9jYWxseSwgZXhwb3NlIHBvcnQgODAwMCB3aXRoIGEgdHVubmVsLCBzZXQKYFdFQkFQUF9C'
     'QVNFX1VSTGAgdG8gdGhlIHR1bm5lbCBVUkwsIGFuZCBwb2ludCB5b3VyIGJvdCdzIG1lbnUgYnV0dG9uIHRoZXJlCmluIEJvdEZh'
     'dGhlci4KCiMjIENvbmZpZ3VyYXRpb24KCkV2ZXJ5dGhpbmcgaXMgcmVhZCBmcm9tIGVudmlyb25tZW50IHZhcmlhYmxlczsgc2Vl'
     'IGAuZW52LmV4YW1wbGVgIGZvciB0aGUgZnVsbApsaXN0IHdpdGggY29tbWVudHMuCgp8IFZhcmlhYmxlIHwgUHVycG9zZSB8Cnwt'
     'LS18LS0tfAp8IGBCT1RfVE9LRU5gIHwgQm90IHRva2VuIGZyb20gQm90RmF0aGVyIHwKfCBgVEVMRUdSQU1fV0VCSE9PS19TRUNS'
     'RVRgIHwgU2hhcmVkIHNlY3JldCBUZWxlZ3JhbSBlY2hvZXMgYmFjayBvbiBldmVyeSB3ZWJob29rIGNhbGwgfAp8IGBNT05HT19V'
     'UklgLCBgTU9OR09fREJfTkFNRWAgfCBEYXRhYmFzZSBjb25uZWN0aW9uIHwKfCBgV0VCQVBQX0JBU0VfVVJMYCB8IFB1YmxpYyBi'
     'YXNlIFVSTDsgdGhlIHdlYmhvb2sgaXMgcmVnaXN0ZXJlZCBhZ2FpbnN0IGl0IGF0IHN0YXJ0dXAgfAp8IGBURUxFR1JBTV9CT1Rf'
     'VVNFUk5BTUVgLCBgVEVMRUdSQU1fTUlOSV9BUFBfU0hPUlRfTkFNRWAgfCBVc2VkIHRvIGJ1aWxkIGRlZXAgbGlua3MgfAp8IGBS'
     'QVRFX0xJTUlUX0NPVU5UYCB8IFJlcXVlc3RzIGFsbG93ZWQgcGVyIHVzZXIgcGVyIG1pbnV0ZSB8CnwgYE1BWF9FVkVOVFNfUEVS'
     'X1VTRVJgLCBgTUFYX1RJVExFX0xFTmAsIGBNQVhfTk9URV9MRU5gIHwgUGVyLXVzZXIgbGltaXRzIChgTUFYX0VWRU5UU19QRVJf'
     'VVNFUmAgaXMgdGhlIGhhcmQgY2VpbGluZykgfAp8IGBFVkVOVF9MSU1JVF9CQVNFYCwgYFJFRkVSUkFMX1NURVBgLCBgUkVGRVJS'
     'QUxfQk9OVVNgIHwgUmVmZXJyYWwgcmV3YXJkOiBzdGFydCBhdCAyNSBldmVudHMsICsyMCBwZXIgMyB2YWxpZCBpbnZpdGVzIHwK'
     'fCBgV0VCQVBQX0JBU0VfVVJMYCB8IEFsc28gdGhlIG9yaWdpbiBvZiBwdWJsaWMgY291bnRkb3duIGxpbmtzIChgL2MvPHRva2Vu'
     'PmApIGFuZCBzaGFyZSBjYXJkcyB8CnwgYFRBU0tTX1NFQ1JFVGAgfCBTaGFyZWQgc2VjcmV0IGZvciBgUE9TVCAvdGFza3MvcnVu'
     'LXJlbWluZGVyc2A7IGVtcHR5IGtlZXBzIHRoZSBlbmRwb2ludCBjbG9zZWQgfAp8IGBBRE1JTl9DSEFUX0lEYCB8IFlvdXIgVGVs'
     'ZWdyYW0gdXNlciBpZCDigJQgd2hlcmUgdGhlIGhlYWx0aCByZXBvcnQgaXMgc2VudCB8CnwgYE9WRVJEVUVfQUZURVJfTUlOVVRF'
     'U2AgfCBIb3cgbGF0ZSBhIHBlbmRpbmcgcmVtaW5kZXIgbWF5IGJlIGJlZm9yZSBpdCBpcyByZXBvcnRlZCB8CnwgYEhFQUxUSENI'
     'RUNLX1BJTkdfVVJMYCB8IGhlYWx0aGNoZWNrcy5pbyBwaW5nIFVSTCBvZiB0aGUgY3JvbiBjaGVjazsgZW1wdHk6IG5vIHBpbmcg'
     'KEFEUiAwMDE0KSB8CnwgYFJVTkJPT0tfVVJMYCB8IFdoZXJlIGFuIGFsZXJ0J3MgbGluayBwb2ludHM7IGRlZmF1bHRzIHRvIGBk'
     'b2NzL1JVTkJPT0subWRgIG9uIEdpdEh1YiB8CnwgYE1BWF9SRVFVRVNUX0JZVEVTYCB8IExhcmdlc3QgcmVxdWVzdCBib2R5IGFj'
     'Y2VwdGVkLCA2NTUzNiBieSBkZWZhdWx0OyBsYXJnZXIgZ2V0cyA0MTMgfAp8IGBSQVRFX0xJTUlUX0FVVEhfRkFJTF9DT1VOVGAs'
     'IGBSQVRFX0xJTUlUX0FVVEhfRkFJTF9HTE9CQUxgIHwgRmFpbGVkIGF1dGhlbnRpY2F0aW9ucyBhIG1pbnV0ZSwgcGVyIGFkZHJl'
     'c3MgKDMwKSBhbmQgaW4gdG90YWwgKDMwMCk7IG1vcmUgZ2V0IDQyOSB8CnwgYFJFTUlOREVSX0JBVENIX1NJWkVgLCBgU1RBTEVf'
     'UFJPQ0VTU0lOR19TRUNTYCB8IFJlbWluZGVyIHR1bmluZyB8CgojIyMgQ29tbWFuZHMgZm9yIGV2ZXJ5b25lCgp8IENvbW1hbmQg'
     'fCBXaGF0IGl0IGRvZXMgfAp8LS0tfC0tLXwKfCBgL3N0YXJ0YCB8IE9wZW5zIHRoZSBhcHAgfAp8IGAvaGVscGAgfCBTaG93cyB3'
     'aGF0IHRoZSBib3QgY2FuIGRvIHwKfCBgL2RlbGV0ZW15ZGF0YWAgfCBFcmFzZXMgZXZlcnl0aGluZyB0aGUgYXBwIGtlZXBzIGFi'
     'b3V0IHlvdSwgYWZ0ZXIgYSBjb25maXJtYXRpb24gKEFEUiAwMDE3KSB8CgojIyMgQWRtaW4gY29tbWFuZHMKClRoZSBhZG1pbiBp'
     'cyBgQURNSU5fQ0hBVF9JRGAgYW5kIG5vYm9keSBlbHNlOyB0byBhbnlvbmUgZWxzZSB0aGVzZSBhcmUgdW5rbm93bgpjb21tYW5k'
     'cy4gU2VuZCB0aGVtIHRvIHRoZSBib3Q6Cgp8IENvbW1hbmQgfCBEb2VzIHwKfC0tLXwtLS18CnwgYC9saW1pdHNgIHwgdGhlIGN1'
     'cnJlbnQgYmFzZSBsaW1pdCwgaW52aXRlIHJld2FyZCBhbmQgdGVjaG5pY2FsIGNlaWxpbmcgfAp8IGAvbGltaXQgQHVzZXJgIG9y'
     'IGAvbGltaXQgMTIzNDU2Nzg5YCB8IG9uZSB1c2VyJ3MgZXZlbnRzLCBsaW1pdCwgYW5kIHdoZXJlIGl0IGNvbWVzIGZyb20gfAp8'
     'IGAvbGltaXQgQHVzZXIgMTAwYCB8IGEgbGltaXQgb2YgdGhlaXIgb3duLCBmcm9tIDEgdXAgdG8gdGhlIGNlaWxpbmcgfAp8IGAv'
     'bGltaXQgQHVzZXIgdW5saW1pdGVkYCB8IG5vIGxpbWl0IG9mIHRoZWlyIG93biAodGhlIGNlaWxpbmcgc3RpbGwgYXBwbGllcykg'
     'fAp8IGAvbGltaXQgQHVzZXIgZGVmYXVsdGAgfCBiYWNrIHRvIHRoZSBmb3JtdWxhIHwKfCBgL3NldGJhc2UgMzBgLCBgL3NldGJv'
     'bnVzIDIwYCwgYC9zZXRzdGVwIDNgIHwgY2hhbmdlIHRoZSBmb3JtdWxhIGF0IG9uY2UsIG5vIHJlZGVwbG95IHwKfCBgL3NldGJh'
     'c2UgZGVmYXVsdGAgKGFuZCB0aGUgb3RoZXJzKSB8IGJhY2sgdG8gdGhlIGVudmlyb25tZW50J3MgdmFsdWUgfAp8IGAvbG9naWQg'
     'PGlkIG9yIEB1c2VybmFtZT5gIHwgVGhlIHBzZXVkb255bSB0aGF0IHVzZXIgaGFzIGluIHRoZSBsb2dzIChBRFIgMDAxOCkgfAoK'
     'VGhlIGFkbWluJ3Mgb3duIGFjY291bnQgaXMgdW5saW1pdGVkLiBBIGBAdXNlcm5hbWVgIGlzIGtub3duIG9uY2UgdGhhdCB1c2Vy'
     'CmhhcyB1c2VkIHRoZSBib3Qgb3IgdGhlIGFwcDsgdGhlIG51bWVyaWMgaWQgYWx3YXlzIHdvcmtzLiBFdmVyeSBjaGFuZ2UgaXMK'
     'd3JpdHRlbiB0byB0aGUgYGFkbWluX2F1ZGl0YCBjb2xsZWN0aW9uLgoKIyMgVGVzdHMKCmBgYGJhc2gKcnVmZiBjaGVjayAuCnB5'
     'dGVzdApgYGAKClRoZSBzdWl0ZSBjb3ZlcnMgdGhlIEhNQUMgdmVyaWZpY2F0aW9uIHBhdGgsIHRpbWVzdGFtcCB2YWxpZGF0aW9u'
     'LCByYXRlCmxpbWl0aW5nLCBkYXRlIGFuZCByZWN1cnJlbmNlIG1hdGhzLCB0aGUgZXZlbnQgQVBJLCBhbmQgdGhlIHdlYmhvb2su'
     'CgojIyBEb2N1bWVudGF0aW9uCgotIGBDSEFOR0VMT0cubWRgOiB3aGF0IGNoYW5nZWQgaW4gZWFjaCB2ZXJzaW9uCi0gYENPTlNU'
     'UkFJTlRTLm1kYDogdGhlIHF1YWxpdHkgYmFyLCB3aXRoIGl0cyBudW1iZXJzIGFuZCBkZWNpc2lvbnMKLSBgZG9jcy9SRUxFQVNF'
     'Lm1kYDogaG93IGEgdmVyc2lvbiBpcyByZWxlYXNlZCwgY2hlY2tlZCBhbmQgcm9sbGVkIGJhY2sKLSBgU0VDVVJJVFkubWRgOiBy'
     'ZXBvcnRpbmcgYSB2dWxuZXJhYmlsaXR5LCB3aGF0IGlzIGRlZmVuZGVkLCB0aGUgYWNjZXB0ZWQgcmlza3MKLSBgZG9jcy9ERUZJ'
     'TklUSU9OX09GX0RPTkUubWRgOiB3aGF0IGV2ZXJ5IGNoYW5nZSBoYXMgdG8gbWVldAotIGBkb2NzL2Fkci9gOiB0aGUgYXJjaGl0'
     'ZWN0dXJlIGRlY2lzaW9ucywgYW5kIHdoeQotIGBkb2NzL0RFQlVHR0lORy5tZGA6IGZpbmRpbmcgYSBwcm9ibGVtIGluIHByb2R1'
     'Y3Rpb24KLSBgZG9jcy9SVU5CT09LLm1kYDogd2hhdCB0byBkbyB3aGVuIGFuIGFsZXJ0IGFycml2ZXMKLSBgZG9jcy9hMTF5L1JF'
     'UVVJUkVNRU5UUy5tZGA6IHRoZSBhY2Nlc3NpYmlsaXR5IHJlcXVpcmVtZW50cyBhbmQgdGhlaXIgdGVzdHMKCiMjIERlcGxveW1l'
     'bnQKClByb2R1Y3Rpb24gaXMgb25lIFJlbmRlciB3ZWIgc2VydmljZSBydW5uaW5nIG5hdGl2ZSBQeXRob246IGJ1aWxkCmBwaXAg'
     'aW5zdGFsbCAtciByZXF1aXJlbWVudHMudHh0YCwgc3RhcnQKYGd1bmljb3JuIC1rIHV2aWNvcm5fd29ya2VyLlV2aWNvcm5Xb3Jr'
     'ZXIgYXBwLm1haW46YXBwYCAoUmVuZGVyJ3MgU3RhcnQKQ29tbWFuZCBtdXN0IHNheSBleGFjdGx5IHRoaXM7IEFEUiAwMDEzKSwg'
     'ZGVwbG95ZWQgb24gZXZlcnkgY29tbWl0IHRvIHRoZQpzZXJ2aWNlJ3MgYnJhbmNoLiBSZW1pbmRlcnMgYXJlIHNlbnQgYnkKYFBP'
     'U1QgL3Rhc2tzL3J1bi1yZW1pbmRlcnNgLCBjYWxsZWQgZXZlcnkgbWludXRlIGJ5IGFuIGV4dGVybmFsIGNyb24gd2l0aCB0aGUK'
     'YFgtVGFza3MtU2VjcmV0YCBoZWFkZXIgKEFEUiAwMDA1KS4gYC5naXRodWIvd29ya2Zsb3dzL3JlbWluZGVyLnltbGAgcnVucyB0'
     'aGUKc2FtZSBwYXNzIG9uIEdpdEh1YidzIHNjaGVkdWxlIGFzIGEgc2xvd2VyIGZhbGxiYWNrLCB3aXRoIGEgaGVhbHRoY2hlY2tz'
     'LmlvCmRlYWQtbWFuJ3Mgc3dpdGNoLiBUaGUgbG9uZy1ydW5uaW5nIHdvcmtlciBhbmQgaXRzIGRlcGxveW1lbnQgZmlsZXMgd2Vy'
     'ZQpyZW1vdmVkIGluIEJhdGNoIDI0IChBRFIgMDAxMik7IGdpdCBoaXN0b3J5IGtlZXBzIHRoZW0uCgojIyBQcm9qZWN0IHN0cnVj'
     'dHVyZQoKYGBgCmFwcC8KICBtYWluLnB5ICAgICAgICAgICAgRmFzdEFQSSBhcHAsIGxpZmVzcGFuLCB3ZWJob29rIHJlZ2lzdHJh'
     'dGlvbgogIGNvbmZpZy5weSAgICAgICAgICBTZXR0aW5ncywgdmFsaWRhdGVkIGF0IHN0YXJ0dXAKICBkYi5weSAgICAgICAgICAg'
     'ICAgTW9uZ28gY29ubmVjdGlvbiBhbmQgaW5kZXggc2V0dXAKICByb3V0ZXMvICAgICAgICAgICAgd2ViLCBldmVudHMgQVBJLCB0'
     'ZWxlZ3JhbSB3ZWJob29rLCBoZWFsdGgKICBzZXJ2aWNlcy8gICAgICAgICAgYXV0aCAoaW5pdERhdGEgKyByYXRlIGxpbWl0KSwg'
     'ZXZlbnRzLCByZW1pbmRlcnMKICBzY2hlbWFzLyAgICAgICAgICAgcmVxdWVzdCBhbmQgcmVzcG9uc2UgbW9kZWxzCiAgdXRpbHMv'
     'ICAgICAgICAgICAgIHRpbWV6b25lLCBKYWxhbGkgYW5kIHJlY3VycmVuY2UgaGVscGVycwp3b3JrZXIvICAgICAgICAgICAgICB0'
     'aGUgcmVtaW5kZXIgbG9vcCBhbmQgYSBvbmUtc2hvdCBydW5uZXIKc3RhdGljLywgdGVtcGxhdGVzLyAgdGhlIE1pbmkgQXBwCnRl'
     'c3RzLwpgYGAKCiMjIFJvYWRtYXAKCi0gVGltZXMgb2YgZGF5IG9uIGV2ZW50cywgbm90IG9ubHkgZGF0ZXMKLSBTZXJ2ZXItc2lk'
     'ZSBzZWFyY2ggYW5kIGZpbHRlcmluZwotIFBlcnNpYW4gaW50ZXJmYWNlIHdpdGggZnVsbCBSVEwgc3VwcG9ydAotIEEgcmVhbCBK'
     'YWxhbGkgZGF0ZSBwaWNrZXIKLSBNb250aCBhbmQgYWdlbmRhIHZpZXdzCi0gYC90b2RheWAgYW5kIGAvd2Vla2AgY29tbWFuZHMs'
     'IGFuZCBhIG1vcm5pbmcgZGlnZXN0CgojIyBMaWNlbnNlCgpNSVQsIHNlZSBbTElDRU5TRV0oTElDRU5TRSkuCg=='),
    ('app/__init__.py', '75ad4f4c2943325f88064ad2beae890b59674be2bf0404b30afc5584dc4de4bb', '57211c22bf5a777aa0a10b4e553a0bf82705d5e6d270b1eae7b4bc7f8b6fda0a',
     'X19hbGxfXyA9IFtdCgpfX3ZlcnNpb25fXyA9ICIxLjAuMCIgICMgb25lIHBsYWNlOyBDSEFOR0VMT0cubWQgaGFzIHRvIG1hdGNo'
     'ICh0ZXN0cy90ZXN0X3JlbGVhc2UucHkpCg=='),
    ('app/main.py', '87b68d0b4a84efb42cb8d98a933bf38d86898aad744dff4d4419205e46827f5d', '0e0c533e2f17361a1df70886c303ab92b55695bf13ad3f73d0a7280d480dee3f',
     'ZnJvbSBfX2Z1dHVyZV9fIGltcG9ydCBhbm5vdGF0aW9ucwoKaW1wb3J0IGxvZ2dpbmcKZnJvbSBjb250ZXh0bGliIGltcG9ydCBh'
     'c3luY2NvbnRleHRtYW5hZ2VyCmZyb20gcGF0aGxpYiBpbXBvcnQgUGF0aAoKZnJvbSBmYXN0YXBpIGltcG9ydCBGYXN0QVBJCmZy'
     'b20gZmFzdGFwaS5zdGF0aWNmaWxlcyBpbXBvcnQgU3RhdGljRmlsZXMKCmZyb20gYXBwIGltcG9ydCBfX3ZlcnNpb25fXwpmcm9t'
     'IGFwcC5jb25maWcgaW1wb3J0IGdldF9zZXR0aW5ncwpmcm9tIGFwcC5kYiBpbXBvcnQgKAogICAgYmFja2ZpbGxfamFsYWxpX2Rh'
     'dGVzLAogICAgY2xvc2VfbW9uZ29fY29ubmVjdGlvbiwKICAgIGNvbm5lY3RfdG9fbW9uZ28sCiAgICBlbnN1cmVfaW5kZXhlcywK'
     'ICAgIHN0b3BfZXhwaXJpbmdfb25lX29mZl9ldmVudHMsCikKZnJvbSBhcHAubWlkZGxld2FyZSBpbXBvcnQgQm9keVNpemVMaW1p'
     'dE1pZGRsZXdhcmUsIFNlY3VyaXR5SGVhZGVyc01pZGRsZXdhcmUsIGVmZmVjdGl2ZV9jc3AKZnJvbSBhcHAub2JzZXJ2YWJpbGl0'
     'eSBpbXBvcnQgUmVxdWVzdENvbnRleHRNaWRkbGV3YXJlLCBjb25maWd1cmVfbG9nZ2luZwpmcm9tIGFwcC5yb3V0ZXMuY2FsZW5k'
     'YXIgaW1wb3J0IHJvdXRlciBhcyBjYWxlbmRhcl9yb3V0ZXIKZnJvbSBhcHAucm91dGVzLmNoYXRzIGltcG9ydCByb3V0ZXIgYXMg'
     'Y2hhdHNfcm91dGVyCmZyb20gYXBwLnJvdXRlcy5ldmVudHMgaW1wb3J0IHJvdXRlciBhcyBldmVudHNfcm91dGVyCmZyb20gYXBw'
     'LnJvdXRlcy5oZWFsdGggaW1wb3J0IHJvdXRlciBhcyBoZWFsdGhfcm91dGVyCmZyb20gYXBwLnJvdXRlcy5yZWZlcnJhbCBpbXBv'
     'cnQgcm91dGVyIGFzIHJlZmVycmFsX3JvdXRlcgpmcm9tIGFwcC5yb3V0ZXMuc2hhcmUgaW1wb3J0IHJvdXRlciBhcyBzaGFyZV9y'
     'b3V0ZXIKZnJvbSBhcHAucm91dGVzLnNoYXJlZ3JvdXAgaW1wb3J0IHJvdXRlciBhcyBzaGFyZWdyb3VwX3JvdXRlcgpmcm9tIGFw'
     'cC5yb3V0ZXMudGFza3MgaW1wb3J0IHJvdXRlciBhcyB0YXNrc19yb3V0ZXIKZnJvbSBhcHAucm91dGVzLnRlbGVncmFtIGltcG9y'
     'dCByb3V0ZXIgYXMgdGVsZWdyYW1fcm91dGVyCmZyb20gYXBwLnJvdXRlcy53ZWIgaW1wb3J0IHJvdXRlciBhcyB3ZWJfcm91dGVy'
     'CmZyb20gYXBwLnNlcnZpY2VzLnRlbGVncmFtX2JvdCBpbXBvcnQgc3RhcnRfc2hhcmVkX2JvdCwgc3RvcF9zaGFyZWRfYm90Cgpz'
     'ZXR0aW5ncyA9IGdldF9zZXR0aW5ncygpCgpjb25maWd1cmVfbG9nZ2luZyhzZXR0aW5ncykgICMgSlNPTiBsaW5lcyB3aXRoIHJl'
     'cXVlc3QgaWRzIChBRFIgMDAxMSkKCgpsb2dnZXIgPSBsb2dnaW5nLmdldExvZ2dlcigidG1fcHJvLmFwcCIpCgpCQVNFX0RJUiA9'
     'IFBhdGgoX19maWxlX18pLnJlc29sdmUoKS5wYXJlbnRzWzFdClNUQVRJQ19ESVIgPSBCQVNFX0RJUiAvICJzdGF0aWMiCgoKQGFz'
     'eW5jY29udGV4dG1hbmFnZXIKYXN5bmMgZGVmIGxpZmVzcGFuKGFwcDogRmFzdEFQSSk6CiAgICBsb2dnZXIuaW5mbygiU3RhcnRp'
     'bmcgJXMgJXMgKCVzKSIsIHNldHRpbmdzLmFwcF9uYW1lLCBfX3ZlcnNpb25fXywgc2V0dGluZ3MuYXBwX2VudikKCiAgICB0cnk6'
     'CiAgICAgICAgIyBPbmUgYm90IGZvciB0aGUgYXBwIChBRFIgMDAxOSk7IHN0YXJ0aW5nIGl0IGFscmVhZHkgYXNrZWQgVGVsZWdy'
     'YW0gd2hvIGl0IGlzLgogICAgICAgIGJvdCA9IGF3YWl0IHN0YXJ0X3NoYXJlZF9ib3Qoc2V0dGluZ3MpCiAgICAgICAgbG9nZ2Vy'
     'LmluZm8oIlJ1bnRpbWUgYm90ID0gQCVzIGlkPSVzIiwgYm90LmJvdC51c2VybmFtZSwgYm90LmJvdC5pZCkKCiAgICAgICAgd2Vi'
     'aG9va191cmwgPSBmIntzZXR0aW5ncy53ZWJhcHBfYmFzZV91cmx9L3RlbGVncmFtL3dlYmhvb2siCiAgICAgICAgYXdhaXQgYm90'
     'LnNldF93ZWJob29rKHVybD13ZWJob29rX3VybCwgc2VjcmV0X3Rva2VuPXNldHRpbmdzLnRlbGVncmFtX3dlYmhvb2tfc2VjcmV0'
     'KQogICAgICAgIGxvZ2dlci5pbmZvKCJUZWxlZ3JhbSB3ZWJob29rIHNldCB0byAlcyIsIHdlYmhvb2tfdXJsKQogICAgZXhjZXB0'
     'IEV4Y2VwdGlvbiBhcyBleGM6CiAgICAgICAgbG9nZ2VyLndhcm5pbmcoIlJ1bnRpbWUgYm90IHZlcmlmaWNhdGlvbi93ZWJob29r'
     'IHNldHVwIGZhaWxlZDogJXMiLCBleGMpCgogICAgYXdhaXQgY29ubmVjdF90b19tb25nbyhzZXR0aW5ncykKICAgIGF3YWl0IGVu'
     'c3VyZV9pbmRleGVzKHNldHRpbmdzKQoKICAgICMgT25lLW9mZiBtaWdyYXRpb246IGEgbm8tb3Agb24gZXZlcnkgYm9vdCBhZnRl'
     'ciB0aGUgZmlyc3QsIGFuZCBhIGZhaWx1cmUKICAgICMgaGVyZSBtdXN0IG5vdCBzdG9wIHRoZSBhcHAgZnJvbSBzZXJ2aW5nLgog'
     'ICAgdHJ5OgogICAgICAgIGF3YWl0IGJhY2tmaWxsX2phbGFsaV9kYXRlcygpCiAgICAgICAgYXdhaXQgc3RvcF9leHBpcmluZ19v'
     'bmVfb2ZmX2V2ZW50cygpCiAgICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgIGxvZ2dlci5leGNlcHRpb24oIlN0YXJ0dXAgbWln'
     'cmF0aW9uIGZhaWxlZDsgc2VhcmNoIG9yIGFyY2hpdmluZyBtYXkgYmUgaW5jb21wbGV0ZSIpCgogICAgeWllbGQKICAgIGF3YWl0'
     'IHN0b3Bfc2hhcmVkX2JvdCgpCgogICAgYXdhaXQgY2xvc2VfbW9uZ29fY29ubmVjdGlvbigpCiAgICBsb2dnZXIuaW5mbygiU3Rv'
     'cHBlZCAlcyIsIHNldHRpbmdzLmFwcF9uYW1lKQoKCmFwcCA9IEZhc3RBUEkoCiAgICB0aXRsZT1zZXR0aW5ncy5hcHBfbmFtZSwK'
     'ICAgIHZlcnNpb249X192ZXJzaW9uX18sCiAgICBkZWJ1Zz1zZXR0aW5ncy5hcHBfZGVidWcsCiAgICBsaWZlc3Bhbj1saWZlc3Bh'
     'biwKKQoKIyBCZWZvcmUgdGhlIHJvdXRlcywgc28gaXQgYWxzbyBjb3ZlcnMgL3N0YXRpYyBhbmQgYW55IGVycm9yIHJlc3BvbnNl'
     'IHRoZQojIGZyYW1ld29yayBwcm9kdWNlcyBvbiBpdHMgb3duLgojIElubmVybW9zdCBvZiB0aGUgdGhyZWU6IGEgYm9keSBvdmVy'
     'IHRoZSBsaW1pdCBpcyByZWZ1c2VkIGJlZm9yZSBpdCBpcyByZWFkLgphcHAuYWRkX21pZGRsZXdhcmUoQm9keVNpemVMaW1pdE1p'
     'ZGRsZXdhcmUsIG1heF9ieXRlcz1zZXR0aW5ncy5tYXhfcmVxdWVzdF9ieXRlcykKYXBwLmFkZF9taWRkbGV3YXJlKFNlY3VyaXR5'
     'SGVhZGVyc01pZGRsZXdhcmUsIHBvbGljeT1lZmZlY3RpdmVfY3NwKHNldHRpbmdzKSkKIyBBZGRlZCBsYXN0LCBzbyBpdCBpcyB0'
     'aGUgb3V0ZXJtb3N0OiBpdHMgaWQgYW5kIHRpbWluZyBjb3ZlciBldmVyeXRoaW5nIGJlbG93LgphcHAuYWRkX21pZGRsZXdhcmUo'
     'UmVxdWVzdENvbnRleHRNaWRkbGV3YXJlKQoKYXBwLm1vdW50KCIvc3RhdGljIiwgU3RhdGljRmlsZXMoZGlyZWN0b3J5PXN0cihT'
     'VEFUSUNfRElSKSksIG5hbWU9InN0YXRpYyIpCgphcHAuaW5jbHVkZV9yb3V0ZXIoaGVhbHRoX3JvdXRlcikKYXBwLmluY2x1ZGVf'
     'cm91dGVyKHdlYl9yb3V0ZXIpCmFwcC5pbmNsdWRlX3JvdXRlcihldmVudHNfcm91dGVyKQphcHAuaW5jbHVkZV9yb3V0ZXIoY2Fs'
     'ZW5kYXJfcm91dGVyKQphcHAuaW5jbHVkZV9yb3V0ZXIoY2hhdHNfcm91dGVyKQphcHAuaW5jbHVkZV9yb3V0ZXIocmVmZXJyYWxf'
     'cm91dGVyKQphcHAuaW5jbHVkZV9yb3V0ZXIoc2hhcmVfcm91dGVyKQphcHAuaW5jbHVkZV9yb3V0ZXIodGFza3Nfcm91dGVyKQph'
     'cHAuaW5jbHVkZV9yb3V0ZXIoc2hhcmVncm91cF9yb3V0ZXIpCmFwcC5pbmNsdWRlX3JvdXRlcih0ZWxlZ3JhbV9yb3V0ZXIpCg=='),
    ('docs/RELEASE.md', None, 'cfc00a27bc05f773540955bc1934317bedb5df23427fb1e4ebefd31e1c4e8004',
     'IyBSZWxlYXNpbmcgYSB2ZXJzaW9uCgpBIHZlcnNpb24gaXMgcmVsZWFzZWQgb25jZSBldmVyeSBjaGFuZ2UgaW4gaXQgaXMgbWVy'
     'Z2VkLCBhbmQgb25seSB3aGVuIHRoZQpsaXN0IGJlbG93IGhvbGRzLiBUaGUgdGFnIG5hbWVzIHRoZSBjb21taXQgdGhhdCBwcm9k'
     'dWN0aW9uIHJ1bnMuCgojIyBCZWZvcmUgdGhlIHRhZwoKRm9yIDEuMC4wLCBlYWNoIGNoZWNrZWQgb24gMjAyNi0wOS0yNyB1bmxl'
     'c3MgaXQgc2F5cyBvdGhlcndpc2U6CgotIFt4XSBDSSBpcyBncmVlbiBvbiBgbWFpbmAsIGFuZCB0aGUga25vd24tZmFpbHVyZXMg'
     'bGlzdCBpcyBlbXB0eS4KLSBbeF0gVGhlIHdob2xlIHN1aXRlIHBhc3NlcyB3aXRoIG5vIGRlcHJlY2F0aW9uIHdhcm5pbmdzLCBh'
     'Ym92ZSB0aGUKICAgICAgY292ZXJhZ2UgZmxvb3IgaW4gYENPTlNUUkFJTlRTLm1kYCAoODMuOTYgJSBhZ2FpbnN0IDc3ICUpLgot'
     'IFt4XSBgcGlwLWF1ZGl0IC1yIHJlcXVpcmVtZW50cy50eHRgIGZpbmRzIG5vIGtub3duIHZ1bG5lcmFiaWxpdHkuCi0gW3hdIENs'
     'b3VkZmxhcmUncyByYW5nZXMgaW4gYGFwcC91dGlscy9uZXQucHlgIG1hdGNoCiAgICAgIGh0dHBzOi8vd3d3LmNsb3VkZmxhcmUu'
     'Y29tL2lwcy8gKHVuY2hhbmdlZCBzaW5jZSAyMDIzLTA5LTI4KS4KLSBbeF0gVGhlIGFsZXJ0IHBhdGggd2FzIHRlc3QtZmlyZWQg'
     'aW4gcHJvZHVjdGlvbiAoQmF0Y2ggMjUpLgotIFt4XSBTZWN1cml0eSBhdWRpdCAoQmF0Y2ggMjcpLCBmaXZlLWF4aXMgcmV2aWV3'
     'IChCYXRjaCAyOSkgYW5kCiAgICAgIHBlcmZvcm1hbmNlIHJldmlldyAoQmF0Y2hlcyAzMCBhbmQgMzEpIGFyZSBkb25lOyB3aGF0'
     'IHdhaXRzIGlzIHVuZGVyCiAgICAgICJLbm93biBkZWJ0IiBpbiBgQ09OU1RSQUlOVFMubWRgLgotIFsgXSBBIHNjcmVlbi1yZWFk'
     'ZXIgcGFzcyB3aXRoIFRhbGtCYWNrIG9uIEFuZHJvaWQsIGJ5IGhhbmQuCi0gW3hdIGBDSEFOR0VMT0cubWRgIGhhcyB0aGUgdmVy'
     'c2lvbiwgZGF0ZWQsIGFuZCBgYXBwL19faW5pdF9fLnB5YCBzYXlzIHRoZQogICAgICBzYW1lIChhIHRlc3QgY2hlY2tzIHRoZSB0'
     'd28pLgoKIyMgVGFnZ2luZwoKRnJvbSBhbiB1cC10by1kYXRlIGBtYWluYCwgYWZ0ZXIgdGhlIGxhc3QgcHVsbCByZXF1ZXN0IGlz'
     'IG1lcmdlZDoKCiAgICBnaXQgc3dpdGNoIG1haW4KICAgIGdpdCBwdWxsIC0tZmYtb25seQogICAgZ2l0IHRhZyAtYSB2MS4wLjAg'
     'LW0gIlRpbWVNYW5hZ2VyIFBybyAxLjAuMCIKICAgIGdpdCBwdXNoIG9yaWdpbiB2MS4wLjAKClRoZW4sIG9uIEdpdEh1YiwgY3Jl'
     'YXRlIGEgcmVsZWFzZSBmcm9tIHRoZSB0YWcgYW5kIHBhc3RlIGl0cyBzZWN0aW9uIG9mCmBDSEFOR0VMT0cubWRgIGFzIHRoZSBu'
     'b3Rlcy4KCiMjIEFmdGVyIHRoZSBkZXBsb3kKCi0gUmVuZGVyJ3MgbG9nIHNob3dzIGBTdGFydGluZyBUaW1lTWFuYWdlciBQcm8g'
     'MS4wLjAgKHByb2R1Y3Rpb24pYC4KLSBPcGVuIHRoZSBNaW5pIEFwcCwgYWRkIGFuIGV2ZW50LCBhbmQgc2VlIGl0IGluIHRoZSBs'
     'aXN0LgotIEFuIGV2ZW50IHdpdGggYSByZW1pbmRlciB0aHJlZSBtaW51dGVzIGFoZWFkIHNlbmRzIGl0IG9uIHRpbWUuCi0gYFBP'
     'U1QgL3Rhc2tzL2FsZXJ0LXRlc3RgIGFuc3dlcnMgYHRlbGVncmFtOiBUcnVlLCBoZWFsdGhjaGVja3M6IFRydWVgCiAgKGBkb2Nz'
     'L1JVTkJPT0subWRgLCBUZXN0LWZpcmluZykuCgojIyBSb2xsaW5nIGJhY2sKClJlbmRlciwgdGhlIHNlcnZpY2UsIEV2ZW50czog'
     'cmVkZXBsb3kgdGhlIGxhc3QgZ29vZCBkZXBsb3kuIEZvciBhIGxvbmdlciB3YXkKYmFjaywgcmV2ZXJ0IHRoZSBtZXJnZSBjb21t'
     'aXQgb24gR2l0SHViIGFuZCBsZXQgdGhlIGF1dG8tZGVwbG95IHJ1bi4gQSB0YWcgaXMKbmV2ZXIgbW92ZWQgb3IgcmV1c2VkOiBh'
     'IGZpeCBzaGlwcyBhcyB0aGUgbmV4dCBwYXRjaCB2ZXJzaW9uLCBgMS4wLjFgLgo='),
    ('tests/test_release.py', None, '903dc12f793d6e5a82d04be9bf2d6723cbc61f2bf01bc30077abccbafb855c9a',
     'IiIiVGhlIHJlbGVhc2UgKEJhdGNoIDMyOyBkb2NzL1JFTEVBU0UubWQpLgoKT25lIHZlcnNpb24sIGluIG9uZSBwbGFjZSBpbiB0'
     'aGUgY29kZSwgd2hpY2ggRmFzdEFQSSByZXBvcnRzIGFuZCB0aGUgc3RhcnQtdXAKbGluZSBuYW1lcywgYW5kIHdoaWNoIHRoZSB0'
     'b3AgZW50cnkgb2YgQ0hBTkdFTE9HLm1kIGhhcyB0byBtYXRjaCwgc28gYSByZWxlYXNlCmNhbm5vdCBzaGlwIHdpdGggaXRzIG5v'
     'dGVzIGRlc2NyaWJpbmcgYW5vdGhlci4gUkVMRUFTRS5tZCBob2xkcyB0aGUgY2hlY2tsaXN0CmEgdmVyc2lvbiBwYXNzZXMgYmVm'
     'b3JlIGl0cyB0YWcsIGFuZCBob3cgdG8gcm9sbCBvbmUgYmFjay4KIiIiCmZyb20gX19mdXR1cmVfXyBpbXBvcnQgYW5ub3RhdGlv'
     'bnMKCmltcG9ydCByZQpmcm9tIHBhdGhsaWIgaW1wb3J0IFBhdGgKClJPT1QgPSBQYXRoKF9fZmlsZV9fKS5yZXNvbHZlKCkucGFy'
     'ZW50c1sxXQoKCmRlZiB0ZXN0X29uZV92ZXJzaW9uX2V2ZXJ5d2hlcmUoKToKICAgIGltcG9ydCBhcHAKICAgIGltcG9ydCBhcHAu'
     'bWFpbiBhcyBtYWluX21vZHVsZQoKICAgIGNoYW5nZWxvZyA9IChST09UIC8gIkNIQU5HRUxPRy5tZCIpLnJlYWRfdGV4dChlbmNv'
     'ZGluZz0idXRmLTgiKQogICAgdG9wID0gcmUuc2VhcmNoKHIiXiMjIFxbKFxkK1wuXGQrXC5cZCspXF0gLSBcZHs0fS1cZHsyfS1c'
     'ZHsyfSQiLCBjaGFuZ2Vsb2csIHJlLk0pCiAgICBhc3NlcnQgdG9wLCAiQ0hBTkdFTE9HLm1kIGhhcyBubyBkYXRlZCByZWxlYXNl'
     'IGVudHJ5IgogICAgYXNzZXJ0IGFwcC5fX3ZlcnNpb25fXyA9PSB0b3AuZ3JvdXAoMSkgPT0gbWFpbl9tb2R1bGUuYXBwLnZlcnNp'
     'b24sICgKICAgICAgICBhcHAuX192ZXJzaW9uX18sIHRvcC5ncm91cCgxKSwgbWFpbl9tb2R1bGUuYXBwLnZlcnNpb24pCgoKZGVm'
     'IHRlc3RfdGhlX2NoYW5nZWxvZ19zYXlzX3doYXRfY2hhbmdlZF9pbl90aGVfdXN1YWxfc2VjdGlvbnMoKToKICAgIGNoYW5nZWxv'
     'ZyA9IChST09UIC8gIkNIQU5HRUxPRy5tZCIpLnJlYWRfdGV4dChlbmNvZGluZz0idXRmLTgiKQogICAgZmlyc3QgPSBjaGFuZ2Vs'
     'b2cuc3BsaXQoIlxuIyMgWyIsIDIpWzFdCiAgICBmb3Igc2VjdGlvbiBpbiAoIiMjIyBBZGRlZCIsICIjIyMgQ2hhbmdlZCIsICIj'
     'IyMgRml4ZWQiLCAiIyMjIFNlY3VyaXR5IiwgIiMjIyBSZW1vdmVkIik6CiAgICAgICAgYXNzZXJ0IHNlY3Rpb24gaW4gZmlyc3Qs'
     'IHNlY3Rpb24KCgpkZWYgdGVzdF90aGVfcmVsZWFzZV9ndWlkZV9oYXNfaXRzX2NoZWNrbGlzdF9hbmRfYV93YXlfYmFjaygpOgog'
     'ICAgZ3VpZGUgPSAoUk9PVCAvICJkb2NzIiAvICJSRUxFQVNFLm1kIikucmVhZF90ZXh0KGVuY29kaW5nPSJ1dGYtOCIpCiAgICBm'
     'b3IgaGVhZGluZyBpbiAoIiMjIEJlZm9yZSB0aGUgdGFnIiwgIiMjIFRhZ2dpbmciLCAiIyMgQWZ0ZXIgdGhlIGRlcGxveSIsICIj'
     'IyBSb2xsaW5nIGJhY2siKToKICAgICAgICBhc3NlcnQgaGVhZGluZyBpbiBndWlkZSwgaGVhZGluZwo='),
]


def current(path: str) -> bytes | None:
    target = ROOT / path
    return target.read_bytes().replace(b"\r\n", b"\n") if target.is_file() else None


def apply() -> bool:
    if not (ROOT / "app" / "main.py").exists() or not (ROOT / "scripts" / "check_known_failures.py").exists():
        print("Run this from the time_manager_pro repository root.")
        return False
    plan, problems = [], []
    for path, before, after, content in FILES:
        data = current(path)
        digest = hashlib.sha256(data).hexdigest() if data is not None else None
        if digest == after:
            plan.append((path, "done", None))
        elif digest == before:
            plan.append((path, "remove" if after is None else "write",
                         None if content is None else base64.b64decode(content)))
        elif data is None:
            problems.append(f"{path}: missing (apply the previous step first)")
        else:
            problems.append(f"{path}: not the version this step was built on (apply the previous step first)")
    if problems:
        print("Nothing was changed:")
        print("\n".join(f"  - {p}" for p in problems))
        return False
    for step, (path, action, data) in enumerate(plan, 1):
        target = ROOT / path
        if action == "done":
            print(f"Step {step:2d} already applied: {path}")
        elif action == "remove":
            target.unlink()
            parent = target.parent
            while parent != ROOT and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
            print(f"Step {step:2d} OK (removed): {path}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            print(f"Step {step:2d} OK: {path}")
    print("\nDone. Expected now: pytest -> 0 failed, 548 passed.")
    if REQUIREMENTS_CHANGED:
        print("requirements.txt changed: --commit installs it; after applying by hand run")
        print("    python -m pip install -r requirements.txt")
    return True


def git_out(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True).stdout.strip()


def run(*cmd: str) -> int:
    print("\n> " + " ".join(cmd), flush=True)
    return subprocess.call(list(cmd))


def stop(reason: str) -> int:
    print(f"\nSTOP: {reason} Nothing was committed.")
    return 1


def commit() -> int:
    branch = git_out("rev-parse", "--abbrev-ref", "HEAD")
    if branch in ("", "HEAD", "main", "master"):
        return stop(f"you are on '{branch or 'no branch'}'. Create the feature branch first; no file was touched.")
    if not apply():
        return stop("the step did not apply.")
    if REQUIREMENTS_CHANGED and run(sys.executable, "-m", "pip", "install", "-q", "-r", "requirements.txt") != 0:
        return stop("pip could not install requirements.txt.")
    if run(sys.executable, "-m", "ruff", "check", ".") != 0:
        return stop("ruff found problems.")
    if run(sys.executable, "scripts/check_known_failures.py") != 0:
        return stop("the gate failed. Please send me the KNOWN-FAILURES GATE lines above.")
    subprocess.call(["git", "rm", "-q", "-f", "--ignore-unmatch", SELF])
    (ROOT / SELF).unlink(missing_ok=True)
    strays = git_out("ls-files", "--others", "--exclude-standard", "apply_*.py").split()
    run("git", "add", "-A")
    if strays:  # untracked copies of other steps stay out of this commit
        subprocess.call(["git", "reset", "-q", "--", *strays])
    if run("git", "commit", "-q", "-m", SUBJECT, "-m", BODY) != 0:
        return stop("git commit failed.")
    done = git_out("rev-parse", "--short", "HEAD")
    if run("git", "push", "-u", "origin", "HEAD") != 0:
        print(f"\nCommitted {done}, but the push failed: run  git push  again.")
        return 1
    print(f"\nCOMMITTED AND PUSHED: {done} on {branch}. Next: the pull request into main.")
    left = sorted(p.name for p in ROOT.glob("apply_*.py"))
    if left:
        print("Still in the tree (each removes itself when run): " + ", ".join(left))
    return 0


if __name__ == "__main__":
    sys.exit(commit() if "--commit" in sys.argv[1:] else (0 if apply() else 1))
