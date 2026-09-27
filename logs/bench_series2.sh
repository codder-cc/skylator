#!/bin/bash
# Серия 2: вклад каждой части контекста, схема «без одной части». База — base_a/base_b.
cd "H:/Nolvus/Translator"
ALL="speaker,vocab,talk,style,entities,summary,terms,tm"
run() { PYTHONIOENCODING=utf-8 ./venv/Scripts/python.exe scripts/bench.py run "$@"; }
without() { echo "$ALL" | tr ',' '\n' | grep -vx "$1" | paste -sd, -; }
for S in D H; do
  run --name ctx_none_$S --set $S --context-parts ""
  for P in speaker vocab talk style entities summary terms tm; do
    run --name ctx_no_${P}_$S --set $S --context-parts "$(without $P)"
  done
done
echo "СЕРИЯ 2 ГОТОВА $(date +%T)"
