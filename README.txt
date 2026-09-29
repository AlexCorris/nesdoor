NESDOOR v2.8 - NES games over sixel for BBS callers
==============================================================================
A game list of the .nes files in roms/, the picture drawn with sixel
graphics, real key-down/key-up input and the game's own sound in SyncTERM,
and optional 2-player games between two nodes.

Callers need a sixel-capable terminal. SyncTERM (syncterm.net) is the one
to recommend: it also plays the sound and reports held keys. Other terminals
get a notice with "Try anyway? (Y/N)".

FILES
  nesdoor.py          the door
  retro.py            talks to the emulator core
  fceumm_libretro.so  the NES emulator core (Linux x86-64, see NOTES)
  twoplayer.py        2-player link between nodes
  conn.py             BBS connection (stdio / DOOR32 socket), terminal check
  sixel.py            graphics encoder
  nesdoor.sh          Linux launcher (logs to nesdoor.log)
  nesdoor.bat         Windows launcher
  nesdoor.ini         settings - created on first run
  LICENSE             GNU GPL v2
  roms/               your .nes files (you supply these)
  sessions/           open 2-player games - created automatically
  saves/<player>/     each player's saved games - created automatically


==============================================================================
FRESH LINUX INSTALL (from scratch)
==============================================================================
Examples use a Mystic BBS in /home/mystic/mystic running as user "mystic".
Change the paths and user name to match your system.

1. Install the system packages (Debian / Ubuntu; needs sudo once):

     sudo apt update
     sudo apt install python3 python3-venv unzip

   Python 3.10 or newer is fine (check with: python3 --version).

2. Unpack the door as the BBS user, from the BBS "doors" folder:

     cd /home/mystic/mystic/doors
     unzip -o /home/mystic/nesdoor_v2.zip

   This creates /home/mystic/mystic/doors/nesdoor. Unzip here, NOT inside
   an existing roms/ folder.

3. Give the door its own Python environment and the one library it needs:

     cd /home/mystic/mystic/doors/nesdoor
     python3 -m venv venv
     ./venv/bin/pip install numpy
     chmod +x nesdoor.sh

   (Optional fallback emulator with no sound, only used if the .so file is
   missing:  ./venv/bin/pip install cynes)

4. Add games: copy .nes files straight into roms/ (not into subfolders).

     mkdir -p roms
     cp /path/to/your/games/*.nes roms/
     ls roms

   File names become titles: Super_Mario_Bros.nes -> "Super Mario Bros".

5. Check that the emulator loads (replace the file name with one of yours):

     ./venv/bin/python -c "import retro; c = retro.Core( \
       'fceumm_libretro.so', 'roms/Super_Mario_Bros.nes', '.'); \
       print('OK', round(c.fps, 1), 'fps')"

   You should see:  OK 60.1 fps
   An error mentioning GLIBC or "cannot open shared object" means the
   included core doesn't suit your Linux - build your own (see NOTES).

6. Add a menu command in Mystic (MCFG / menu editor):

     Command:  (D3) Exec DOOR32 program
     Data:     mystic/doors/nesdoor/nesdoor.sh %Pdoor32

   That opens the game list. To jump straight into one game, put its file
   name (without .nes) first:  mystic/doors/nesdoor/nesdoor.sh tetris %Pdoor32

7. Try it from SyncTERM. You should get the game list; pick a game, read the
   controls screen, press ENTER. The first run creates nesdoor.ini.

8. If anything drops you straight back to the BBS menu:

     tail -20 /home/mystic/mystic/doors/nesdoor/nesdoor.log

   The START line shows what the launcher passed; any Python error follows.

Synchronet on Linux: same steps 1-5, then in SCFG add an online program with
command line  /path/to/nesdoor/nesdoor.sh %n , native executable, standard
I/O intercepted ("Intercept I/O" = Yes), drop file DOOR32.SYS.

UPDATING LATER
  Zip install: unzip the new version from the doors folder exactly as in
  step 2. Your venv, roms/, saves/, nesdoor.ini and log are left alone.
  Git install: see INSTALL / UPDATE WITH GIT below.


==============================================================================
INSTALL / UPDATE WITH GIT
==============================================================================
Source: https://github.com/AlexCorris/nesdoor
Git never touches your venv, roms/, saves/, sessions/, nesdoor.ini or logs -
they're listed in .gitignore - so "git pull" is a safe one-line update.
The emulator core (.so) isn't kept in git; it comes from the Releases page.

NEW INSTALL (replaces steps 1-3 above)

     sudo apt install git python3 python3-venv
     cd /home/mystic/mystic/doors
     git clone https://github.com/AlexCorris/nesdoor.git
     cd nesdoor
     U=https://github.com/AlexCorris/nesdoor/releases/latest/download
     wget $U/fceumm_libretro.so
     python3 -m venv venv
     ./venv/bin/pip install numpy
     mkdir -p roms          # then copy your .nes files in

   Then carry on from step 5 (check the emulator) and step 6 (Mystic menu).

UPDATE

     cd /home/mystic/mystic/doors/nesdoor
     git pull

   Nothing else is needed unless the release notes say the emulator core
   changed; then fetch it again with the two U= / wget lines above, adding
   -O fceumm_libretro.so to the wget line so it overwrites the old file.

SWITCH AN EXISTING ZIP INSTALL TO GIT (keeps your games, saves and settings)

     cd /home/mystic/mystic/doors/nesdoor
     git init -b main
     git remote add origin https://github.com/AlexCorris/nesdoor.git
     git fetch origin
     git reset --hard origin/main
     git branch --set-upstream-to=origin/main main

   "reset --hard" replaces the door's program files with the GitHub copies.
   Your untracked files (venv, roms, saves, ini, the .so) are not touched.
   From then on, "git pull" updates it.

IF "git pull" COMPLAINS ABOUT LOCAL CHANGES
   That means a program file (usually nesdoor.sh) was edited by hand. Keep
   your own tweaks in nesdoor.ini where possible. To update anyway:
       git stash          # set your edits aside
       git pull
       git stash pop      # put them back (fix any conflict it reports)
   Or throw your edits away:  git checkout -- nesdoor.sh && git pull


DROP FILES
  The door reads, in this order:
  1. BBSDEV.DRP - the modern format (https://realdeuce.github.io/bbsdev.drp/).
     Found through the BBSDEV_DRP environment variable the BBS sets, or a
     BBSDEV.DRP file/folder on the command line. Supported I/O types: stdio,
     socket, local. Used from it: the alias, the stable user key (saves stay
     with the player even if they change alias), the forced logoff time (the
     door saves the game and exits on time), and the terminal encoding.
  2. DOOR32.SYS - alias from line 7, socket handle from line 2.
  3. DOOR.SYS - alias from line 36 (or line 10).
  An invalid BBSDEV.DRP is rejected with a note in nesdoor.log and the door
  falls back to the next drop file.


==============================================================================
PLAYING
==============================================================================
GAME LIST   UP/DOWN choose, PGUP/PGDN page, HOME/END, ENTER play.
            Just type to search (titles starting with the text come first,
            then titles containing it). BACKSPACE erases, Ctrl+U clears.
            Ctrl+Q (or Esc Esc) leaves the door.

CONTROLS    D-pad = arrows or WASD     A = X or Space     B = Z
            Start = Enter              Select = Tab or Right Shift
            M = sound on/off           Ctrl+Q or Esc Esc = back to the list
            Ctrl+S = save your spot    Ctrl+L = load it    Ctrl+R = reset
            A controls screen with a gamepad picture appears before each game.

HELD KEYS   In SyncTERM the door turns on physical key reporting, so holding
            Right while tapping Jump works like a real controller. Other
            terminals get timed holds (--hold / --carry) and E / Q combo keys
            (jump right / jump left).

2 PLAYERS   On the controls screen press 2 instead of ENTER: you are PLAYER 1
            and the game is offered to the other nodes. It appears at the top
            of their game list as "> JOIN <you> as PLAYER 2: <game>". The
            joiner gets controller 2 and sees and hears the same game. Player
            2 can join or leave any time. Both callers must be on the same
            machine. Needs the libretro core (not the cynes fallback).

SAVING      Three kinds, all per player and per game (saves/<player>/):
            * Leaving a game (Ctrl+Q, Esc Esc, idle, or even hanging up)
              snapshots it. Next time, the controls screen says "You have a
              saved game from <date>": ENTER continues, N starts over.
            * Ctrl+S saves your spot any time; Ctrl+L jumps back to it.
            * Games with a battery save on the cartridge (Zelda, Final Fantasy,
              Dragon Warrior...) keep their in-game save files too, just like
              the real cartridge - even if the player starts over with N.
            Ctrl+R presses the console's RESET button (saves untouched).
            To wipe everything for one game and start from scratch, press R
            on its controls screen and confirm with Y - that erases the
            continue point, the Ctrl+S save and the cartridge save for that
            player only.
            In 2-player games the host's saves are used. Saving needs the
            libretro core (not the cynes fallback).

IDLE        10 minutes without a key in a game returns to the list; 5 minutes
            idle in the list leaves the door.


==============================================================================
SETTINGS  (nesdoor.ini, created on first run)
==============================================================================
[video]
  scale = 1.5              picture height (1.5 x 240 lines fits 80x25)
  terminal_aspect = 1.2    SyncTERM shows 640x400 at 4:3, so pixels are 1.2x
                           tall; the picture is widened to cancel it. Use 1.0
                           if SyncTERM's aspect correction is turned off.
  pixel_aspect = 1.0       1.0 = square NES pixels; 1.143 = 8:7 "old TV" look

[sound]
  volume = 70   rate = 16000   chunk = 0.2   gain = 2.5
  load / queue / flush     SyncTERM audio command formats. If the sound test
                           in a game stays silent in SyncTERM 1.10+, check
                           these against the Audio section of the CTerm
                           manual (syncterm.net/cterm.html).

Command-line options (add them to the nesdoor.py line in nesdoor.sh):
  --no-audio  --no-keyreport  --no-check (skip sixel check)  --skip N
  --scale N  --row N  --col N  --hold MS  --carry MS  --core PATH


==============================================================================
WINDOWS / SYNCHRONET (Win32)
==============================================================================
  1. Folder, e.g. C:\sbbs\xtrn\nesdoor\ with nesdoor.py, retro.py,
     twoplayer.py, conn.py, sixel.py, nesdoor.bat and a roms\ folder.
  2. Core: download fceumm_libretro.dll.zip from the libretro buildbot
     (buildbot.libretro.com -> nightly -> windows -> x86_64 -> latest) and
     put fceumm_libretro.dll in the folder. 64-bit core needs 64-bit Python.
  3. Either
       a) install Python 3.12 64-bit ("Add to PATH"), then: pip install numpy
          and use nesdoor.bat as the command, or
       b) build one exe:  pip install numpy pyinstaller
                          pyinstaller --onefile nesdoor.py  -> dist\nesdoor.exe
          and put nesdoor.exe next to fceumm_libretro.dll and roms\
  4. SCFG -> External Programs -> Online Programs -> add:
       Command Line ........ C:\sbbs\xtrn\nesdoor\nesdoor.bat %n
       Native Executable ... Yes
       I/O: not intercepted (the door uses the DOOR32 socket handle itself)
       BBS Drop File Type .. DOOR32.SYS
     Option names vary a little between Synchronet versions - see its wiki.


==============================================================================
NOTES
==============================================================================
EMULATOR CORE  fceumm_libretro.so was built from
  https://github.com/libretro/libretro-fceumm (GPLv2) on Ubuntu 24.04 x86-64.
  To build your own (needs git and a compiler):
      sudo apt install git build-essential
      git clone --depth 1 https://github.com/libretro/libretro-fceumm
      cd libretro-fceumm && make -j4
      cp fceumm_libretro.so /home/mystic/mystic/doors/nesdoor/

GAMES  Only use ROMs you have the right to use - homebrew games, or dumps of
  cartridges you own.

VERSION HISTORY
  2.8  BBSDEV.DRP drop file support (stdio, socket, local; logoff deadline)
  2.7.1 save/reset messages moved to the top line
  2.7  Ctrl+R reset button; R on the controls screen erases a game's saves
  2.6  save games: auto-resume, Ctrl+S / Ctrl+L quick save, battery saves
  2.5  correct picture shape for SyncTERM's 4:3 display; sixel check notice
  2.4  Windows / Synchronet fixes (node directory, process check)
  2.3  type-to-search game list, Ctrl+Q to leave, JOIN entries in the list
  2.2  controls screen with gamepad picture; 2-player games
  2.1  scrolling game list; on-screen quit instructions
  2.0  libretro core with sound; SyncTERM key press/release input
