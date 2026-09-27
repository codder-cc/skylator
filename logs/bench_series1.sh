#!/bin/bash
# Серия 1: шум базы и температура. Порядок фиксирован заранее.
cd "H:/Nolvus/Translator"
run() { PYTHONIOENCODING=utf-8 ./venv/Scripts/python.exe scripts/bench.py run "$@"; }
run --name base_a_D --set D
run --name base_b_D --set D
run --name t00_D --set D --params '{"temperature":0.0}'
run --name t06_D --set D --params '{"temperature":0.6}'
run --name t09_D --set D --params '{"temperature":0.9}'
run --name base_a_H --set H
run --name base_b_H --set H
run --name t00_H --set H --params '{"temperature":0.0}'
echo "СЕРИЯ 1 ГОТОВА $(date +%T)"
