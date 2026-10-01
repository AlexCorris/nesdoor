#!/bin/sh
# Menu Data examples:  nesdoor.sh %Pdoor32          -> game list
#                      nesdoor.sh tetris %Pdoor32   -> straight into roms/tetris.nes
cd "$(dirname "$0")"
if [ -n "$1" ] && [ -f "roms/$1.nes" ]; then
    ROM="roms/$1.nes"; shift
else
    ROM="roms"
fi
echo "$(date) START $ROM args: $*" >> nesdoor.log
./venv/bin/python ./nesdoor.py "$ROM" "$@" --hold 100 2>>nesdoor.log
echo "$(date) END exit=$?" >> nesdoor.log
