#!/bin/bash
# Серия 3: параметры генерации. База — base_a/base_b (батч 12, t=0.3, top_k 20, rp 1.05).
cd "H:/Nolvus/Translator"
run() { PYTHONIOENCODING=utf-8 ./venv/Scripts/python.exe scripts/bench.py run "$@"; }
run --name scene_D --set D --scene
run --name scene_t00_D --set D --scene --params '{"temperature":0.0}'
for S in D H; do
  run --name bs1_$S  --set $S --batch-size 1
  run --name bs4_$S  --set $S --batch-size 4
  run --name bs24_$S --set $S --batch-size 24
  run --name think_$S --set $S --params '{"thinking":true}' --max-tokens 6144
  run --name topk1_$S --set $S --params '{"top_k":1}'
  run --name topk50_$S --set $S --params '{"top_k":50}'
  run --name rp100_$S --set $S --params '{"repetition_penalty":1.0}'
  run --name rp115_$S --set $S --params '{"repetition_penalty":1.15}'
done
echo "СЕРИЯ 3 ГОТОВА $(date +%T)"
