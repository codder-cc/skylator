#!/bin/bash
# Все оставшиеся опыты — очередью на M5 заранее (мастер будет выключен ночью).
cd "H:/Nolvus/Translator"
q() { PYTHONIOENCODING=utf-8 ./venv/Scripts/python.exe scripts/bench.py run --no-wait "$@"; sleep 3; }
ALL="speaker,vocab,talk,style,entities,summary,terms,tm"
without() { echo "$ALL" | tr ',' '\n' | grep -vx "$1" | paste -sd, -; }
for S in D H; do
  q --name ctx_none_$S --set $S --context-parts ""
  for P in speaker vocab talk style entities summary terms tm; do
    q --name ctx_no_${P}_$S --set $S --context-parts "$(without $P)"
  done
  [ "$S" = D ] && { q --name scene_D --set D --scene; q --name scene_t00_D --set D --scene --params '{"temperature":0.0}'; }
  q --name bs1_$S  --set $S --batch-size 1
  q --name bs4_$S  --set $S --batch-size 4
  q --name bs24_$S --set $S --batch-size 24
  q --name think_$S --set $S --params '{"thinking":true}' --max-tokens 6144
  q --name topk1_$S --set $S --params '{"top_k":1}'
  q --name topk50_$S --set $S --params '{"top_k":50}'
  q --name rp100_$S --set $S --params '{"repetition_penalty":1.0}'
  q --name rp115_$S --set $S --params '{"repetition_penalty":1.15}'
done
echo "ОЧЕРЕДЬ РОЗДАНА $(date +%T)"
