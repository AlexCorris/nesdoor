#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
CC="${CC:-i686-w64-mingw32-gcc}"
echo "===== NESDoor-C Win7/32 v0.10 configurable build ====="
"$CC" --version | head -1
"$CC" -std=gnu99 -O3 -DNDEBUG -D_WIN32_WINNT=0x0601 -march=i686 -mtune=generic \
  -static-libgcc -s -Wall -Wextra \
  -o NESDoor-C.exe src/main.c src/door.c src/core.c src/sixel.c src/audio.c -lws2_32 -lm
echo
file NESDoor-C.exe
echo
echo "===== DLL IMPORTS ====="
i686-w64-mingw32-objdump -p NESDoor-C.exe | grep "DLL Name" || true
echo
echo "SUCCESS: NESDoor-C.exe built."
