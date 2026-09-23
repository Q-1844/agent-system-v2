#!/bin/bash
cd "C:\Users\曹文淇\Desktop\ai工作流\animation-storyboard"
for spec in "s01_c01 1" "s02_c01 2" "s03_c01 2" "s04_c01 2" "s05_c01 2" "s06_c01 2" "s07_c01 2" "s08_c01 2"; do
  set -- $spec
  echo "=== BATCH $1 x$2 $(date +%H:%M:%S) ==="
  "D:/Delta Force/python.exe" _34_fable_pilot/_34_video.py "$1" "$2"
done
echo "=== BATCH ALL DONE $(date +%H:%M:%S) ==="
