#!/usr/bin/env python3
"""Batch 20, step 7: CI tells known failures from new ones.

A batch carries failing tests on purpose until they are fixed, which kept CI
red on every push and so said nothing: a real regression, or a flaky test,
looked the same as the known findings. tests/known_failures.txt now lists them
(10, each with its requirement), and on fix/** branches CI runs
scripts/check_known_failures.py, which fails on any other failure and on a
listed test that starts to pass, by name. main and pull requests into main run
the full suite unchanged. The same script is the gate before every commit.
Nothing is skipped or marked; the tests stay red.

Run from the repository root on fix/batch20-a11y:  python apply_batch20_step7.py

Every file is checked against the exact version this step was built on
(SHA-256) before anything is written. If any file differs, nothing is changed.
A second run reports everything as already applied.
Expected afterwards: pytest -> 10 failed, 376 passed.
"""
from __future__ import annotations

import base64
import hashlib
import sys
from pathlib import Path

ROOT = Path.cwd()

# (path, sha256 before or None for a new file, sha256 after, content)
FILES = [
    ('.github/workflows/ci.yml', '47b002e0b3e4564892a2f67977380b3c0f0d550f619530c7a7e83ac5bd99594c', 'eba6f2e52f03f758ad9fd0da740523f05439675e1da78f2a2407ea55760b638f',
     'bmFtZTogQ0kKb246CiAgcHVzaDoKICAgIGJyYW5jaGVzOiBbbWFpbiwgImZpeC8qKiJdCiAgcHVsbF9yZXF1ZXN0OgogICAgYnJh'
     'bmNoZXM6IFttYWluXQpqb2JzOgogIHRlc3Q6CiAgICBydW5zLW9uOiB1YnVudHUtbGF0ZXN0CiAgICBlbnY6CiAgICAgIFRFTEVH'
     'UkFNX1dFQkhPT0tfU0VDUkVUOiAiZHVtbXlfc2VjcmV0X2Zvcl9jaV9vbmx5IgogICAgICBCT1RfVE9LRU46ICIxMjM0NTY6Q0lf'
     'VEVTVF9UT0tFTiIKICAgICAgTU9OR09fVVJJOiAibW9uZ29kYjovLzEyNy4wLjAuMToyNzAxNyIKICAgICAgQVBQX0VOVjogImRl'
     'dmVsb3BtZW50IgogICAgc3RlcHM6CiAgICAgIC0gdXNlczogYWN0aW9ucy9jaGVja291dEB2NAogICAgICAtIG5hbWU6IFNldCB1'
     'cCBQeXRob24gMy4xMgogICAgICAgIHVzZXM6IGFjdGlvbnMvc2V0dXAtcHl0aG9uQHY1CiAgICAgICAgd2l0aDoKICAgICAgICAg'
     'IHB5dGhvbi12ZXJzaW9uOiAiMy4xMiIKICAgICAgLSBuYW1lOiBJbnN0YWxsIGRlcGVuZGVuY2llcwogICAgICAgIHJ1bjogfAog'
     'ICAgICAgICAgcGlwIGluc3RhbGwgLXIgcmVxdWlyZW1lbnRzLnR4dAogICAgICAgICAgcGlwIGluc3RhbGwgLXIgcmVxdWlyZW1l'
     'bnRzLWRldi50eHQKICAgICAgLSBuYW1lOiBJbnN0YWxsIENocm9taXVtIGZvciB0aGUgYnJvd3NlciB0ZXN0cwogICAgICAgICMg'
     'dGVzdHMvYnJvd3NlciBydW5zIHRoZSByZWFsIGFwcCBpbiBDaHJvbWl1bSB3aXRoIHZlbmRvcmVkIGF4ZS1jb3JlLgogICAgICAg'
     'IHJ1bjogcHl0aG9uIC1tIHBsYXl3cmlnaHQgaW5zdGFsbCAtLXdpdGgtZGVwcyBjaHJvbWl1bQogICAgICAtIG5hbWU6IEF1ZGl0'
     'IHByb2R1Y3Rpb24gZGVwZW5kZW5jaWVzCiAgICAgICAgIyBQcm9kdWN0aW9uIG9ubHkuIHJlcXVpcmVtZW50cy1kZXYudHh0IGlz'
     'IGRlbGliZXJhdGVseSBub3QKICAgICAgICAjIGF1ZGl0ZWQ6IHB5dGVzdCBuZXZlciBydW5zIG9uIHRoZSBzZXJ2ZXIsIGFuZCBw'
     'eXRlc3QtYXN5bmNpbwogICAgICAgICMgcGlucyBpdCBiZWxvdyB0aGUgcmVsZWFzZSB0aGF0IGZpeGVzIGl0cyBhZHZpc29yeS4g'
     'QmxvY2tpbmcKICAgICAgICAjIHJhdGhlciB0aGFuIGFkdmlzb3J5LCBiZWNhdXNlIHRoaXMgZ2F0ZSBpcyBncmVlbiB0b2RheSBh'
     'bmQgYQogICAgICAgICMgZ2F0ZSB0aGF0IHN0YXJ0cyByZWQgaXMgb25lIGV2ZXJ5b25lIGxlYXJucyB0byBpZ25vcmUuCiAgICAg'
     'ICAgcnVuOiBwaXAtYXVkaXQgLS1yZXF1aXJlbWVudCByZXF1aXJlbWVudHMudHh0CiAgICAgIC0gbmFtZTogTGludCAocnVmZikK'
     'ICAgICAgICBydW46IHJ1ZmYgY2hlY2sgLgogICAgICAtIG5hbWU6IFJ1biB0ZXN0cwogICAgICAgICMgbWFpbiBhbmQgcHVsbCBy'
     'ZXF1ZXN0cyBpbnRvIG1haW46IGV2ZXJ5IHRlc3QgaGFzIHRvIHBhc3MuCiAgICAgICAgaWY6IGdpdGh1Yi5yZWYgPT0gJ3JlZnMv'
     'aGVhZHMvbWFpbicgfHwgZ2l0aHViLmV2ZW50X25hbWUgPT0gJ3B1bGxfcmVxdWVzdCcKICAgICAgICBydW46IHB5dGVzdCAtdgog'
     'ICAgICAtIG5hbWU6IFJ1biB0ZXN0cyAob25seSB0aGUga25vd24gZmFpbHVyZXMgbWF5IGZhaWwpCiAgICAgICAgIyBmaXgvKiog'
     'YnJhbmNoZXM6IGEgYmF0Y2ggY2FycmllcyBpdHMgZmFpbGluZyB0ZXN0cyBvbiBwdXJwb3NlIHVudGlsCiAgICAgICAgIyB0aGV5'
     'IGFyZSBmaXhlZCAodGVzdHMva25vd25fZmFpbHVyZXMudHh0KS4gVGhpcyBmYWlscyBvbiBhbnkgb3RoZXIKICAgICAgICAjIGZh'
     'aWx1cmUsIGFuZCBvbiBhIGtub3duIGZhaWx1cmUgdGhhdCBzdGFydGVkIHRvIHBhc3MsIGJ5IG5hbWUuCiAgICAgICAgaWY6IGdp'
     'dGh1Yi5yZWYgIT0gJ3JlZnMvaGVhZHMvbWFpbicgJiYgZ2l0aHViLmV2ZW50X25hbWUgIT0gJ3B1bGxfcmVxdWVzdCcKICAgICAg'
     'ICBydW46IHB5dGhvbiBzY3JpcHRzL2NoZWNrX2tub3duX2ZhaWx1cmVzLnB5Cg=='),
    ('CONSTRAINTS.md', '458c1beea4e3626e6541f8002ecacd3be70455b101a66b296ca2a22568d113b1', '900e0d710b04c09084c10d883007a50ce1eafc268a99916ecfa664361c01f8ee',
     'IyBDb25zdHJhaW50cwoKTGFzdCByZXZpZXdlZDogMjAyNi0wOS0xNiAoQmF0Y2ggMjAsIHN0ZXAgMCkuCgpTY29wZSB0b2RheSBp'
     'cyB0aGUgZmxvb3IgYW5kIGFjY2Vzc2liaWxpdHkuIENvdmVyYWdlLCBzZWN1cml0eSwgcGVyZm9ybWFuY2UKYW5kIG9ic2VydmFi'
     'aWxpdHkgcm93cyBiZWxvbmcgdG8gQmF0Y2ggMjAgcHJpb3JpdHkgIzIgYW5kIGFyZSBhZGRlZCB0aGVuLiBUaGlzCmZpbGUgaXMg'
     'bm90IHdlYWtlbmVkIGluIHRoZSBzYW1lIGNvbW1pdCBhcyBhIGNoYW5nZSB0aGF0IHdhcyBmYWlsaW5nIGl0LgoKIyMgRmxvb3Ig'
     'KGFsd2F5cykKCi0gTm8gc2tpcHBlZCwgeGZhaWxlZCBvciBkZWxldGVkIHRlc3RzIHRvIGdldCBncmVlbi4gQSB0ZXN0IHRoYXQg'
     'aXMgd3JvbmcgaXMKICBmaXhlZCBpbiBpdHMgb3duIGNvbW1pdCwgd2l0aCB0aGUgcmVhc29uIGluIHRoZSBtZXNzYWdlLgotIE5v'
     'IHRocmVzaG9sZCBiZWxvdyBpcyBsb3dlcmVkIGluIHRoZSBzYW1lIGNoYW5nZSB0aGF0IHdhcyBmYWlsaW5nIGl0LgotIE5vIG5l'
     'dyBydWZmIGV4Y2x1c2lvbnMgb3IgYCMgbm9xYWAgZm9yIHRlc3QgZmlsZXMuCi0gVGlnaHRlbmluZyB0aGlzIGZpbGUgaXMgcXVp'
     'ZXQuIExvb3NlbmluZyBpdCBpcyBhIHJldmlld2VkIGNoYW5nZSBvZiBpdHMgb3duLgoKLSAqKktub3duIGZhaWx1cmVzLioqIEEg'
     'YmF0Y2ggbWF5IGNhcnJ5IGZhaWxpbmcgdGVzdHMgb24gcHVycG9zZSwgYnV0IG9ubHkKICB0aGUgb25lcyBpbiBgdGVzdHMva25v'
     'd25fZmFpbHVyZXMudHh0YC4gT24gYGZpeC8qKmAgYnJhbmNoZXMgQ0kgcnVucwogIGBzY3JpcHRzL2NoZWNrX2tub3duX2ZhaWx1'
     'cmVzLnB5YCwgd2hpY2ggZmFpbHMgb24gYW55IG90aGVyIGZhaWx1cmUgYW5kIG9uIGEKICBsaXN0ZWQgdGVzdCB0aGF0IHBhc3Nl'
     'czsgZWFjaCBmaXggcmVtb3ZlcyBpdHMgbGluZXMuIGBtYWluYCBhbmQgcHVsbAogIHJlcXVlc3RzIGludG8gaXQgcnVuIHRoZSBm'
     'dWxsIHN1aXRlLCB3aGVyZSB0aGUgbGlzdCBoYXMgdG8gYmUgZW1wdHkuIFRoaXMgaXMKICBub3QgYSB3YXkgYXJvdW5kIHRoZSBy'
     'dWxlIGFib3ZlOiBub3RoaW5nIGlzIHNraXBwZWQgb3IgbWFya2VkLCB0aGUgdGVzdHMKICBzdGF5IHJlZCBhbmQgYXJlIGNvdW50'
     'ZWQgYXMgcmVkLgoKIyMgRW5mb3JjZWQgd2l0aCBudW1iZXJzCgp8IERpbWVuc2lvbiB8IFJ1bGUgfCBDaGVja2VkIGJ5IHwgUnVu'
     'cyBhdCB8CnwtLS0tLS0tLS0tLXwtLS0tLS18LS0tLS0tLS0tLS0tfC0tLS0tLS0tLXwKfCBBY2Nlc3NpYmlsaXR5IChleHRlcm5h'
     'bCkgfCBaZXJvIHNlcmlvdXMgb3IgY3JpdGljYWwgYXhlIHZpb2xhdGlvbnMsIHRhZ3MgYHdjYWcyYSB3Y2FnMmFhIHdjYWcyMWEg'
     'd2NhZzIxYWFgLCBvbiBsaXN0IChsaWdodCBhbmQgZGFyayksIGNvbXBvc2VyLCBkZXRhaWwgYW5kIG1vbnRoIHwgYXhlLWNvcmUg'
     'NC4xMy4wLCB2ZW5kb3JlZCBhbmQgU0hBLTI1NiBwaW5uZWQsIGluIENocm9taXVtIHRocm91Z2ggUGxheXdyaWdodDogYHB5dGVz'
     'dCAtbSBicm93c2VyYCB8IENJOiBldmVyeSBwdXNoIHRvIGBtYWluYCBhbmQgYGZpeC8qKmAsIGFuZCBldmVyeSBQUiB8CnwgQWNj'
     'ZXNzaWJpbGl0eSAoYmVoYXZpb3VyKSB8IEExMVktMDEgdG8gQTExWS0wOSBpbiBgZG9jcy9hMTF5L1JFUVVJUkVNRU5UUy5tZGAg'
     'Z3JlZW4gfCBgcHl0ZXN0IHRlc3RzL2Jyb3dzZXIgdGVzdHMvdGVzdF9hMTF5X2ZpbmRpbmdzLnB5YCB8IENJIHwKfCBDb250cmFz'
     'dCAoZmFzdCkgfCBGYWxsYmFjayBwYWxldHRlIHRleHQgcGFpcnMg4omlIDQuNToxIHwgYHB5dGVzdCB0ZXN0cy90ZXN0X2ExMXlf'
     'ZmluZGluZ3MucHlgIHwgRXZlcnkgbG9jYWwgcnVuIChtaWxsaXNlY29uZHMpIHwKfCBUYXJnZXQgc2l6ZSB8IFByaW1hcnkgY29u'
     'dHJvbHMg4omlIDQ0w5c0NCBDU1MgcHggKEQzKSB8IGB0ZXN0X3ByaW1hcnlfY29udHJvbHNfYXJlX2F0X2xlYXN0XzQ0X2Nzc19w'
     'aXhlbHNgIHwgQ0kgfAp8IENsZWFuIGNvbnNvbGUgfCBObyBwYWdlIGVycm9yIGFuZCBubyBDU1AgdmlvbGF0aW9uIGluc2lkZSBU'
     'ZWxlZ3JhbSB8IHRlYXJkb3duIG9mIHRoZSBgb3Blbl9hcHBgIGZpeHR1cmUgfCBDSSB8CnwgTGludCB8IGBydWZmIGNoZWNrIC5g'
     'IGNsZWFuIHwgcnVmZiAwLjguNCB8IENJIHwKfCBQcm9kdWN0aW9uIGRlcGVuZGVuY2llcyB8IE5vIGtub3duIHZ1bG5lcmFiaWxp'
     'dGllcyB8IGBwaXAtYXVkaXQgLS1yZXF1aXJlbWVudCByZXF1aXJlbWVudHMudHh0YCB8IENJIHwKClRoZSBmYXN0IGxvb3AgY2Fu'
     'IGxlYXZlIHRoZSBicm93c2VyIG91dCB3aXRoIGBweXRlc3QgLW0gIm5vdCBicm93c2VyImAuIENJCm5ldmVyIGRvZXMuCgpXaGF0'
     'IHRoZSBleHRlcm5hbCBjaGVjayBjYW5ub3Qgc2VlOiBheGUtY29yZSdzIHN0YWNraW5nIG1vZGVsIGRvZXMgbm90IGtub3cKdGhl'
     'IGJyb3dzZXIncyB0b3AgbGF5ZXIsIHNvIGluc2lkZSBhbiBvcGVuIG1vZGFsIGA8ZGlhbG9nPmAgaXQgY2FsbHMgZXZlcnkKbGlu'
     'ZSAib3ZlcmxhcHBlZCIgYW5kIG1lYXN1cmVzIG5vdGhpbmcg4oCUIGEgY29udHJhc3QgdGVzdCB0aGVyZSBwYXNzZXMgd2l0aG91'
     'dApsb29raW5nLiBUaGUgYGF4ZWAgZml4dHVyZSB0aGVyZWZvcmUgc2hvd3Mgb3BlbiBkaWFsb2dzIGFnYWluIG5vbi1tb2RhbGx5'
     'CmJlZm9yZSBlYWNoIHJ1biAoc2FtZSBtYXJrdXAsIHNhbWUgcGl4ZWxzKSwgYW5kCmB0ZXN0X2F4ZV9jYW5fc3RpbGxfbWVhc3Vy'
     'ZV9jb250cmFzdF9pbnNpZGVfYW5fb3Blbl9zaGVldGAgZmFpbHMgaWYgYXhlIGdvZXMKYmxpbmQgYWdhaW4uIEZvdW5kIGluIEJh'
     'dGNoIDIwIHN0ZXAgMmItMiwgd2hlbiB0d28gYXhlIHN0YXRlcyB0dXJuZWQgZ3JlZW4Kd2l0aCBubyBjb2xvdXIgY2hhbmdlZC4K'
     'CiMjIERlY2lzaW9ucwoKLSAqKkQxIOKAlCBicm93c2VyIGhhcm5lc3M6KiogcHl0ZXN0LCBQbGF5d3JpZ2h0IGZvciBQeXRob24s'
     'IHZlbmRvcmVkCiAgYXhlLWNvcmUuIEl0IHN0YXlzIGluIHRoZSBwcm9qZWN0J3MgbGFuZ3VhZ2UsIHVzZXMgYSByZWFsIGJyb3dz'
     'ZXIsIGJyaW5ncyBhbgogIG91dHNpZGUgV0NBRyBvcGluaW9uLCBhbmQgcHJvdmVzIHRoZSBDU1AgaW4gdGhlIHNhbWUgcnVuLiBU'
     'aGUgY29zdCBpcyBvbmUgZGV2CiAgZGVwZW5kZW5jeSBhbmQgYSBDaHJvbWl1bSBkb3dubG9hZCBpbiBDSS4KLSAqKkQyIOKAlCBv'
     'cmRlcjoqKiB0aGUgQ3JpdGljYWwgZGF0YS1sb3NzIGFuZCBkZWFkLWJ1dHRvbiBmaW5kaW5ncyBjb21lIGZpcnN0CiAgKEExMVkt'
     'MDIpLCBpbiBvbmUgY29tbWl0LgotICoqRDMg4oCUIHRhcmdldCBzaXplOioqIDQ0w5c0NCBweCBmb3IgcHJpbWFyeSBjb250cm9s'
     'cy4gVGhhdCBpcyBXQ0FHIDIuNS41CiAgKEFBQSkgYW5kIHRoZSBhZ2VudC1za2lsbHMgY2hlY2tsaXN0LiBUaGUgQUEgZmxvb3Is'
     'IDI0w5cyNCBweCAoV0NBRyAyLjIKICAyLjUuOCksIGlzIGFscmVhZHkgbWV0IGV2ZXJ5d2hlcmUgbWVhc3VyZWQuIERlbGl2ZXJl'
     'ZCBhcyBhbiBpbnZpc2libGUgdG91Y2gKICBhcmVhIChhbiBgOjphZnRlcmAgcGFzdCB0aGUgdmlzaWJsZSBlZGdlKSwgc28gbm90'
     'aGluZyBncm93czsgdGhlIHRlc3QgbWVhc3VyZXMKICB3aGF0IGEgZmluZ2VyIG1lZXRzIHdpdGggYGVsZW1lbnRGcm9tUG9pbnRg'
     'LCBub3QgdGhlIHBhaW50ZWQgYm94LgotICoqRDQg4oCUIFRlbGVncmFtIHRoZW1lczoqKiB0aGUgYXBwIGNvcnJlY3RzIGEgaGlu'
     'dCBvciBzdWJ0aXRsZSBjb2xvdXIgdGhhdAogIGZhbGxzIGJlbG93IDQuNToxIGFnYWluc3QgdGhlIHRoZW1lIGJhY2tncm91bmQs'
     'IGluc3RlYWQgb2YgcGFzc2luZyBpdAogIHRocm91Z2guIFRoZSB1c2VyJ3MgdGhlbWUgaXMgbm90IHRoZSBhcHAncyB0byBjaG9v'
     'c2UsIGJ1dCByZWFkYWJsZSB0ZXh0IGlzLgoKLSAqKkQ1IOKAlCBkaWFsb2dzOioqIGV2ZXJ5IG1vZGFsIGlzIGEgbmF0aXZlIGA8'
     'ZGlhbG9nPmAgb3BlbmVkIHRocm91Z2gKICBgc3RhdGljL21vZGFsLmpzYDogb25lIHN0YWNrLCBzbyBFc2NhcGUgYW5kIFRlbGVn'
     'cmFtJ3MgYmFjayBidXR0b24gY2xvc2Ugb25seQogIHRoZSB0b3AgZGlhbG9nLCBUYWIgd3JhcHMgaW5zaWRlIGl0LCBhbmQgZm9j'
     'dXMgcmV0dXJucyB0byBpdHMgb3BlbmVyLiBJdCBpcwogIG1pZ3JhdGVkIGluIHRocmVlIHNsaWNlcyAoMmEsIDJiLCAyYyksIGVh'
     'Y2ggcHJvdmVuIG9uIGl0cyBvd24uCi0gKipENiDigJQgaW5pdGlhbCBmb2N1czoqKiBhIGRpYWxvZyBzdGFydHMgYXQgaXRzIHRp'
     'dGxlOyB0aGUgY29uZmlybSBzdGFydHMgYXQKICBDYW5jZWwuIEEgc2NyZWVuIHJlYWRlciBhbm5vdW5jZXMgd2hlcmUgdGhlIHVz'
     'ZXIgaXMsIGFuZCBubyBwaG9uZSBrZXlib2FyZAogIHNwcmluZ3MgdXAgYmVmb3JlIGFueXRoaW5nIHdhcyBjaG9zZW4uCgotICoq'
     'RDcg4oCUIHN0YXR1cyBtZXNzYWdlczoqKiB0aGUgcGFnZSdzIG9uZSBzdGF0dXMgcmVnaW9uIChgI3RvYXN0YCwKICBgcm9sZT0i'
     'c3RhdHVzImApIGZvbGxvd3MgdGhlIGRpYWxvZyBvbiB0b3AsIG1vdmVkIHdoZW4gYSBkaWFsb2cgb3BlbnMgb3IKICBjbG9zZXMg'
     'cmF0aGVyIHRoYW4gd2hlbiBhIG1lc3NhZ2UgaXMgd3JpdHRlbi4gTWVhc3VyZWQgaW4gQ2hyb21pdW06IG91dHNpZGUKICBhbiBv'
     'cGVuIG1vZGFsIGRpYWxvZyB0aGUgcmVnaW9uIGlzIGRyb3BwZWQgZnJvbSB0aGUgYWNjZXNzaWJpbGl0eSB0cmVlLgoKLSAqKkQ4'
     'IOKAlCBldmVudCBjYXJkczoqKiBlYWNoIGNhcmQgc3RheXMgb25lIGJ1dHRvbiwgbmFtZWQgYnkgaXRzIHRpdGxlIGFuZAogIGRl'
     'c2NyaWJlZCBieSBpdHMgb3duIGJhZGdlcywgZGF0ZXMgYW5kIHN0YXR1cy4gVGhlIGFsdGVybmF0aXZlLCBhIGhlYWRpbmcKICB3'
     'aXRoIGEgYnV0dG9uIHN0cmV0Y2hlZCBpbnNpZGUgaXQsIGNoYW5nZWQgdGhlIGtleWJvYXJkIGZvY3VzIHJpbmcgKDUsNTI3CiAg'
     'cGl4ZWxzIG1lYXN1cmVkKTsgdGhpcyBvbmUgY2hhbmdlZCBub3RoaW5nLgotICoqRDkg4oCUIGxpc3Qgc3RhdHVzOioqIGZpbHRl'
     'cnMgYW5kIHNlYXJjaGVzIGFubm91bmNlIGhvdyBtYW55IGV2ZW50cyB0aGV5CiAgbGVhdmUsIGluIGEgc2hvcnQgc3RhdHVzIG9m'
     'IGl0cyBvd247IGxvYWRpbmcgYW5kIHJlbG9hZGluZyBzdGF5IHF1aWV0LgoKLSAqKkQxMCDigJQgdmFsaWRhdGlvbiBlcnJvcnM6'
     'Kiogc2hvd24gdW5kZXIgdGhlaXIgZmllbGQsIHZpc2libGUsIGFuZCBvbmx5IHdoaWxlCiAgdGhlIGZpZWxkIGlzIGludmFsaWQ7'
     'IHRoZXkgcmVwbGFjZSB0aGUgdG9hc3QgZm9yIHRoZXNlIHRocmVlIGNoZWNrcywgc28gYQogIHNjcmVlbiByZWFkZXIgZG9lcyBu'
     'b3QgaGVhciB0aGUgc2FtZSBlcnJvciB0d2ljZS4KCiMjIEV4Y2VwdGlvbnMKCnwgSUQgfCBSdWxlIHwgUGF0aCB8IFJlYXNvbiB8'
     'IE93bmVyIHwgRXhwaXJlcyB8CnwtLS0tfC0tLS0tLXwtLS0tLS18LS0tLS0tLS18LS0tLS0tLXwtLS0tLS0tLS18Cnwg4oCUIHwg'
     'bm9uZSB8IHwgfCB8IHwK'),
    ('scripts/check_known_failures.py', None, 'ce69003269ccceb4d02655cd9e1acbbc1df93a786402ea09c4f226d6e0d4557d',
     'IiIiUnVucyB0aGUgdGVzdCBzdWl0ZSBhbmQgcGFzc2VzIG9ubHkgaWYgZXhhY3RseSB0aGUga25vd24gZmFpbHVyZXMgZmFpbC4K'
     'CkEgYmF0Y2ggbWF5IGNhcnJ5IGZhaWxpbmcgdGVzdHMgb24gcHVycG9zZSB1bnRpbCB0aGV5IGFyZSBmaXhlZDsgdGhleSBhcmUK'
     'bGlzdGVkIGluIHRlc3RzL2tub3duX2ZhaWx1cmVzLnR4dC4gT24gZml4LyoqIGJyYW5jaGVzIENJIHJ1bnMgdGhpcyBpbnN0ZWFk'
     'IG9mCnBsYWluIHB5dGVzdCwgc28gYSByZWQgam9iIGFsd2F5cyBtZWFucyBzb21ldGhpbmcgbmV3OiBhIHRlc3QgdGhhdCBzaG91'
     'bGQgcGFzcwpmYWlsZWQsIG9yIGEga25vd24gZmFpbHVyZSBzdGFydGVkIHRvIHBhc3MgYW5kIGl0cyBsaW5lIGhhcyB0byBnby4g'
     'bWFpbiBhbmQKcHVsbCByZXF1ZXN0cyBpbnRvIG1haW4gcnVuIHBsYWluIHB5dGVzdCwgd2hlcmUgbm90aGluZyBtYXkgZmFpbC4g'
     'VGhlIHNhbWUKc2NyaXB0IGlzIHRoZSBnYXRlIGJlZm9yZSBldmVyeSBjb21taXQuCgogICAgcHl0aG9uIHNjcmlwdHMvY2hlY2tf'
     'a25vd25fZmFpbHVyZXMucHkgW2V4dHJhIHB5dGVzdCBhcmd1bWVudHNdCiIiIgpmcm9tIF9fZnV0dXJlX18gaW1wb3J0IGFubm90'
     'YXRpb25zCgppbXBvcnQgcmUKaW1wb3J0IHN1YnByb2Nlc3MKaW1wb3J0IHN5cwpmcm9tIHBhdGhsaWIgaW1wb3J0IFBhdGgKClJP'
     'T1QgPSBQYXRoKF9fZmlsZV9fKS5yZXNvbHZlKCkucGFyZW50c1sxXQpLTk9XTiA9IFJPT1QgLyAidGVzdHMiIC8gImtub3duX2Zh'
     'aWx1cmVzLnR4dCIKU1VNTUFSWSA9IHJlLmNvbXBpbGUociJeKEZBSUxFRHxFUlJPUikgKFxTLio/KSg/OiAtIC4qKT8kIikKCgpk'
     'ZWYgcmVhZF9rbm93bihwYXRoOiBQYXRoID0gS05PV04pIC0+IHNldFtzdHJdOgogICAgIiIiTm9kZSBpZHMgaW4gdGhlIGxpc3Q7'
     'IGJsYW5rIGxpbmVzIGFuZCAjIGNvbW1lbnRzIGFyZSBpZ25vcmVkLiIiIgogICAgaWYgbm90IHBhdGguZXhpc3RzKCk6CiAgICAg'
     'ICAgcmV0dXJuIHNldCgpCiAgICBsaW5lcyA9IChsaW5lLnN0cmlwKCkgZm9yIGxpbmUgaW4gcGF0aC5yZWFkX3RleHQoZW5jb2Rp'
     'bmc9InV0Zi04Iikuc3BsaXRsaW5lcygpKQogICAgcmV0dXJuIHtsaW5lIGZvciBsaW5lIGluIGxpbmVzIGlmIGxpbmUgYW5kIG5v'
     'dCBsaW5lLnN0YXJ0c3dpdGgoIiMiKX0KCgpkZWYgcGFyc2VfZmFpbHVyZXMob3V0cHV0OiBzdHIpIC0+IHR1cGxlW3NldFtzdHJd'
     'LCBzZXRbc3RyXV06CiAgICAiIiJOb2RlIGlkcyBmcm9tIHB5dGVzdCdzIHNob3J0IHN1bW1hcnkgKC1yZkUpLCBhcyAoZmFpbGVk'
     'LCBlcnJvcnMpLiIiIgogICAgZmFpbGVkOiBzZXRbc3RyXSA9IHNldCgpCiAgICBlcnJvcnM6IHNldFtzdHJdID0gc2V0KCkKICAg'
     'IGZvciBsaW5lIGluIG91dHB1dC5zcGxpdGxpbmVzKCk6CiAgICAgICAgbWF0Y2ggPSBTVU1NQVJZLm1hdGNoKGxpbmUucnN0cmlw'
     'KCkpCiAgICAgICAgaWYgbWF0Y2g6CiAgICAgICAgICAgIChmYWlsZWQgaWYgbWF0Y2guZ3JvdXAoMSkgPT0gIkZBSUxFRCIgZWxz'
     'ZSBlcnJvcnMpLmFkZChtYXRjaC5ncm91cCgyKSkKICAgIHJldHVybiBmYWlsZWQsIGVycm9ycwoKCmRlZiBjb21wYXJlKGtub3du'
     'OiBzZXRbc3RyXSwgZmFpbGVkOiBzZXRbc3RyXSwgZXJyb3JzOiBzZXRbc3RyXSkgLT4gbGlzdFtzdHJdOgogICAgIiIiRXZlcnkg'
     'd2F5IHRoZSBydW4gZGlmZmVycyBmcm9tIHRoZSBsaXN0LCBvbmUgbGluZSBlYWNoLiIiIgogICAgcHJvYmxlbXMgPSBbZiJlcnJv'
     'ciAoc2V0dXAgb3IgdGVhcmRvd24gYnJva2UpLCBuZXZlciBhIGtub3duIGZhaWx1cmU6IHtub2RlfSIgZm9yIG5vZGUgaW4gc29y'
     'dGVkKGVycm9ycyldCiAgICBwcm9ibGVtcyArPSBbZiJmYWlsZWQsIGJ1dCBpcyBub3QgYSBrbm93biBmYWlsdXJlOiB7bm9kZX0i'
     'IGZvciBub2RlIGluIHNvcnRlZChmYWlsZWQgLSBrbm93bildCiAgICBwcm9ibGVtcyArPSBbZiJrbm93biBmYWlsdXJlIHRoYXQg'
     'ZGlkIG5vdCBmYWlsIChmaXhlZD8gdGhlbiByZW1vdmUgaXRzIGxpbmUpOiB7bm9kZX0iCiAgICAgICAgICAgICAgICAgZm9yIG5v'
     'ZGUgaW4gc29ydGVkKGtub3duIC0gZmFpbGVkKV0KICAgIHJldHVybiBwcm9ibGVtcwoKCmRlZiBydW5fcHl0ZXN0KGV4dHJhOiBs'
     'aXN0W3N0cl0pIC0+IHR1cGxlW2ludCwgc3RyXToKICAgICIiIlJ1bnMgcHl0ZXN0LCBzdHJlYW1zIGl0cyBvdXRwdXQgYXMgaXQg'
     'Y29tZXMsIGFuZCByZXR1cm5zIGl0LiIiIgogICAgY29tbWFuZCA9IFtzeXMuZXhlY3V0YWJsZSwgIi1tIiwgInB5dGVzdCIsICIt'
     'cCIsICJubzpjYWNoZXByb3ZpZGVyIiwgIi1yZkUiLCAqZXh0cmFdCiAgICBwcm9jZXNzID0gc3VicHJvY2Vzcy5Qb3Blbihjb21t'
     'YW5kLCBjd2Q9Uk9PVCwgc3Rkb3V0PXN1YnByb2Nlc3MuUElQRSwgc3RkZXJyPXN1YnByb2Nlc3MuU1RET1VULAogICAgICAgICAg'
     'ICAgICAgICAgICAgICAgICAgICAgdGV4dD1UcnVlLCBlbmNvZGluZz0idXRmLTgiLCBlcnJvcnM9InJlcGxhY2UiLCBidWZzaXpl'
     'PTEpCiAgICBsaW5lcyA9IFtdCiAgICBmb3IgbGluZSBpbiBwcm9jZXNzLnN0ZG91dDoKICAgICAgICBzeXMuc3Rkb3V0LndyaXRl'
     'KGxpbmUpCiAgICAgICAgbGluZXMuYXBwZW5kKGxpbmUpCiAgICByZXR1cm4gcHJvY2Vzcy53YWl0KCksICIiLmpvaW4obGluZXMp'
     'CgoKZGVmIG1haW4oYXJndjogbGlzdFtzdHJdKSAtPiBpbnQ6CiAgICBpZiBoYXNhdHRyKHN5cy5zdGRvdXQsICJyZWNvbmZpZ3Vy'
     'ZSIpOgogICAgICAgIHN5cy5zdGRvdXQucmVjb25maWd1cmUoZXJyb3JzPSJyZXBsYWNlIikKICAgIGtub3duID0gcmVhZF9rbm93'
     'bigpCiAgICBjb2RlLCBvdXRwdXQgPSBydW5fcHl0ZXN0KGFyZ3YpCiAgICBwcmludCgpCiAgICBpZiBjb2RlIG5vdCBpbiAoMCwg'
     'MSk6CiAgICAgICAgcHJpbnQoZiJLTk9XTi1GQUlMVVJFUyBHQVRFOiBGQUlMLiBweXRlc3Qgc3RvcHBlZCBhYm5vcm1hbGx5IChl'
     'eGl0IGNvZGUge2NvZGV9KS4iKQogICAgICAgIHJldHVybiBjb2RlCiAgICBmYWlsZWQsIGVycm9ycyA9IHBhcnNlX2ZhaWx1cmVz'
     'KG91dHB1dCkKICAgIHByb2JsZW1zID0gY29tcGFyZShrbm93biwgZmFpbGVkLCBlcnJvcnMpCiAgICBpZiBwcm9ibGVtczoKICAg'
     'ICAgICBwcmludCgiS05PV04tRkFJTFVSRVMgR0FURTogRkFJTCIpCiAgICAgICAgZm9yIHByb2JsZW0gaW4gcHJvYmxlbXM6CiAg'
     'ICAgICAgICAgIHByaW50KGYiICAtIHtwcm9ibGVtfSIpCiAgICAgICAgcmV0dXJuIDEKICAgIHByaW50KGYiS05PV04tRkFJTFVS'
     'RVMgR0FURTogT0suIEV4YWN0bHkgdGhlIHtsZW4oa25vd24pfSBrbm93biBmYWlsdXJlKHMpIGZhaWxlZDsgIgogICAgICAgICAg'
     'ImV2ZXJ5dGhpbmcgZWxzZSBwYXNzZWQuIikKICAgIHJldHVybiAwCgoKaWYgX19uYW1lX18gPT0gIl9fbWFpbl9fIjoKICAgIHN5'
     'cy5leGl0KG1haW4oc3lzLmFyZ3ZbMTpdKSkK'),
    ('tests/known_failures.txt', None, '68d625a22227850fbd590df1f270f173e44a73dde6c8416b7a6fff7038e5e80b',
     'IyBUZXN0cyB0aGF0IGZhaWwgb24gcHVycG9zZSBvbiB0aGlzIGJyYW5jaCwgYW5kIG5vdGhpbmcgZWxzZSBtYXkuCiMKIyBFYWNo'
     'IGlzIGEgZmluZGluZyB3aXRoIGEgcmVxdWlyZW1lbnQgaW4gZG9jcy9hMTF5L1JFUVVJUkVNRU5UUy5tZCwgcmVkIHVudGlsCiMg'
     'aXRzIGZpeCBsYW5kcy4gT24gZml4LyoqIGJyYW5jaGVzIENJIHJ1bnMgc2NyaXB0cy9jaGVja19rbm93bl9mYWlsdXJlcy5weSwK'
     'IyB3aGljaCBmYWlscyBvbiBhbnkgb3RoZXIgZmFpbHVyZSBhbmQgb24gYSBsaXN0ZWQgdGVzdCB0aGF0IHBhc3NlczsgZWFjaCBm'
     'aXgKIyByZW1vdmVzIGl0cyBsaW5lcy4gbWFpbiBhbmQgcHVsbCByZXF1ZXN0cyBpbnRvIG1haW4gcnVuIHRoZSBmdWxsIHN1aXRl'
     'LCB3aGVyZQojIHRoaXMgbGlzdCBoYXMgdG8gYmUgZW1wdHkuIFNlZSBDT05TVFJBSU5UUy5tZC4KCiMgQTExWS0wNTogY29udHJh'
     'c3QsIGFuZCBheGUgd2l0aCBub3RoaW5nIHNlcmlvdXMgb3IgY3JpdGljYWwKdGVzdHMvYnJvd3Nlci90ZXN0X2ExMXlfYnJvd3Nl'
     'ci5weTo6dGVzdF9heGVfZmluZHNfbm9fc2VyaW91c19vcl9jcml0aWNhbF92aW9sYXRpb25zW2NvbXBvc2VyXQp0ZXN0cy9icm93'
     'c2VyL3Rlc3RfYTExeV9icm93c2VyLnB5Ojp0ZXN0X2F4ZV9maW5kc19ub19zZXJpb3VzX29yX2NyaXRpY2FsX3Zpb2xhdGlvbnNb'
     'ZGV0YWlsXQp0ZXN0cy9icm93c2VyL3Rlc3RfYTExeV9icm93c2VyLnB5Ojp0ZXN0X2F4ZV9maW5kc19ub19zZXJpb3VzX29yX2Ny'
     'aXRpY2FsX3Zpb2xhdGlvbnNbbGlzdC1kYXJrXQp0ZXN0cy9icm93c2VyL3Rlc3RfYTExeV9icm93c2VyLnB5Ojp0ZXN0X2F4ZV9m'
     'aW5kc19ub19zZXJpb3VzX29yX2NyaXRpY2FsX3Zpb2xhdGlvbnNbbGlzdC1saWdodF0KdGVzdHMvYnJvd3Nlci90ZXN0X2ExMXlf'
     'YnJvd3Nlci5weTo6dGVzdF9heGVfZmluZHNfbm9fc2VyaW91c19vcl9jcml0aWNhbF92aW9sYXRpb25zW21vbnRoXQp0ZXN0cy9i'
     'cm93c2VyL3Rlc3RfYTExeV9icm93c2VyLnB5Ojp0ZXN0X211dGVkX3RleHRfc3RheXNfcmVhZGFibGVfdW5kZXJfYV9sb3dfY29u'
     'dHJhc3RfdGVsZWdyYW1fdGhlbWUKdGVzdHMvdGVzdF9hMTF5X2ZpbmRpbmdzLnB5Ojp0ZXN0X3RoZV9mYWxsYmFja19wYWxldHRl'
     'X21lZXRzX3djYWdfYWFfdGV4dF9jb250cmFzdAoKIyBBMTFZLTA3OiB0aGUgcHVibGljIGNvdW50ZG93biBwYWdlCnRlc3RzL3Rl'
     'c3RfYTExeV9maW5kaW5ncy5weTo6dGVzdF90aGVfY291bnRkb3duX2ltYWdlX2FsdF90ZXh0X2NhcnJpZXNfdGhlX2NvdW50ZG93'
     'bgp0ZXN0cy90ZXN0X2ExMXlfZmluZGluZ3MucHk6OnRlc3RfdGhlX2NvdW50ZG93bl9wYWdlX2hhc19hX21haW5fbGFuZG1hcmtf'
     'YW5kX2FfaGVhZGluZwoKIyBBMTFZLTA5OiBhIGNsZWFuIGNvbnNvbGUgdW5kZXIgdGhlIHByb2R1Y3Rpb24gQ1NQCnRlc3RzL2Jy'
     'b3dzZXIvdGVzdF9hMTF5X2Jyb3dzZXIucHk6OnRlc3Rfb3BlbmluZ190aGVfYXBwX291dHNpZGVfdGVsZWdyYW1fcmFpc2VzX25v'
     'X2NzcF92aW9sYXRpb24K'),
    ('tests/test_known_failures.py', None, 'd3a7741272c6c5b58f5394121dbbd1701c9e56b871bd54bff811c1e1abce1ce8',
     'IiIiVGhlIGdhdGUgZm9yIGEgYmF0Y2ggdGhhdCBjYXJyaWVzIGZhaWxpbmcgdGVzdHMgb24gcHVycG9zZS4KCnNjcmlwdHMvY2hl'
     'Y2tfa25vd25fZmFpbHVyZXMucHkgcGFzc2VzIG9ubHkgaWYgZXhhY3RseSB0aGUgdGVzdHMgbGlzdGVkIGluCnRlc3RzL2tub3du'
     'X2ZhaWx1cmVzLnR4dCBmYWlsLiBDSSBydW5zIGl0IG9uIGZpeC8qKiBicmFuY2hlcywgYW5kIGl0IGlzIHRoZQpnYXRlIGJlZm9y'
     'ZSBldmVyeSBjb21taXQuIFRoZXNlIHRlc3RzIHBpbiBob3cgaXQgcmVhZHMgcHl0ZXN0J3Mgc3VtbWFyeSwgaG93Cml0IGNvbXBh'
     'cmVzLCBhbmQgdGhhdCBDSSB1c2VzIGl0IG9ubHkgd2hlcmUga25vd24gZmFpbHVyZXMgYXJlIGFsbG93ZWQuCiIiIgpmcm9tIF9f'
     'ZnV0dXJlX18gaW1wb3J0IGFubm90YXRpb25zCgppbXBvcnQgaW1wb3J0bGliLnV0aWwKZnJvbSBwYXRobGliIGltcG9ydCBQYXRo'
     'CgppbXBvcnQgcHl0ZXN0CgpST09UID0gUGF0aChfX2ZpbGVfXykucmVzb2x2ZSgpLnBhcmVudHNbMV0KU0NSSVBUID0gUk9PVCAv'
     'ICJzY3JpcHRzIiAvICJjaGVja19rbm93bl9mYWlsdXJlcy5weSIKTElTVCA9IFJPT1QgLyAidGVzdHMiIC8gImtub3duX2ZhaWx1'
     'cmVzLnR4dCIKQ0kgPSBST09UIC8gIi5naXRodWIiIC8gIndvcmtmbG93cyIgLyAiY2kueW1sIgoKQSA9ICJ0ZXN0cy90ZXN0X3gu'
     'cHk6OnRlc3RfYSIKQiA9ICJ0ZXN0cy9icm93c2VyL3Rlc3RfeS5weTo6dGVzdF9iW2xpc3QtbGlnaHRdIgpDID0gInRlc3RzL3Rl'
     'c3RfeC5weTo6dGVzdF9jIgoKCmRlZiBsb2FkX2dhdGUoKToKICAgIGlmIG5vdCBTQ1JJUFQuZXhpc3RzKCk6CiAgICAgICAgcHl0'
     'ZXN0LmZhaWwoInNjcmlwdHMvY2hlY2tfa25vd25fZmFpbHVyZXMucHkgZG9lcyBub3QgZXhpc3QgeWV0IikKICAgIHNwZWMgPSBp'
     'bXBvcnRsaWIudXRpbC5zcGVjX2Zyb21fZmlsZV9sb2NhdGlvbigiY2hlY2tfa25vd25fZmFpbHVyZXMiLCBTQ1JJUFQpCiAgICBt'
     'b2R1bGUgPSBpbXBvcnRsaWIudXRpbC5tb2R1bGVfZnJvbV9zcGVjKHNwZWMpCiAgICBzcGVjLmxvYWRlci5leGVjX21vZHVsZSht'
     'b2R1bGUpCiAgICByZXR1cm4gbW9kdWxlCgoKZGVmIHRlc3RfdGhlX3N1bW1hcnlfbGluZXNfZ2l2ZV90aGVfbm9kZV9pZHMoKToK'
     'ICAgIGdhdGUgPSBsb2FkX2dhdGUoKQogICAgb3V0cHV0ID0gIlxuIi5qb2luKFsKICAgICAgICBmIkZBSUxFRCB7Qn0gLSBBc3Nl'
     'cnRpb25FcnJvcjogYXhlIGZvdW5kIDMgdmlvbGF0aW9ucyIsCiAgICAgICAgZiJGQUlMRUQge0F9IiwKICAgICAgICBmIkVSUk9S'
     'IHtDfSAtIEZhaWxlZDogdGhlIHBhZ2UgcmFpc2VkIGEgQ1NQIHZpb2xhdGlvbiIsCiAgICAgICAgIjEwIGZhaWxlZCwgMzY4IHBh'
     'c3NlZCwgMSBlcnJvciBpbiA4MC4wMXMiLAogICAgXSkKICAgIGFzc2VydCBnYXRlLnBhcnNlX2ZhaWx1cmVzKG91dHB1dCkgPT0g'
     'KHtBLCBCfSwge0N9KQoKCmRlZiB0ZXN0X2V4YWN0bHlfdGhlX2tub3duX2ZhaWx1cmVzX2lzX2FfcGFzcygpOgogICAgZ2F0ZSA9'
     'IGxvYWRfZ2F0ZSgpCiAgICBhc3NlcnQgZ2F0ZS5jb21wYXJlKGtub3duPXtBLCBCfSwgZmFpbGVkPXtBLCBCfSwgZXJyb3JzPXNl'
     'dCgpKSA9PSBbXQoKCmRlZiB0ZXN0X2FfbmV3X2ZhaWx1cmVfaXNfcmVwb3J0ZWRfYnlfbmFtZSgpOgogICAgZ2F0ZSA9IGxvYWRf'
     'Z2F0ZSgpCiAgICBwcm9ibGVtcyA9IGdhdGUuY29tcGFyZShrbm93bj17QX0sIGZhaWxlZD17QSwgQ30sIGVycm9ycz1zZXQoKSkK'
     'ICAgIGFzc2VydCBsZW4ocHJvYmxlbXMpID09IDEgYW5kIEMgaW4gcHJvYmxlbXNbMF0KCgpkZWYgdGVzdF9hX2tub3duX2ZhaWx1'
     'cmVfdGhhdF9wYXNzZXNfaXNfcmVwb3J0ZWRfc29faXRzX2xpbmVfZ29lcygpOgogICAgZ2F0ZSA9IGxvYWRfZ2F0ZSgpCiAgICBw'
     'cm9ibGVtcyA9IGdhdGUuY29tcGFyZShrbm93bj17QSwgQn0sIGZhaWxlZD17QX0sIGVycm9ycz1zZXQoKSkKICAgIGFzc2VydCBs'
     'ZW4ocHJvYmxlbXMpID09IDEgYW5kIEIgaW4gcHJvYmxlbXNbMF0KCgpkZWYgdGVzdF9hbl9lcnJvcl9pc19uZXZlcl9hX2tub3du'
     'X2ZhaWx1cmUoKToKICAgIGdhdGUgPSBsb2FkX2dhdGUoKQogICAgIiIiQW4gZXJyb3IgbWVhbnMgc2V0dXAgb3IgdGVhcmRvd24g'
     'YnJva2U6IHNvbWV0aGluZyBlbHNlIHdlbnQgd3JvbmcuIiIiCiAgICBwcm9ibGVtcyA9IGdhdGUuY29tcGFyZShrbm93bj17QX0s'
     'IGZhaWxlZD17QX0sIGVycm9ycz17QX0pCiAgICBhc3NlcnQgcHJvYmxlbXMgYW5kIGFsbChBIGluIHAgZm9yIHAgaW4gcHJvYmxl'
     'bXMpCgoKZGVmIHRlc3RfdGhlX2xpc3RfaWdub3Jlc19jb21tZW50c19hbmRfYmxhbmtfbGluZXModG1wX3BhdGgpOgogICAgZ2F0'
     'ZSA9IGxvYWRfZ2F0ZSgpCiAgICBsaXN0aW5nID0gdG1wX3BhdGggLyAia25vd24udHh0IgogICAgbGlzdGluZy53cml0ZV90ZXh0'
     'KGYiIyB3aHkgdGhlc2UgZmFpbFxuXG57QX1cbiAge0J9ICBcbiMgQTExWS0wN1xuIiwgZW5jb2Rpbmc9InV0Zi04IikKICAgIGFz'
     'c2VydCBnYXRlLnJlYWRfa25vd24obGlzdGluZykgPT0ge0EsIEJ9CgoKZGVmIHRlc3RfZXZlcnlfbGlzdGVkX2ZhaWx1cmVfbmFt'
     'ZXNfYV90ZXN0X3RoYXRfZXhpc3RzKCk6CiAgICAiIiJBIHR5cG8gaW4gdGhlIGxpc3QgbXVzdCBub3QgYmVjb21lIGEgc2lsZW50'
     'IHBhc3MuIiIiCiAgICBhc3NlcnQgTElTVC5leGlzdHMoKSwgInRlc3RzL2tub3duX2ZhaWx1cmVzLnR4dCBkb2VzIG5vdCBleGlz'
     'dCB5ZXQiCiAgICBlbnRyaWVzID0gW2xpbmUuc3RyaXAoKSBmb3IgbGluZSBpbiBMSVNULnJlYWRfdGV4dChlbmNvZGluZz0idXRm'
     'LTgiKS5zcGxpdGxpbmVzKCldCiAgICBlbnRyaWVzID0gW2UgZm9yIGUgaW4gZW50cmllcyBpZiBlIGFuZCBub3QgZS5zdGFydHN3'
     'aXRoKCIjIildCiAgICBicm9rZW4gPSBbZSBmb3IgZSBpbiBlbnRyaWVzIGlmICI6OiIgbm90IGluIGUgb3Igbm90IChST09UIC8g'
     'ZS5zcGxpdCgiOjoiKVswXSkuZXhpc3RzKCldCiAgICBhc3NlcnQgZW50cmllcyBhbmQgbm90IGJyb2tlbiwgZiJlbnRyaWVzIHRo'
     'YXQgbmFtZSBubyB0ZXN0IGZpbGU6IHticm9rZW59IgoKCmRlZiB0ZXN0X2NpX3J1bnNfdGhlX2Z1bGxfc3VpdGVfb25fbWFpbl9h'
     'bmRfdGhlX2dhdGVfZWxzZXdoZXJlKCk6CiAgICAiIiJtYWluIGFuZCBwdWxsIHJlcXVlc3RzIGludG8gbWFpbiBhbGxvdyBubyBm'
     'YWlsdXJlIGF0IGFsbDsgb25seSBmaXgvKioKICAgIGJyYW5jaGVzIG1heSBjYXJyeSB0aGUgbGlzdGVkIG9uZXMuIiIiCiAgICBz'
     'dGVwcyA9IENJLnJlYWRfdGV4dChlbmNvZGluZz0idXRmLTgiKS5zcGxpdCgiLSBuYW1lOiIpCiAgICBmdWxsID0gW3MgZm9yIHMg'
     'aW4gc3RlcHMgaWYgInJ1bjogcHl0ZXN0IC12IiBpbiBzXQogICAgZ2F0ZWQgPSBbcyBmb3IgcyBpbiBzdGVwcyBpZiAicHl0aG9u'
     'IHNjcmlwdHMvY2hlY2tfa25vd25fZmFpbHVyZXMucHkiIGluIHNdCgogICAgc3RyaWN0ID0gImlmOiBnaXRodWIucmVmID09ICdy'
     'ZWZzL2hlYWRzL21haW4nIHx8IGdpdGh1Yi5ldmVudF9uYW1lID09ICdwdWxsX3JlcXVlc3QnIgogICAgYmF0Y2ggPSAiaWY6IGdp'
     'dGh1Yi5yZWYgIT0gJ3JlZnMvaGVhZHMvbWFpbicgJiYgZ2l0aHViLmV2ZW50X25hbWUgIT0gJ3B1bGxfcmVxdWVzdCciCiAgICBh'
     'c3NlcnQgbGVuKGZ1bGwpID09IDEgYW5kIHN0cmljdCBpbiBmdWxsWzBdCiAgICBhc3NlcnQgbGVuKGdhdGVkKSA9PSAxIGFuZCBi'
     'YXRjaCBpbiBnYXRlZFswXQo='),
]


def current(path: str) -> bytes | None:
    target = ROOT / path
    return target.read_bytes().replace(b"\r\n", b"\n") if target.exists() else None


def main() -> int:
    if not (ROOT / "app" / "main.py").exists() or not (ROOT / "tests" / "browser" / "conftest.py").exists():
        print("Run this from the time_manager_pro repository root, on fix/batch20-a11y.")
        return 1
    plan, problems = [], []
    for path, before, after, content in FILES:
        data = current(path)
        digest = hashlib.sha256(data).hexdigest() if data is not None else None
        if digest == after:
            plan.append((path, None))
        elif digest == before:
            plan.append((path, base64.b64decode(content)))
        elif data is None:
            problems.append(f"{path}: missing (apply the previous step first)")
        else:
            problems.append(f"{path}: not the version this step was built on (apply the previous step first)")
    if problems:
        print("Nothing was changed:")
        print("\n".join(f"  - {p}" for p in problems))
        return 1
    for step, (path, data) in enumerate(plan, 1):
        if data is None:
            print(f"Step {step:2d} already applied: {path}")
            continue
        target = ROOT / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        print(f"Step {step:2d} OK: {path}")
    print("\nDone. Expected now: pytest -> 10 failed, 376 passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
