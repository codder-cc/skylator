"""
translator.parsing — thin wrappers around file-format I/O.

Modules:
  esp_parser      — ESP/ESM binary: extract_strings(), rewrite()
  bsa_handler     — BSArch subprocess: unpack(), pack()
  swf_handler     — FFDec subprocess: decompile(), compile_texts()
  mcm_handler     — MCM translation .txt: read(), write()
  asset_extractor — DB→file export for MCM, BSA-MCM, SWF
"""
