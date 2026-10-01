NESDoor Native Win32 Port
==============================================================================
Native C/Win32 implementation of NESDoor for legacy 32-bit Windows systems.

This port was developed and tested primarily for:

  Windows 7 32-bit
  Synchronet BBS
  DOOR32.SYS Socket mode
  SyncTERM / SIXEL
  FCEUmm libretro core

It exists because the Python implementation could run on the tested Windows 7
32-bit system, but emulator, audio, and terminal rendering performance were
not adequate for normal gameplay.

The native port is EXPERIMENTAL and is not currently a feature-for-feature
replacement for the main Python NESDoor implementation. See LIMITATIONS below.


==============================================================================
ARCHITECTURE
==============================================================================

NES emulation and terminal presentation run on independent timing.

The FCEUmm core advances at its native NES frame rate (approximately 60 Hz).
SIXEL presentation is independently limited to 20 FPS by default.

This is important on older systems and BBS connections: slow terminal
rendering must not slow the emulated NES clock.

Audio is also serviced independently from SIXEL presentation.

The Synchronet DOOR32 socket is inherited from the BBS and treated as
parent-owned. NESDoor therefore does not shutdown() or closesocket() that
inherited socket when returning to the BBS. This permits the door to exit and
be entered again without requiring the caller to disconnect from the BBS.


==============================================================================
FILES
==============================================================================

  build-win7-32.sh     MinGW cross-build script
  nesdoor-c.ini        Runtime tuning
  src/main.c           Main UI, timing and controller logic
  src/door.c/.h        BBS / DOOR32 socket interface
  src/core.c/.h        Libretro core interface
  src/audio.c/.h       SyncTERM audio handling
  src/sixel.c/.h       SIXEL encoder
  src/libretro_min.h   Minimal libretro interface definitions


==============================================================================
BUILDING
==============================================================================

The tested build environment uses the MinGW 32-bit cross compiler on Linux:

  i686-w64-mingw32-gcc

From this directory:

  ./build-win7-32.sh

The resulting executable is:

  NESDoor-C.exe

The tested executable is a PE32 Intel 80386 Windows console application and
uses only these Windows DLL imports:

  KERNEL32.dll
  msvcrt.dll
  WS2_32.dll

The FCEUmm 32-bit Windows libretro core is required at runtime:

  fceumm_libretro.dll


==============================================================================
SYNCHRONET
==============================================================================

The tested configuration is a Synchronet external program using:

  Native Executable: Yes
  I/O mode:          Socket
  Drop file:         DOOR32.SYS

The native port reads the inherited socket handle from DOOR32.SYS.

Do not configure Synchronet to intercept standard I/O for this tested
configuration.


==============================================================================
CONFIGURATION
==============================================================================

Runtime tuning is read from:

  nesdoor-c.ini

The tested defaults are:

[input]
dpad_hold_frames=20
button_hold_frames=20
start_hold_frames=6
select_hold_frames=6

[video]
render_fps=20

Missing, invalid, or out-of-range values fall back to these defaults.

dpad_hold_frames
  Number of native NES frames for which a direction remains pressed after
  receiving an ordinary terminal key event.

button_hold_frames
  Number of native NES frames for which A/B remain pressed. Some NES games
  use button duration for actions such as variable-height jumping.

start_hold_frames / select_hold_frames
  Hold duration for START and SELECT.

render_fps
  Maximum SIXEL presentation rate. This does NOT change NES emulation speed.
  The emulator continues to advance at the core's native frame rate.


==============================================================================
INPUT
==============================================================================

Current fallback controls:

  Arrows / WASD     D-pad
  X / Space         A
  Z                 B
  Enter             Start
  Tab               Select
  M                 Sound on/off
  Ctrl+S            Save state
  Ctrl+L            Load state
  Ctrl+R            Reset
  Ctrl+Q            Return to game list
  Esc Esc           Return to game list

Ordinary ANSI keyboard input supplies key presses but no reliable key-release
event. The native port therefore uses independent per-button hold timers.
Keyboard auto-repeat refreshes the appropriate timer.

Because each NES button has its own timer, combinations such as Right+A,
Right+B, and Right+B+A can overlap.

The main Python NESDoor implementation has more complete physical
key-down/key-up support for compatible terminals. Bringing that protocol to
the native implementation is future work.


==============================================================================
CURRENT LIMITATIONS
==============================================================================

The main Python NESDoor implementation remains the full-featured version.

This native Win32 implementation does not currently claim parity with all
features of the Python version. In particular, do not assume support for:

  * BBSDEV.DRP
  * Python NESDoor's complete physical key-down/key-up protocol
  * two-player node-to-node games
  * every save/resume workflow provided by the Python version
  * every terminal supported by the Python version

The native implementation should therefore be considered a Windows 7/32-bit
compatibility port and development branch rather than a replacement for the
main implementation.


==============================================================================
WHY 20 FPS?
==============================================================================

The NES emulator itself is NOT running at 20 FPS.

The native NES clock continues at approximately 60 Hz. Only SIXEL transmission
is capped at 20 FPS by default.

Decoupling these clocks corrected the slow-motion gameplay and broken audio
experienced when emulator advancement was tied directly to terminal rendering.


==============================================================================
LICENSE
==============================================================================

NESDoor is distributed under the GNU General Public License version 2.

See ../LICENSE for the complete license.

This native implementation was developed as a contribution to NESDoor and is
distributed as part of the same GPLv2 project.
