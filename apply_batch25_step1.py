#!/usr/bin/env python3
"""Batch 25, step 1: production starts through the uvicorn-worker package.

Run from the repository root, on the feature branch:

    python apply_batch25_step1.py --commit    apply, check, commit and push, in one go
    python apply_batch25_step1.py             apply only

--commit stops at the first problem and then leaves git untouched: on main,
when a file is not the version this step was built on, when ruff fails, or
when the known-failures gate fails. It removes only itself, then commits with
the message below and pushes. A second run reports everything as applied.
Expected afterwards: pytest -> 0 failed, 457 passed.
"""
from __future__ import annotations

import base64
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
SELF = Path(__file__).name
SUBJECT = 'build: start production through uvicorn-worker'
BODY = 'Render starts the app with gunicorn and uvicorn.workers.UvicornWorker, which the pinned uvicorn deprecates in favour of the uvicorn-worker package; an upgrade that drops it would stop production at start-up, and no test starts gunicorn. As chosen (ADR 0013): uvicorn-worker 0.3.0 is pinned (0.4.0 needs uvicorn 0.36), and the README gives the Start Command Render must use, gunicorn -k uvicorn_worker.UvicornWorker app.main:app, to be set only after this is deployed. A test checks the pin, the README, and that the class loads without a deprecation.'
REQUIREMENTS_CHANGED = True

# (path, sha256 before or None if new, sha256 after or None if removed, content)
FILES = [
    ('README.md', 'fa956408d1e2a234b7663ae4e3622caa1b933c24a8886208fcaa839315c66fbc', '100a017861af7d77ef56af9da7d7976cd617cf53d6a28d4d3ebbd6a0f8186871',
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
     'U2AgfCBIb3cgbGF0ZSBhIHBlbmRpbmcgcmVtaW5kZXIgbWF5IGJlIGJlZm9yZSBpdCBpcyByZXBvcnRlZCB8CnwgYFJFTUlOREVS'
     'X0JBVENIX1NJWkVgLCBgU1RBTEVfUFJPQ0VTU0lOR19TRUNTYCB8IFJlbWluZGVyIHR1bmluZyB8CgojIyMgQWRtaW4gY29tbWFu'
     'ZHMKClRoZSBhZG1pbiBpcyBgQURNSU5fQ0hBVF9JRGAgYW5kIG5vYm9keSBlbHNlOyB0byBhbnlvbmUgZWxzZSB0aGVzZSBhcmUg'
     'dW5rbm93bgpjb21tYW5kcy4gU2VuZCB0aGVtIHRvIHRoZSBib3Q6Cgp8IENvbW1hbmQgfCBEb2VzIHwKfC0tLXwtLS18CnwgYC9s'
     'aW1pdHNgIHwgdGhlIGN1cnJlbnQgYmFzZSBsaW1pdCwgaW52aXRlIHJld2FyZCBhbmQgdGVjaG5pY2FsIGNlaWxpbmcgfAp8IGAv'
     'bGltaXQgQHVzZXJgIG9yIGAvbGltaXQgMTIzNDU2Nzg5YCB8IG9uZSB1c2VyJ3MgZXZlbnRzLCBsaW1pdCwgYW5kIHdoZXJlIGl0'
     'IGNvbWVzIGZyb20gfAp8IGAvbGltaXQgQHVzZXIgMTAwYCB8IGEgbGltaXQgb2YgdGhlaXIgb3duLCBmcm9tIDEgdXAgdG8gdGhl'
     'IGNlaWxpbmcgfAp8IGAvbGltaXQgQHVzZXIgdW5saW1pdGVkYCB8IG5vIGxpbWl0IG9mIHRoZWlyIG93biAodGhlIGNlaWxpbmcg'
     'c3RpbGwgYXBwbGllcykgfAp8IGAvbGltaXQgQHVzZXIgZGVmYXVsdGAgfCBiYWNrIHRvIHRoZSBmb3JtdWxhIHwKfCBgL3NldGJh'
     'c2UgMzBgLCBgL3NldGJvbnVzIDIwYCwgYC9zZXRzdGVwIDNgIHwgY2hhbmdlIHRoZSBmb3JtdWxhIGF0IG9uY2UsIG5vIHJlZGVw'
     'bG95IHwKfCBgL3NldGJhc2UgZGVmYXVsdGAgKGFuZCB0aGUgb3RoZXJzKSB8IGJhY2sgdG8gdGhlIGVudmlyb25tZW50J3MgdmFs'
     'dWUgfAoKVGhlIGFkbWluJ3Mgb3duIGFjY291bnQgaXMgdW5saW1pdGVkLiBBIGBAdXNlcm5hbWVgIGlzIGtub3duIG9uY2UgdGhh'
     'dCB1c2VyCmhhcyB1c2VkIHRoZSBib3Qgb3IgdGhlIGFwcDsgdGhlIG51bWVyaWMgaWQgYWx3YXlzIHdvcmtzLiBFdmVyeSBjaGFu'
     'Z2UgaXMKd3JpdHRlbiB0byB0aGUgYGFkbWluX2F1ZGl0YCBjb2xsZWN0aW9uLgoKIyMgVGVzdHMKCmBgYGJhc2gKcnVmZiBjaGVj'
     'ayAuCnB5dGVzdApgYGAKClRoZSBzdWl0ZSBjb3ZlcnMgdGhlIEhNQUMgdmVyaWZpY2F0aW9uIHBhdGgsIHRpbWVzdGFtcCB2YWxp'
     'ZGF0aW9uLCByYXRlCmxpbWl0aW5nLCBkYXRlIGFuZCByZWN1cnJlbmNlIG1hdGhzLCB0aGUgZXZlbnQgQVBJLCBhbmQgdGhlIHdl'
     'Ymhvb2suCgojIyBEb2N1bWVudGF0aW9uCgotIGBDT05TVFJBSU5UUy5tZGA6IHRoZSBxdWFsaXR5IGJhciwgd2l0aCBpdHMgbnVt'
     'YmVycyBhbmQgZGVjaXNpb25zCi0gYGRvY3MvREVGSU5JVElPTl9PRl9ET05FLm1kYDogd2hhdCBldmVyeSBjaGFuZ2UgaGFzIHRv'
     'IG1lZXQKLSBgZG9jcy9hZHIvYDogdGhlIGFyY2hpdGVjdHVyZSBkZWNpc2lvbnMsIGFuZCB3aHkKLSBgZG9jcy9ERUJVR0dJTkcu'
     'bWRgOiBmaW5kaW5nIGEgcHJvYmxlbSBpbiBwcm9kdWN0aW9uCi0gYGRvY3MvYTExeS9SRVFVSVJFTUVOVFMubWRgOiB0aGUgYWNj'
     'ZXNzaWJpbGl0eSByZXF1aXJlbWVudHMgYW5kIHRoZWlyIHRlc3RzCgojIyBEZXBsb3ltZW50CgpQcm9kdWN0aW9uIGlzIG9uZSBS'
     'ZW5kZXIgd2ViIHNlcnZpY2UgcnVubmluZyBuYXRpdmUgUHl0aG9uOiBidWlsZApgcGlwIGluc3RhbGwgLXIgcmVxdWlyZW1lbnRz'
     'LnR4dGAsIHN0YXJ0CmBndW5pY29ybiAtayB1dmljb3JuX3dvcmtlci5Vdmljb3JuV29ya2VyIGFwcC5tYWluOmFwcGAgKFJlbmRl'
     'cidzIFN0YXJ0CkNvbW1hbmQgbXVzdCBzYXkgZXhhY3RseSB0aGlzOyBBRFIgMDAxMyksIGRlcGxveWVkIG9uIGV2ZXJ5IGNvbW1p'
     'dCB0byB0aGUKc2VydmljZSdzIGJyYW5jaC4gUmVtaW5kZXJzIGFyZSBzZW50IGJ5CmBQT1NUIC90YXNrcy9ydW4tcmVtaW5kZXJz'
     'YCwgY2FsbGVkIGV2ZXJ5IG1pbnV0ZSBieSBhbiBleHRlcm5hbCBjcm9uIHdpdGggdGhlCmBYLVRhc2tzLVNlY3JldGAgaGVhZGVy'
     'IChBRFIgMDAwNSkuIGAuZ2l0aHViL3dvcmtmbG93cy9yZW1pbmRlci55bWxgIHJ1bnMgdGhlCnNhbWUgcGFzcyBvbiBHaXRIdWIn'
     'cyBzY2hlZHVsZSBhcyBhIHNsb3dlciBmYWxsYmFjaywgd2l0aCBhIGhlYWx0aGNoZWNrcy5pbwpkZWFkLW1hbidzIHN3aXRjaC4g'
     'VGhlIGxvbmctcnVubmluZyB3b3JrZXIgYW5kIGl0cyBkZXBsb3ltZW50IGZpbGVzIHdlcmUKcmVtb3ZlZCBpbiBCYXRjaCAyNCAo'
     'QURSIDAwMTIpOyBnaXQgaGlzdG9yeSBrZWVwcyB0aGVtLgoKIyMgUHJvamVjdCBzdHJ1Y3R1cmUKCmBgYAphcHAvCiAgbWFpbi5w'
     'eSAgICAgICAgICAgIEZhc3RBUEkgYXBwLCBsaWZlc3Bhbiwgd2ViaG9vayByZWdpc3RyYXRpb24KICBjb25maWcucHkgICAgICAg'
     'ICAgU2V0dGluZ3MsIHZhbGlkYXRlZCBhdCBzdGFydHVwCiAgZGIucHkgICAgICAgICAgICAgIE1vbmdvIGNvbm5lY3Rpb24gYW5k'
     'IGluZGV4IHNldHVwCiAgcm91dGVzLyAgICAgICAgICAgIHdlYiwgZXZlbnRzIEFQSSwgdGVsZWdyYW0gd2ViaG9vaywgaGVhbHRo'
     'CiAgc2VydmljZXMvICAgICAgICAgIGF1dGggKGluaXREYXRhICsgcmF0ZSBsaW1pdCksIGV2ZW50cywgcmVtaW5kZXJzCiAgc2No'
     'ZW1hcy8gICAgICAgICAgIHJlcXVlc3QgYW5kIHJlc3BvbnNlIG1vZGVscwogIHV0aWxzLyAgICAgICAgICAgICB0aW1lem9uZSwg'
     'SmFsYWxpIGFuZCByZWN1cnJlbmNlIGhlbHBlcnMKd29ya2VyLyAgICAgICAgICAgICAgdGhlIHJlbWluZGVyIGxvb3AgYW5kIGEg'
     'b25lLXNob3QgcnVubmVyCnN0YXRpYy8sIHRlbXBsYXRlcy8gIHRoZSBNaW5pIEFwcAp0ZXN0cy8KYGBgCgojIyBSb2FkbWFwCgot'
     'IFRpbWVzIG9mIGRheSBvbiBldmVudHMsIG5vdCBvbmx5IGRhdGVzCi0gU2VydmVyLXNpZGUgc2VhcmNoIGFuZCBmaWx0ZXJpbmcK'
     'LSBQZXJzaWFuIGludGVyZmFjZSB3aXRoIGZ1bGwgUlRMIHN1cHBvcnQKLSBBIHJlYWwgSmFsYWxpIGRhdGUgcGlja2VyCi0gTW9u'
     'dGggYW5kIGFnZW5kYSB2aWV3cwotIGAvdG9kYXlgIGFuZCBgL3dlZWtgIGNvbW1hbmRzLCBhbmQgYSBtb3JuaW5nIGRpZ2VzdAoK'
     'IyMgTGljZW5zZQoKTUlULCBzZWUgW0xJQ0VOU0VdKExJQ0VOU0UpLgo='),
    ('docs/adr/0013-production-starts-through-uvicorn-worker.md', None, '13dfd7a729e399ec83bde6cd44e393495d43e5d6e52e3cb781e549f3889b901f',
     'IyAwMDEzLiBQcm9kdWN0aW9uIHN0YXJ0cyB0aHJvdWdoIHRoZSB1dmljb3JuLXdvcmtlciBwYWNrYWdlCgpTdGF0dXM6IEFjY2Vw'
     'dGVkCkRlY2lkZWQ6IEJhdGNoIDI1LCBjaG9zZW4gZnJvbSBvcHRpb25zLiBSZWNvcmRlZCAyMDI2LTA5LTI2IChCYXRjaCAyNSku'
     'CgojIyBDb250ZXh0ClJlbmRlciBzdGFydHMgdGhlIGFwcCB3aXRoIGBndW5pY29ybiAtayB1dmljb3JuLndvcmtlcnMuVXZpY29y'
     'bldvcmtlcmAuIEluIHRoZQpwaW5uZWQgdXZpY29ybiAoMC4zMC42KSB0aGF0IG1vZHVsZSBpcyBkZXByZWNhdGVkIGluIGZhdm91'
     'ciBvZiB0aGUgc2VwYXJhdGUKYHV2aWNvcm4td29ya2VyYCBwYWNrYWdlLCBhbmQgYW4gdXBncmFkZSB0aGF0IGRyb3BzIGl0IHdv'
     'dWxkIHN0b3AgcHJvZHVjdGlvbgphdCBzdGFydC11cC4gTm8gdGVzdCBzdGFydHMgZ3VuaWNvcm4sIHNvIHRoZSBzdWl0ZSBuZXZl'
     'ciBzYXcgdGhlIHdhcm5pbmcuCgojIyBEZWNpc2lvbgpgdXZpY29ybi13b3JrZXI9PTAuMy4wYCBpcyBwaW5uZWQ6IDAuNC4wIG5l'
     'ZWRzIHV2aWNvcm4gMC4zNiBvciBsYXRlciwgYW4KdXBncmFkZSBvZiBpdHMgb3duLiBUaGUgU3RhcnQgQ29tbWFuZCBiZWNvbWVz'
     'CmBndW5pY29ybiAtayB1dmljb3JuX3dvcmtlci5Vdmljb3JuV29ya2VyIGFwcC5tYWluOmFwcGAsIHNldCBpbiBSZW5kZXIncwpz'
     'ZXR0aW5ncyBvbmx5IGFmdGVyIHRoZSBwaW4gaXMgZGVwbG95ZWQsIHNvIHRoZSBwYWNrYWdlIGlzIHRoZXJlIHdoZW4gdGhlIG5l'
     'dwpjb21tYW5kIHJ1bnMuIFBsYWluIHV2aWNvcm4gd2l0aG91dCBndW5pY29ybiB3YXMgdGhlIG90aGVyIG9wdGlvbjsgaXQga2Vl'
     'cHMgb25lCmRlcGVuZGVuY3kgZmV3ZXIgYnV0IGdpdmVzIHVwIGd1bmljb3JuJ3MgcHJvY2VzcyBtYW5hZ2VtZW50LgoKIyMgQ29u'
     'c2VxdWVuY2VzClRoZSBzYW1lIHNlcnZlciBhbmQgYmVoYXZpb3VyLCB0aHJvdWdoIGEgc3VwcG9ydGVkIHBhdGguIFRoZSBjb21t'
     'YW5kIGxpdmVzIGluClJlbmRlcidzIHNldHRpbmdzLCBub3QgaW4gdGhlIHJlcG9zaXRvcnksIHNvIHRoZSBSRUFETUUgc3RhdGVz'
     'IGl0IGFuZApgdGVzdHMvdGVzdF9zdGFydF9jb21tYW5kLnB5YCBjaGVja3MgdGhlIFJFQURNRSwgdGhlIHBpbiwgYW5kIHRoYXQg'
     'dGhlIGNsYXNzCmxvYWRzIHdpdGhvdXQgYSBkZXByZWNhdGlvbiAob24gTGludXg7IGd1bmljb3JuIGRvZXMgbm90IGltcG9ydCBv'
     'biBXaW5kb3dzKS4KUm9sbGluZyBiYWNrIGlzIHJlc3RvcmluZyB0aGUgb2xkIFN0YXJ0IENvbW1hbmQuCg=='),
    ('docs/adr/README.md', '1a3f52ecd74dc182582728c62ccf4db9ef513092a143f533841cac99f01d72f9', '27e37591e195e59a7a24bba120bd4f9066f86f40bdce8e92d9409b3d8ba5700b',
     'IyBBcmNoaXRlY3R1cmUgZGVjaXNpb24gcmVjb3JkcwoKT25lIGZpbGUgcGVyIGRlY2lzaW9uIHRoYXQgYSBsYXRlciBjaGFuZ2Ug'
     'aGFzIHRvIHJlc3BlY3Q6IGl0cyBzdGF0dXMsIHRoZQpjb250ZXh0IHRoYXQgZm9yY2VkIGl0LCB0aGUgZGVjaXNpb24sIGFuZCB3'
     'aGF0IGZvbGxvd3MgZnJvbSBpdC4gQSBkZWNpc2lvbiBpcwpuZXZlciBlZGl0ZWQgYXdheTsgYSBuZXcgcmVjb3JkIHN1cGVyc2Vk'
     'ZXMgaXQgKGBTdGF0dXM6IFN1cGVyc2VkZWQgYnkgMDBOTmApLgpBZGQgdGhlIG5leHQgbnVtYmVyIHdpdGggdGhlIHNhbWUgZm91'
     'ciBzZWN0aW9uczsgYHRlc3RzL3Rlc3RfZG9jcy5weWAgY2hlY2tzCnRoYXQgZXZlcnkgcmVjb3JkIGhhcyB0aGVtIGFuZCBpcyBs'
     'aXN0ZWQgaGVyZS4KCi0gWzAwMDEuIERhdGV0aW1lcyBhcmUgdGltZXpvbmUtYXdhcmUgZnJvbSB0aGUgZGF0YWJhc2Ugb25dKDAw'
     'MDEtdGltZXpvbmUtYXdhcmUtZGF0ZXRpbWVzLm1kKSAoQmF0Y2ggMTkpCi0gWzAwMDIuIFRoZSBhcHAgZG9lcyBub3QgZGVueSBm'
     'cmFtaW5nXSgwMDAyLW5vLWZyYW1pbmctZGVuaWFsLm1kKSAoQmF0Y2ggMTkpCi0gWzAwMDMuIEdhcmJhZ2UgY29sbGVjdGlvbiB3'
     'YWl0cyBhIGZ1bGwgcGVyaW9kIHBsdXMgdGhpcnR5IGRheXNdKDAwMDMtdHRsLWEtZnVsbC1wZXJpb2QtcGx1cy10aGlydHktZGF5'
     'cy5tZCkgKEJhdGNoIDE5KQotIFswMDA0LiBFYWNoIHJlbWluZGVyIG9jY3VycmVuY2UgaGFzIGFuIGlkZW1wb3RlbmN5IGtleV0o'
     'MDAwNC1hbi1pZGVtcG90ZW5jeS1rZXktcGVyLXJlbWluZGVyLm1kKSAoQmF0Y2ggMTkpCi0gWzAwMDUuIFJlbWluZGVycyBhcmUg'
     'dHJpZ2dlcmVkIGZyb20gb3V0c2lkZSwgZXZlcnkgbWludXRlXSgwMDA1LXJlbWluZGVycy1hcmUtdHJpZ2dlcmVkLWZyb20tb3V0'
     'c2lkZS5tZCkgKEJhdGNoIDE4OyBtZWFzdXJlZCBpbiBCYXRjaCAyMCkKLSBbMDAwNi4gRXZlcnkgZGlhbG9nIGlzIGEgbmF0aXZl'
     'IGRpYWxvZyBvbiBvbmUgbW9kYWwgc3RhY2tdKDAwMDYtbmF0aXZlLWRpYWxvZ3MtYW5kLW9uZS1tb2RhbC1zdGFjay5tZCkgKEJh'
     'dGNoIDIwIChENSwgRDYsIEQ3KSkKLSBbMDAwNy4gQSBiYXRjaCBjYXJyaWVzIGl0cyBmYWlsaW5nIHRlc3RzIGJ5IG5hbWVdKDAw'
     'MDctdGhlLWtub3duLWZhaWx1cmVzLWdhdGUubWQpIChCYXRjaCAyMCkKLSBbMDAwOC4gQSBmYWlsaW5nIGNvbG91ciBrZWVwcyBp'
     'dHMgaHVlIGFuZCBtb3ZlcyBvbmx5IGFzIGZhciBhcyBpdCBtdXN0XSgwMDA4LXRoZS1jb2xvdXItY29udHJhc3QtcnVsZS5tZCkg'
     'KEJhdGNoIDIwIChENCwgRDExKSkKLSBbMDAwOS4gTGltaXRzIGFyZSBtYW5hZ2VkIGZyb20gdGhlIGJvdCwgYnkgb25lIGFkbWlu'
     'XSgwMDA5LWFkbWluLWNvbnRyb2wtb2YtbGltaXRzLm1kKSAoQmF0Y2ggMjAgKEQxMykpCi0gWzAwMTAuIFRlc3RzIHJlYWNoIG5v'
     'IG5ldHdvcmsgYW5kIHBpbiBjYXVzZXMsIG5vdCBjbG9ja3NdKDAwMTAtdGVzdHMtYXJlLWhlcm1ldGljLWFuZC1tZWFzdXJlLWNh'
     'dXNlcy5tZCkgKEJhdGNoIDIwKQotIFswMDExLiBMb2dzIGFyZSBKU09OIGxpbmVzLCBhbmQgUkVEIGNvbWVzIGZyb20gdGhlbV0o'
     'MDAxMS1zdHJ1Y3R1cmVkLWxvZ3MtYW5kLXJlZC1mcm9tLWxvZy1saW5lcy5tZCkgKEJhdGNoIDIzKQotIFswMDEyLiBPbmUgcmVt'
     'aW5kZXIgcGF0aDogdGhlIGVuZHBvaW50LCB3aXRoIHRoZSBBY3Rpb24gYXMgZmFsbGJhY2tdKDAwMTItb25lLXJlbWluZGVyLXBh'
     'dGgubWQpIChCYXRjaCAyNCkKLSBbMDAxMy4gUHJvZHVjdGlvbiBzdGFydHMgdGhyb3VnaCB0aGUgdXZpY29ybi13b3JrZXIgcGFj'
     'a2FnZV0oMDAxMy1wcm9kdWN0aW9uLXN0YXJ0cy10aHJvdWdoLXV2aWNvcm4td29ya2VyLm1kKSAoQmF0Y2ggMjUpCg=='),
    ('requirements.txt', '2f885a7d568f915d2427150daa64f38ecd759373cf0faf6321b69cf3a8771c40', 'f85f73516f549efd64f9547027b0e0f6c173883bedfe1ee93c4cef6d5a0f1e70',
     'IyA9PT09PT09PT09PT09PT09PT09PSBURUxFR1JBTSBCT1QgPT09PT09PT09PT09PT09PT09PT0KcHl0aG9uLXRlbGVncmFtLWJv'
     'dD09MjEuMQoKIyA9PT09PT09PT09PT09PT09PT09PSBXRUIgRlJBTUVXT1JLID09PT09PT09PT09PT09PT09PT09CiMgMC4xMTUu'
     'NiBwaW5zIHN0YXJsZXR0ZTwwLjQyLjAsIHdoaWNoIGhlbGQgc3RhcmxldHRlIG9uIGEgcmVsZWFzZSB3aXRoCiMgc2V2ZW4gb3Bl'
     'biBhZHZpc29yaWVzLiBCdW1waW5nIGZhc3RhcGkgaXMgd2hhdCB1bnBpbnMgaXQuIFZlcmlmaWVkCiMgYWdhaW5zdCB0aGUgZnVs'
     'bCBzdWl0ZTogMjM0IHByZS1leGlzdGluZyB0ZXN0cyBwYXNzIHVuY2hhbmdlZC4KZmFzdGFwaT09MC4xNDEuMQp1dmljb3JuW3N0'
     'YW5kYXJkXT09MC4zMC42Cmd1bmljb3JuPT0yMi4wLjAKdXZpY29ybi13b3JrZXI9PTAuMy4wICAjIHRoZSB3b3JrZXIgY2xhc3Mg'
     'Z3VuaWNvcm4gc3RhcnRzIChBRFIgMDAxMykKCiMgPT09PT09PT09PT09PT09PT09PT0gU0VUVElOR1MgLyBWQUxJREFUSU9OID09'
     'PT09PT09PT09PT09PT09PT09CnB5ZGFudGljPT0yLjEwLjMKcHlkYW50aWMtc2V0dGluZ3M9PTIuNy4wCgojID09PT09PT09PT09'
     'PT09PT09PT09IERBVEFCQVNFID09PT09PT09PT09PT09PT09PT09Cm1vdG9yPT0zLjYuMApweW1vbmdvPj00LjksPDQuMTAKZG5z'
     'cHl0aG9uPj0yLjcsPDMKCiMgPT09PT09PT09PT09PT09PT09PT0gVEVNUExBVEVTID09PT09PT09PT09PT09PT09PT09Cmppbmph'
     'Mj09My4xLjYKCiMgPT09PT09PT09PT09PT09PT09PT0gVVRJTElUSUVTID09PT09PT09PT09PT09PT09PT09CmNlcnRpZmk9PTIw'
     'MjQuOC4zMApqZGF0ZXRpbWU9PTUuMC4wCiMgVGhlIElBTkEgdGltZXpvbmUgZGF0YWJhc2UuIExpbnV4IGhvc3RzIHVzdWFsbHkg'
     'c2hpcCBpdCwgV2luZG93cyBhbmQgc2xpbQojIGNvbnRhaW5lcnMgZG8gbm90LCBhbmQgd2l0aG91dCBpdCBldmVyeSBldmVudCBz'
     'aWxlbnRseSBmYWxscyBiYWNrIHRvIFVUQy4KIyBGb3IgYSB0aW1lem9uZS1jcml0aWNhbCBhcHAgdGhhdCBpcyBub3QgYSBkZXBl'
     'bmRlbmN5IHRvIGxlYXZlIHRvIHRoZSBPUy4KdHpkYXRhPT0yMDI2LjQKIyBSdW50aW1lLCBub3QgdGVzdDogYXBwL3NlcnZpY2Vz'
     'L3RlbGVncmFtX2FwaS5weSBjYWxscyBpdCBkaXJlY3RseSBmb3IKIyB0aGUgQm90IEFQSSA4LjAgbWV0aG9kcyBweXRob24tdGVs'
     'ZWdyYW0tYm90IDIxLjEgZG9lcyBub3QgZXhwb3NlLgpodHRweD09MC4yOC4xCgojID09PT09PT09PT09PT09PT09PT09IFNIQVJF'
     'IENBUkRTIChCYXRjaCAxMmIpID09PT09PT09PT09PT09PT09PT09ClBpbGxvdz09MTIuMy4wCiMgT25seSB1c2VkIHdoZW4gUGls'
     'bG93IHdhcyBidWlsdCB3aXRob3V0IFJhcW07IHRoZSB3aGVlbHMgYnVuZGxlIGl0LAojIHNvIG9uIGEgbm9ybWFsIGluc3RhbGwg'
     'dGhlc2UgdHdvIG5ldmVyIHJ1bi4KYXJhYmljLXJlc2hhcGVyPT0zLjAuMQpweXRob24tYmlkaT09MC42LjExCg=='),
    ('tests/test_start_command.py', None, '5d173fb8102c41ea5a3ce30bd95118cdf1f78d201c4b95be9ff40bd64e83c316',
     'IiIiUHJvZHVjdGlvbiBzdGFydHMgdGhyb3VnaCB1dmljb3JuLXdvcmtlciAoQmF0Y2ggMjUsIEFEUiAwMDEzKS4KClJlbmRlciBz'
     'dGFydHMgdGhlIGFwcCB3aXRoIGd1bmljb3JuIGFuZCBhIHV2aWNvcm4gd29ya2VyIGNsYXNzLiBUaGUgY2xhc3MgaXQKbmFtZWQs'
     'IHV2aWNvcm4ud29ya2Vycy5Vdmljb3JuV29ya2VyLCBpcyBkZXByZWNhdGVkIGluIHRoZSBwaW5uZWQgdXZpY29ybiBpbgpmYXZv'
     'dXIgb2YgdGhlIHV2aWNvcm4td29ya2VyIHBhY2thZ2UsIGFuZCBhbiB1cGdyYWRlIHRoYXQgZHJvcHMgaXQgd291bGQgc3RvcApw'
     'cm9kdWN0aW9uIGF0IHN0YXJ0LXVwLiBUaGUgdGVzdHMgbmV2ZXIgc3RhcnQgZ3VuaWNvcm4sIHNvIHRoZXkgZGlkIG5vdCBzZWUg'
     'aXQuCmd1bmljb3JuIGNhbm5vdCBiZSBpbXBvcnRlZCBvbiBXaW5kb3dzOyB0aGVyZSB0aGUgcGFja2FnZSBpcyBvbmx5IGxvb2tl'
     'ZCB1cCwKYW5kIENJIG9uIExpbnV4IGltcG9ydHMgaXQgZm9yIHJlYWwuCiIiIgpmcm9tIF9fZnV0dXJlX18gaW1wb3J0IGFubm90'
     'YXRpb25zCgppbXBvcnQgaW1wb3J0bGliLnV0aWwKaW1wb3J0IHJlCmltcG9ydCBzeXMKZnJvbSBwYXRobGliIGltcG9ydCBQYXRo'
     'CgpST09UID0gUGF0aChfX2ZpbGVfXykucmVzb2x2ZSgpLnBhcmVudHNbMV0KU1RBUlQgPSAiZ3VuaWNvcm4gLWsgdXZpY29ybl93'
     'b3JrZXIuVXZpY29ybldvcmtlciBhcHAubWFpbjphcHAiCgoKZGVmIHRlc3RfdXZpY29ybl93b3JrZXJfaXNfcGlubmVkX2FuZF9p'
     'bnN0YWxsZWQoKToKICAgIHJlcXVpcmVtZW50cyA9IChST09UIC8gInJlcXVpcmVtZW50cy50eHQiKS5yZWFkX3RleHQoZW5jb2Rp'
     'bmc9InV0Zi04IikKICAgIHBpbm5lZCA9IHJlLnNlYXJjaChyIl51dmljb3JuLXdvcmtlcj09XGQiLCByZXF1aXJlbWVudHMsIHJl'
     'Lk0pCiAgICBhc3NlcnQgcGlubmVkLCAicmVxdWlyZW1lbnRzLnR4dCBkb2VzIG5vdCBwaW4gdXZpY29ybi13b3JrZXIiCiAgICBh'
     'c3NlcnQgaW1wb3J0bGliLnV0aWwuZmluZF9zcGVjKCJ1dmljb3JuX3dvcmtlciIpIGlzIG5vdCBOb25lLCAidXZpY29ybi13b3Jr'
     'ZXIgaXMgbm90IGluc3RhbGxlZCIKCgpkZWYgdGVzdF90aGVfd29ya2VyX2NsYXNzX2xvYWRzX3dpdGhvdXRfYV9kZXByZWNhdGlv'
     'bigpOgogICAgaWYgc3lzLnBsYXRmb3JtID09ICJ3aW4zMiI6ICAjIGd1bmljb3JuIGRvZXMgbm90IGltcG9ydCBvbiBXaW5kb3dz'
     'CiAgICAgICAgYXNzZXJ0IGltcG9ydGxpYi51dGlsLmZpbmRfc3BlYygidXZpY29ybl93b3JrZXIiKSBpcyBub3QgTm9uZQogICAg'
     'ICAgIHJldHVybgogICAgaW1wb3J0IHV2aWNvcm5fd29ya2VyICAjIGEgRGVwcmVjYXRpb25XYXJuaW5nIGlzIGFuIGVycm9yIGlu'
     'IHRoaXMgc3VpdGUKCiAgICBhc3NlcnQgaGFzYXR0cih1dmljb3JuX3dvcmtlciwgIlV2aWNvcm5Xb3JrZXIiKQoKCmRlZiB0ZXN0'
     'X3RoZV9yZWFkbWVfZ2l2ZXNfdGhlX3N0YXJ0X2NvbW1hbmRfcmVuZGVyX211c3RfdXNlKCk6CiAgICByZWFkbWUgPSAoUk9PVCAv'
     'ICJSRUFETUUubWQiKS5yZWFkX3RleHQoZW5jb2Rpbmc9InV0Zi04IikKICAgIGFzc2VydCBTVEFSVCBpbiByZWFkbWUKICAgIGFz'
     'c2VydCAidXZpY29ybi53b3JrZXJzLlV2aWNvcm5Xb3JrZXIiIG5vdCBpbiByZWFkbWUK'),
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
    print("\nDone. Expected now: pytest -> 0 failed, 457 passed.")
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
