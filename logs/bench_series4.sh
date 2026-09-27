#!/bin/bash
# Серия 4: судья на исправленных параметрах (top_k и штраф теперь доходят до MLX).
cd "H:/Nolvus/Translator"
S="C:/Users/incro/AppData/Local/Temp/claude/H--Nolvus/bb782997-3cef-41c8-89b4-86ec35a6f373/scratchpad"
PYTHONIOENCODING=utf-8 ./venv/Scripts/python.exe scripts/judge_speaker_test.py --worker darwin-int00mac-7PKF2W --variants A,B,B2,B3 > logs/judge_speaker_rerun.log 2>&1
PYTHONIOENCODING=utf-8 ./venv/Scripts/python.exe scripts/judge_layer_test.py --worker darwin-int00mac-7PKF2W --variants plain,gloss > logs/judge_layer_rerun.log 2>&1
echo "СЕРИЯ 4 ГОТОВА $(date +%T)"
