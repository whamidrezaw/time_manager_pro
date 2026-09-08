cd /workspaces/time_manager_pro
git pull
[ -d .venv ] || { pip install -q uv && uv venv --python 3.12 .venv; }
source .venv/bin/activate
ls -la apply_batch13a1.py
python apply_batch13a1.py --check
python apply_batch13a1.py
ruff check . && pytest
rm -f apply_batch13a1.py
git add -A
git commit -m "Batch 13a-1: incremental rendering, section headers, date picker fix"
git push
sleep 25
gh run list --limit 2