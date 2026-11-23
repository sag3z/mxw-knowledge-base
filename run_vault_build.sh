#!/usr/bin/env bash
# Full vault build; every step resumes (skips finished plans/articles/notes). Log: data/vault_build.log
cd "$(dirname "$0")"
export PYTHONUTF8=1
for c in nft ai_dev tools trading drops_testnets market_news predictions jobs_income community other; do
  test -f data/plans/$c.json || python -u build_vault.py plan $c
done
python -u build_vault.py write all --workers 3
python -u build_vault.py schemes --workers 2
python -u build_vault.py entities --min 4 --workers 3
python -u build_vault.py index
python -u check_vault.py vault
echo ALLDONE
