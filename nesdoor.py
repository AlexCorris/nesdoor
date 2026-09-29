#!/usr/bin/env python3
"""
nesdoor.py v2 - NES games over sixel for SyncTERM, with real key up/down input
and the game's own audio.

  nesdoor.py [--roms roms/] [dropfile]      game list (default)
  nesdoor.py ROM.nes [dropfile]             play one game directly
  options: --core fceumm_libretro.so --scale 1.5 --row 2 --skip 3 (picture size: nesdoor.ini [video])
           --no-audio --no-keyreport

Input:  SyncTERM reports physical key presses AND releases (CSI = 1 h), so
        holding Right while tapping Jump works like a real controller. Other
        terminals fall back to timed holds (--hold / --carry).
Audio:  the emulator's sound is cut into short chunks and queued on a SyncTERM
        1.10 audio channel. M toggles it. --no-audio turns it off.
Needs:  a sixel-capable terminal (SyncTERM recommended); others get a notice.
Keys:   arrows/WASD D-pad, X or Space = A, Z = B, Enter = Start,
        Tab or Right Shift = Select, M = sound on/off, Ctrl+Q or Esc Esc = quit
"""
import argparse, base64, configparser, io, os, re, sys, time, wave
import numpy as np
import conn as connmod
import twoplayer
from sixel import encode

HERE = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__))
BUTTONS = ('b', 'y', 'select', 'start', 'up', 'down', 'left', 'right', 'a')   # RetroPad order

# evdev key codes (what SyncTERM reports in CSI = Pk K / k)
EV = {105: 'left', 106: 'right', 103: 'up', 108: 'down',
      30: 'left', 32: 'right', 17: 'up', 31: 'down',               # A D W S
      45: 'a', 57: 'a', 44: 'b',                                   # X Space Z
      28: 'start', 96: 'start', 15: 'select', 54: 'select'}        # Enter KPEnter Tab RShift
EV_CTRL, EV_Q, EV_ESC, EV_M, EV_S, EV_L, EV_R = (29, 97), 16, 1, 50, 31, 38, 19

# translated keys, for terminals without key reports
KEYS = {b'\x1b[A': 'up', b'\x1b[B': 'down', b'\x1b[C': 'right', b'\x1b[D': 'left',
        b'\x1bOA': 'up', b'\x1bOB': 'down', b'\x1bOC': 'right', b'\x1bOD': 'left',
        b'w': 'up', b's': 'down', b'a': 'left', b'd': 'right',
        b'x': 'a', b' ': 'a', b'z': 'b', b'\r': 'start', b'\n': 'start', b'\t': 'select'}
COMBOS = {b'e': 'right', b'q': 'left'}

DEFAULT_INI = """[video]
; picture height: 1.5 x the NES's 240 lines fills an 80x25 SyncTERM screen
scale = 1.5
; SyncTERM shows its 640x400 screen at 4:3, so each pixel is 1.2x taller than
; wide. The picture is widened by this much to cancel it. Use 1.0 if you turn
; SyncTERM's aspect-ratio correction off.
terminal_aspect = 1.2
; shape of an NES pixel: 1.0 = square (sharp, 16:15 picture),
; 1.143 = 8:7 like on an old TV (wider picture, smaller side panels)
pixel_aspect = 1.0

[sound]
; SyncTERM audio command templates (see the Audio section of the CTerm manual)
load = SyncTERM:A;Load;S={slot};{file}
queue = SyncTERM:A;Queue;C={channel};S={slot};V={volume}{loop}
flush = SyncTERM:A;Flush;C={channel}
volume = 70
; sample rate sent to the caller (lower = less bandwidth): 11025, 16000, 22050
rate = 16000
; seconds of audio per chunk (smaller = less delay, more overhead)
chunk = 0.2
; loudness boost for the emulator's output
gain = 2.5
"""

# ------------------------------------------------------------------ emulators
def mask_of(pressed):
    return sum(1 << BUTTONS.index(b) for b in pressed if b in BUTTONS)

def pressed_of(mask):
    return {b for i, b in enumerate(BUTTONS) if mask & (1 << i)}

class LibretroNES:
    """Any libretro core via retro.py - gives picture AND sound. One core, many games."""
    has_audio = True
    def __init__(self, core):
        import retro
        self.c = retro.Core(core, None, HERE)
    def load(self, rom):
        self.c.load(rom)
        self.fps, self.sample_rate = self.c.fps, self.c.sample_rate
    two_player = True
    def step(self, pressed, pressed2=()):
        self.c.buttons = mask_of(pressed)
        self.c.buttons2 = mask_of(pressed2)
        return self.c.step()
    def audio(self):
        return self.c.take_audio()
    can_save = True
    def save_state(self):
        return self.c.save_state()
    def load_state(self, data):
        return self.c.load_state(data)
    def sram_get(self):
        return self.c.sram_get()
    def sram_set(self, data):
        return self.c.sram_set(data)
    def reset(self):
        self.c.reset()
    def close(self):
        self.c.close()

class CynesNES:
    """Fallback when no libretro core is installed (no sound)."""
    BITS = {'a': 128, 'b': 64, 'select': 32, 'start': 16, 'up': 8, 'down': 4, 'left': 2, 'right': 1}
    has_audio, fps, sample_rate, two_player, can_save = False, 60.0988, 0, False, False
    def load(self, rom):
        from cynes import NES
        self.n = NES(rom)
    def step(self, pressed, pressed2=()):
        self.n.controller = sum(self.BITS[b] for b in pressed if b in self.BITS)
        return self.n.step()
    def audio(self):
        return np.zeros((0, 2), np.int16)
    def close(self):
        pass

# ------------------------------------------------------------------ input
class TimedHolds:
    """Fallback for terminals that only send key presses: each press is a hold
    that auto-repeat keeps alive; pressing A carries the current direction."""
    def __init__(self, first_ms, carry_ms, repeat_ms=110):
        self.first, self.carry, self.repeat = first_ms / 1000, carry_ms / 1000, repeat_ms / 1000
        self.until = {}
    def press(self, btn, now, secs=None):
        if secs is None:
            secs = self.repeat if btn in self.until else self.first
        self.until[btn] = max(self.until.get(btn, 0), now + secs)
        if btn == 'a':
            for d in ('left', 'right', 'up', 'down'):
                if self.until.get(d, 0) >= now:
                    self.until[d] = max(self.until[d], now + self.carry)
    def held(self, now):
        for b in [b for b, t in self.until.items() if t < now]:
            del self.until[b]
        return set(self.until)

def parse(buf):
    """Split input into ('report', press?, [codes]) and ('key', bytes) events."""
    ev, i = [], 0
    while i < len(buf):
        if buf[i] == 0x1b:
            if i + 1 >= len(buf):
                break
            if buf[i + 1] == 0x5b:                      # CSI
                j = i + 2
                while j < len(buf) and not (0x40 <= buf[j] <= 0x7e):
                    j += 1
                if j >= len(buf):
                    break
                body, final = buf[i + 2:j], buf[j:j + 1]
                if body.startswith(b'=') and final in (b'K', b'k'):
                    codes = [int(x) for x in body[1:].split(b';') if x.isdigit()]
                    ev.append(('report', final == b'K', codes))
                else:
                    ev.append(('key', buf[i:i + 2] + final))
                i = j + 1
            elif buf[i + 1] == 0x4f and i + 2 < len(buf):
                ev.append(('key', buf[i:i + 3])); i += 3
            elif buf[i + 1] == 0x1b:
                ev.append(('key', b'\x1b\x1b')); i += 2
            else:
                i += 1
        else:
            ev.append(('key', buf[i:i + 1].lower())); i += 1
    return ev, buf[i:]

# ------------------------------------------------------------------ audio
class AudioOut:
    """Streams emulator audio as short WAV chunks queued back-to-back on one
    SyncTERM audio channel (the channel queue plays them in order)."""
    CH, SLOTS = 2, 8
    def __init__(self, send, cfg, src_rate):
        s = cfg['sound']
        self.send, self.src = send, src_rate
        self.t = {k: s.get(k) for k in ('load', 'queue', 'flush')}
        self.vol, self.rate, self.chunk = int(s.get('volume', 70)), int(s.get('rate', 16000)), float(s.get('chunk', 0.2))
        self.gain = float(s.get('gain', 2.5))
        self.buf, self.n, self.on = np.zeros(0), 0, True
    def apc(self, body):
        self.send(b'\x1b_' + body.encode('ascii') + b'\x1b\\')
    def feed(self, stereo):
        if not self.on or not len(stereo):
            return
        mono = stereo.astype(np.float32).mean(axis=1) / 32768.0 * self.gain
        self.buf = np.concatenate([self.buf, mono])
        need = int(self.chunk * self.src)
        while len(self.buf) >= need:
            part, self.buf = self.buf[:need], self.buf[need:]
            n_out = int(len(part) * self.rate / self.src)
            out = np.interp(np.linspace(0, len(part) - 1, n_out), np.arange(len(part)), part)
            self._send_chunk(out)
    def _send_chunk(self, x):
        b = io.BytesIO()
        with wave.open(b, 'wb') as w:
            w.setnchannels(1); w.setsampwidth(1); w.setframerate(self.rate)
            w.writeframes(((np.clip(x, -1, 1) * 0.95 + 1) * 127.5).astype(np.uint8).tobytes())
        i = self.n % self.SLOTS; self.n += 1
        fn, slot = f'nesa{i}.wav', 200 + i
        self.apc(f'SyncTERM:C;S;{fn};' + base64.b64encode(b.getvalue()).decode('ascii'))
        self.apc(self.t['load'].format(slot=slot, file=fn))
        self.apc(self.t['queue'].format(channel=self.CH, slot=slot, volume=self.vol, loop=''))
    def toggle(self):
        self.on = not self.on
        if not self.on:
            self.stop()
        return self.on
    def stop(self):
        self.buf = np.zeros(0)
        self.apc(self.t['flush'].format(channel=self.CH))

# ------------------------------------------------------------------ saves
class Saves:
    """Per-player, per-game files in saves/<player>/:
         <game>.srm         the cartridge's own battery save (Zelda's save file etc.)
         <game>.state       quick save (Ctrl+S / Ctrl+L)
         <game>.resume      snapshot taken automatically when the player leaves"""
    def __init__(self, name, rom):
        safe = re.sub(r'[^A-Za-z0-9_.-]', '_', name)[:30] or 'player'
        self.dir = os.path.join(HERE, 'saves', safe)
        self.base = os.path.join(self.dir, os.path.splitext(os.path.basename(rom))[0])
    def path(self, kind):
        return f"{self.base}.{kind}"
    def read(self, kind):
        try:
            with open(self.path(kind), 'rb') as f:
                return f.read()
        except OSError:
            return None
    def write(self, kind, data):
        if not data:
            return False
        try:
            os.makedirs(self.dir, exist_ok=True)
            tmp = self.path(kind) + '.tmp'
            with open(tmp, 'wb') as f:
                f.write(data)
            os.replace(tmp, self.path(kind))
            return True
        except OSError:
            return False
    KINDS = ('resume', 'state', 'srm')
    def any(self):
        return any(os.path.exists(self.path(k)) for k in self.KINDS)
    def erase(self):
        for k in self.KINDS:
            try:
                os.remove(self.path(k))
            except OSError:
                pass
    def when(self, kind):
        try:
            return time.strftime("%b %d, %I:%M %p", time.localtime(os.path.getmtime(self.path(kind))))
        except OSError:
            return None

def status_line(send, msg, color="1;33", title=None, col=1):
    """Messages go on the top line (the picture can never cover it). An empty
    message puts the game's title back."""
    if msg:
        text(send, 1, 1, f"{msg:^79}"[:79], color)
    else:
        text(send, 1, 1, " " * 79)
        if title:
            text(send, 1, max(1, col), title[:60], "1;32")

# ------------------------------------------------------------------ screen helpers
def at(row, col):
    return f"\x1b[{row};{col}H"

def text(send, row, col, s, color="37"):
    send(f"{at(row, col)}\x1b[0;{color}m{s}\x1b[0m".encode('cp437', 'replace'))

def nice_name(fn):
    return os.path.splitext(fn)[0].replace('_', ' ').strip()

def help_panels(send, args, audio, role=None, saves=False):
    """Controls on the left, how to quit on the right, beside the picture."""
    left = [("CONTROLS", "1;32"), ("", ""), ("Arrows", "1;37"), ("or WASD", "1;37"), (" move", "0;37"),
            ("X / Space", "1;37"), (" A button", "0;37"), ("Z", "1;37"), (" B button", "0;37"),
            ("Enter", "1;37"), (" Start", "0;37"), ("Tab", "1;37"), (" Select", "0;37")]
    right = [("TO QUIT", "1;31"), ("", ""), ("Ctrl+Q", "1;37"), (" or", "0;37"), ("Esc Esc", "1;37"),
             ("(to list)", "0;37")]
    if audio:
        right += [("", ""), ("SOUND", "1;32"), ("M on/off", "1;37")]
    if saves:
        right += [("", ""), ("SAVE GAME", "1;32"), ("Ctrl+S", "1;37"), (" save", "0;37"),
                  ("Ctrl+L", "1;37"), (" load", "0;37"), ("Ctrl+R", "1;37"), (" reset", "0;37")]
    if role:
        right += [("", ""), (role, "1;33")]
    img_w = int(256 * args.xscale / 8 + 0.999)
    lw = args.col - 2                                      # columns free on the left
    rcol = args.col + img_w + 1
    rw = 81 - rcol
    if lw >= 8:
        for i, (t, c) in enumerate(left):
            if t:
                text(send, 4 + i, 1, t[:lw], c)
    if rw >= 8:
        for i, (t, c) in enumerate(right):
            if t:
                text(send, 4 + i, rcol, t[:rw], c)
    if lw < 8 or rw < 8:                                   # no room at the sides: one line at the bottom
        text(send, 25, 2, "Ctrl+Q or Esc Esc = back to list   M = sound", "1;31")

# ------------------------------------------------------------------ sixel check
def sixel_gate(io_, send, has_sixel):
    """Callers whose terminal can't draw sixel graphics get told why, instead of
    a screen full of garbage. Returns True to continue."""
    if has_sixel:
        return True
    send(b'\x1b[0m\x1b[2J\x1b[?25l')
    text(send, 3, 34, "NES  ARCADE", "1;32")
    msg = ("Your terminal did not answer the graphics check." if has_sixel is None
           else "Your terminal does not support sixel graphics.")
    text(send, 7, 8, "These games draw the picture with SIXEL graphics, so you need a", "1;37")
    text(send, 8, 8, "sixel-capable terminal program to play.", "1;37")
    text(send, 10, 8, msg, "1;33")
    text(send, 12, 8, "Recommended: SyncTERM  (syncterm.net) - free, and it also plays the", "0;37")
    text(send, 13, 8, "game sound and lets you hold keys down like a real controller.", "0;37")
    text(send, 16, 8, "Try anyway? (Y/N)", "1;36")
    pending, t0 = b'', time.monotonic()
    while time.monotonic() - t0 < 120:
        data = io_.read(0.5)
        if data is None:
            return False
        events, pending = parse(pending + data) if data else ([], pending)
        for kind, *v in events:
            if kind == 'key':
                if v[0] == b'y':
                    return True
                if v[0] in (b'n', b'q', b'\r', b'\n', b'\x11', b'\x1b\x1b'):
                    return False
    return False

def confirm_erase(io_, send, title):
    send(b'\x1b[0m\x1b[2J\x1b[?25l')
    text(send, 6, 10, f"Erase ALL saved progress for  {title[:40]}?", "1;31")
    text(send, 8, 10, "This removes your continue point, your Ctrl+S save, and the game's", "1;37")
    text(send, 9, 10, "own save file (like wiping the cartridge battery). It can't be undone.", "1;37")
    text(send, 12, 10, "Other players' saves are not touched.", "0;37")
    text(send, 15, 10, "Type Y to erase and start fresh, anything else to cancel.", "1;36")
    pending, t0 = b'', time.monotonic()
    while time.monotonic() - t0 < 60:
        data = io_.read(0.5)
        if data is None:
            return None
        if data:
            events, pending = parse(pending + data)
            for kind, *v in events:
                if kind == 'key':
                    return v[0] == b'y'
    return False

# ------------------------------------------------------------------ controller picture
PAD_W, PAD_H = 336, 136
PAD_SPOTS = {  # x center in pixels -> (key label, button name)
    'dpad': (72, "ARROWS/WASD", "D-PAD"), 'select': (148, "TAB", "SELECT"), 'start': (198, "ENTER", "START"),
    'b': (250, "Z", "B"), 'a': (298, "X/SPACE", "A")}

def controller_image():
    """A generic two-button gamepad, drawn with numpy (no image files needed)."""
    yy, xx = np.mgrid[0:PAD_H, 0:PAD_W]
    img = np.zeros((PAD_H, PAD_W, 3), np.uint8)
    def rrect(x0, y0, x1, y1, r, rgb):
        m = ((xx >= x0 + r) & (xx <= x1 - r) & (yy >= y0) & (yy <= y1)) | \
            ((yy >= y0 + r) & (yy <= y1 - r) & (xx >= x0) & (xx <= x1))
        for cx, cy in ((x0 + r, y0 + r), (x1 - r, y0 + r), (x0 + r, y1 - r), (x1 - r, y1 - r)):
            m |= (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r
        img[m] = rgb
    def circle(cx, cy, r, rgb):
        img[(xx - cx) ** 2 + (yy - cy) ** 2 <= r * r] = rgb
    rrect(4, 6, PAD_W - 5, PAD_H - 7, 34, (80, 88, 116))          # rim
    rrect(8, 10, PAD_W - 9, PAD_H - 11, 30, (38, 42, 58))         # body
    rrect(32, 55, 112, 81, 4, (14, 14, 18))                        # d-pad
    rrect(59, 28, 85, 108, 4, (14, 14, 18))
    circle(72, 68, 7, (40, 40, 48))
    rrect(130, 62, 166, 74, 6, (105, 105, 118))                    # select
    rrect(180, 62, 216, 74, 6, (105, 105, 118))                    # start
    for cx, rgb in ((250, (70, 120, 220)), (298, (80, 190, 90))):  # B, A
        circle(cx, 68, 21, tuple(min(255, v + 60) for v in rgb))
        circle(cx, 68, 18, rgb)
    return img

def controls_screen(io_, send, title, lines, start_label="ENTER to start", allow_two=False, allow_new=False,
                    allow_erase=False):
    """Shows what every key does. Returns True (start), False (back) or None (hung up)."""
    send(b'\x1b[0m\x1b[2J\x1b[?25l')
    text(send, 2, max(1, (80 - len(title) - 14) // 2), f"HOW TO PLAY:  {title}"[:78], "1;32")
    for i, (t, c) in enumerate(lines[:2]):
        text(send, 4 + i, max(1, (80 - len(t)) // 2 + 1), t, c)
    col0 = (80 - PAD_W // 8) // 2 + 1
    send(at(7, col0).encode() + encode(controller_image()))
    for key, name_row, color in ((1, 16, "1;33"), (2, 17, "0;37")):
        for spot, (x, label, name) in PAD_SPOTS.items():
            t = label if key == 1 else name
            c = col0 + x // 8 - len(t) // 2
            text(send, name_row, c, t, color)
    text(send, 19, 10, "In the game:  Ctrl+Q or Esc Esc = back to the list     M = sound", "1;31")
    text(send, 21, 10, "Ctrl+S saves your spot, Ctrl+L loads it. Leaving saves automatically.", "0;36")
    if allow_erase:
        text(send, 22, 10, "R = erase ALL saved progress for this game and start from scratch", "0;31")
    lab = f"{start_label}      Q = back to the list"
    text(send, 23, max(1, (80 - len(lab)) // 2 + 1), lab, "1;36")
    pending, t0 = b'', time.monotonic()
    while True:
        data = io_.read(0.5)
        if data is None:
            return None
        if not data:
            if time.monotonic() - t0 > 120:
                return False
            continue
        events, pending = parse(pending + data)
        for kind, *v in events:
            if kind != 'key':
                continue
            if v[0] in (b'\r', b'\n', b' '):
                return True
            if v[0] == b'2' and allow_two:
                return 'two'
            if v[0] == b'n' and allow_new:
                return 'new'
            if v[0] == b'r' and allow_erase:
                return 'erase'
            if v[0] in (b'q', b'\x11', b'\x1b\x1b'):
                return False

# ------------------------------------------------------------------ game list
def picker(io_, send, roms, state, sessions_dir=None):
    """Scrolling, searchable list of ROMs, with any open 2-player games at the top.
    Returns ('play', rom), ('join', session) or None to leave the door."""
    names = [nice_name(f) for f in roms]
    sessions = twoplayer.list_sessions(sessions_dir) if sessions_dir else []
    last_scan = time.monotonic()
    top, rows = 5, 14
    search = state.get('search', '')
    sel = state.get('sel', 0)
    pending, last = b'', time.monotonic()

    def items():
        """(label, value) rows: join offers first, then games matching the search."""
        out = [(f"> JOIN {s_['host'][:18]} as PLAYER 2: {nice_name(s_['game'])[:26]}", ('join', s_))
               for s_ in sessions]
        q = search.lower()
        if q:
            first = [i for i, n in enumerate(names) if n.lower().startswith(q)]
            rest = [i for i, n in enumerate(names) if q in n.lower() and i not in first]
            idx = first + rest
        else:
            idx = range(len(names))
        out += [(names[i], ('play', roms[i])) for i in idx]
        return out

    def draw(full=True):
        its = items()
        nonlocal sel
        sel = max(0, min(sel, len(its) - 1))
        if full:
            send(b'\x1b[0m\x1b[2J\x1b[?25l')
            text(send, 1, 22, "Needs a sixel terminal such as SyncTERM", "0;36")
            text(send, 2, 34, "NES  ARCADE", "1;32")
            text(send, 23, 4, "UP/DOWN choose   PGUP/PGDN page   HOME/END   ENTER play", "1;36")
            text(send, 24, 4, "Type to search (BACKSPACE erases)      Ctrl+Q = leave", "1;36")
        n_games = sum(1 for _, v in its if v[0] == 'play')
        status = (f"{n_games} of {len(roms)} games match" if search else f"{len(roms)} games") + \
                 (f"   {len(sessions)} open 2-player game{'s' if len(sessions) != 1 else ''}" if sessions else "")
        text(send, 3, 4, f"{status:<72}", "0;32")
        off = min(max(0, sel - rows // 2), max(0, len(its) - rows))
        text(send, top - 1, 36, "-- more --" if off > 0 else " " * 10, "0;36")
        for i in range(rows):
            k = off + i
            if k >= len(its):
                text(send, top + i, 10, " " * 62)
                continue
            label, val = its[k]
            num = "    " if val[0] == 'join' else f"{k - len(sessions) + 1:>3}."
            color = "1;37;44" if k == sel else ("1;33" if val[0] == 'join' else "0;37")
            text(send, top + i, 10, f" {num} {label[:54]:<54} ", color)
        text(send, top + rows, 36, "-- more --" if off + rows < len(its) else " " * 10, "0;36")
        if its:
            text(send, 21, 4, f"{'Search: ' + search + '_' if search else '':<40}   {sel + 1} of {len(its):<6}", "1;33")
        else:
            text(send, 21, 4, f"Search: {search}_   no matches - BACKSPACE to erase{' ' * 20}", "1;31")
        return its

    if not roms and not sessions:
        send(b'\x1b[0m\x1b[2J')
        text(send, 10, 20, "No games found in the roms folder.", "1;31")
        text(send, 12, 20, "Put .nes files in roms/ and try again.", "0;37")
        time.sleep(3)
        return None
    its = draw()
    while True:
        data = io_.read(0.5)
        if data is None:
            return None
        if sessions_dir and time.monotonic() - last_scan > 2:
            now_s = twoplayer.list_sessions(sessions_dir)
            last_scan = time.monotonic()
            if [d['pid'] for d in now_s] != [d['pid'] for d in sessions]:
                sessions = now_s; its = draw(False)
        if not data:
            if time.monotonic() - last > 300:
                return None
            continue
        last = time.monotonic()
        events, pending = parse(pending + data)
        old_sel, old_search = sel, search
        for kind, *v in events:
            if kind != 'key':
                continue
            k = v[0]
            if k in (b'\x11', b'\x03', b'\x1b\x1b'):                # Ctrl+Q, Ctrl+C, Esc Esc
                return None
            elif k in (b'\x1b[A', b'\x1bOA'):
                sel = (sel - 1) % max(1, len(its))
            elif k in (b'\x1b[B', b'\x1bOB'):
                sel = (sel + 1) % max(1, len(its))
            elif k == b'\x1b[V':                                    # SyncTERM Page Up
                sel = max(0, sel - rows)
            elif k == b'\x1b[U':                                    # SyncTERM Page Down
                sel = min(len(its) - 1, sel + rows)
            elif k == b'\x1b[H':
                sel = 0
            elif k in (b'\x1b[K', b'\x1b[F'):
                sel = len(its) - 1
            elif k in (b'\r', b'\n'):
                if its:
                    state['sel'], state['search'] = sel, search
                    return its[sel][1]
            elif k in (b'\x08', b'\x7f'):
                search = search[:-1]
            elif k == b'\x15':                                      # Ctrl+U clears the search
                search = ''
            elif len(k) == 1 and 32 <= k[0] < 127 and not (k == b' ' and not search):
                search = (search + k.decode())[:30]
        if search != old_search:
            sel = len(sessions) if search else 0
            its = draw(False)
        elif sel != old_sel:
            its = draw(False)

# ------------------------------------------------------------------ one game
def p2_status(send, args, host):
    """Who is controller 2 - shown under the controls on the left."""
    w = max(1, min(12, args.col - 2))
    text(send, 19, 1, "PLAYER 2:"[:w], "1;33")
    if host.guest:
        text(send, 20, 1, f"{host.guest[:w]:<{w}}", "1;37"); text(send, 21, 1, " " * w)
    else:
        text(send, 20, 1, "waiting..."[:w], "0;37"); text(send, 21, 1, "(top list)"[:w], "0;36")

def play_game(io_, send, emu, rom, term, cfg, args, pending=b'', host=None, name='player', resume=False):
    """Returns 'back' (player quit to the list) or 'gone' (caller hung up).
    With host set, a second caller can drop in as controller 2 and sees the same screen."""
    emu.load(rom)
    saves = Saves(name, rom) if getattr(emu, 'can_save', False) else None
    sram_saved = None
    if saves:
        sram = saves.read('srm')
        if sram:
            emu.sram_set(sram)
        sram_saved = emu.sram_get()
        if resume:
            emu.load_state(saves.read('resume'))
    cterm = term.get('cterm')
    reports = bool(cterm) and not args.no_keyreport
    audio = None
    msg_until = 0.0
    def both(data):                           # host's caller + mirrored to player 2
        send(data)
        if host:
            host.send(data)
    local_audio = bool(cterm and cterm >= (1, 329))
    def audio_out(data):                      # SyncTERM audio only to terminals that can play it
        if local_audio:
            send(data)
        if host:
            host.send(data)
    if emu.has_audio and not args.no_audio and (local_audio or host):
        audio = AudioOut(audio_out, cfg, emu.sample_rate)
    pos = b'\x1b[%d;%dH' % (args.row, args.col)
    send(b'\x1b[0m\x1b[?25l\x1b[2J')
    game_title = nice_name(os.path.basename(rom))
    text(send, 1, max(1, args.col), game_title[:60], "1;32")
    help_panels(send, args, audio, "PLAYER 1" if host else None, saves=bool(saves))
    if resume and saves:
        status_line(send, "Continuing your saved game"); msg_until = time.monotonic() + 3
    if host:
        p2_status(send, args, host)
    if reports:
        send(b'\x1b[=1h\x1b[=2h')           # physical key press/release reports, no translated keys
    physical, got_report = set(), False
    timed = TimedHolds(args.hold, args.carry)
    frame_dt = 1.0 / emu.fps
    last_sent, n = None, 0
    start = next_t = time.monotonic()
    last_esc, last_input = 0.0, time.monotonic()
    result = 'back'
    running = True
    quick = None
    last_sram_check = time.monotonic()
    try:
        while running:
            now = time.monotonic()
            if msg_until and now > msg_until:
                status_line(send, "", title=game_title, col=args.col); msg_until = 0.0
            if saves and now - last_sram_check > 20:          # keep the cartridge save on disk
                last_sram_check = now
                cur = emu.sram_get()
                if cur and cur != sram_saved:
                    saves.write('srm', cur); sram_saved = cur
            if args.maxsecs and now - start > args.maxsecs:
                break
            if now - last_input > 600:              # 10 idle minutes: back to the list
                break
            data = io_.read(max(0.0, next_t - now))
            if data is None:
                result = 'gone'; break
            if data:
                last_input = time.monotonic()
                events, pending = parse(pending + data)
                t = time.monotonic()
                for kind, *v in events:
                    if kind == 'report':
                        press, codes = v
                        got_report = True
                        for c in codes:
                            if press:
                                physical.add(c)
                                ctrl = physical & set(EV_CTRL)
                                if c == EV_Q and ctrl:
                                    running = False
                                elif c == EV_S and ctrl:
                                    quick = 'save'
                                elif c == EV_L and ctrl:
                                    quick = 'load'
                                elif c == EV_R and ctrl:
                                    quick = 'reset'
                                elif c == EV_ESC:
                                    if t - last_esc < 0.6:
                                        running = False
                                    last_esc = t
                                elif c == EV_M and audio:
                                    audio.toggle()
                            else:
                                physical.discard(c)
                    else:
                        k = v[0]
                        if k in (b'\x11', b'\x1b\x1b'):
                            running = False
                        elif k == b'\x13':
                            quick = 'save'
                        elif k == b'\x0c':
                            quick = 'load'
                        elif k == b'\x12':
                            quick = 'reset'
                        elif k == b'm' and audio:
                            audio.toggle()
                        elif k in COMBOS:
                            timed.press(COMBOS[k], t, timed.carry); timed.press('a', t)
                        elif k in KEYS:
                            timed.press(KEYS[k], t)
                if quick == 'reset':
                    if hasattr(emu, 'reset'):
                        emu.reset(); last_sent = None
                        status_line(send, "RESET pressed - your saves are untouched")
                        msg_until = time.monotonic() + 3
                elif quick and not saves:
                    status_line(send, "Saving needs the libretro emulator core", "1;31")
                    msg_until = time.monotonic() + 3
                elif quick == 'save':
                    ok = saves.write('state', emu.save_state())
                    saves.write('srm', emu.sram_get())
                    status_line(send, "Game saved - Ctrl+L brings you back to this spot" if ok else "Couldn't save!",
                                "1;33" if ok else "1;31")
                    msg_until = time.monotonic() + 3
                elif quick == 'load':
                    data = saves.read('state')
                    ok = bool(data) and emu.load_state(data)
                    status_line(send, "Loaded your saved spot" if ok else "No saved spot yet - press Ctrl+S to save",
                                "1;33" if ok else "1;31")
                    msg_until = time.monotonic() + 3
                    last_sent = None
                quick = None
                continue
            if got_report:
                pressed = set() if physical & set(EV_CTRL) else {EV[c] for c in physical if c in EV}
            else:
                pressed = timed.held(time.monotonic())
            if 'left' in pressed and 'right' in pressed:
                pressed -= {'left', 'right'}
            if 'up' in pressed and 'down' in pressed:
                pressed -= {'up', 'down'}
            pressed2 = ()
            if host:
                host.poll()
                if host.just_joined or host.just_left:
                    p2_status(send, args, host)
                    last_sent = None                   # give the newcomer a full picture
                pressed2 = pressed_of(host.buttons)
                if 'left' in pressed2 and 'right' in pressed2:
                    pressed2 -= {'left', 'right'}
            frame = emu.step(pressed, pressed2)
            if audio:
                audio.feed(emu.audio())
            n += 1
            next_t += frame_dt
            if time.monotonic() - next_t > 0.25:
                next_t = time.monotonic()
            if frame is not None and n % args.skip == 0 and (last_sent is None or not np.array_equal(frame, last_sent)):
                both(pos + encode(frame, args.scale, args.xscale))
                last_sent = frame.copy()
    finally:
        if saves:                                           # leaving (or hung up): keep their place
            saves.write('srm', emu.sram_get())
            saves.write('resume', emu.save_state())
        try:
            if audio:
                audio.stop()
            if reports:
                send(b'\x1b[=2l\x1b[=1l')
        except OSError:
            result = 'gone'
    return result

# ------------------------------------------------------------------ player 2
def join_game(io_, send, sess, term, args, name):
    """Play as controller 2 in someone else's game. Returns 'back' or 'gone'."""
    try:
        g = twoplayer.Guest(sess['port'], name)
    except OSError as e:
        send(b'\x1b[0m\x1b[2J')
        text(send, 10, 15, f"Couldn't join that game ({e}).", "1;31")
        time.sleep(2.5)
        return 'back'
    cterm = term.get('cterm')
    reports = bool(cterm) and not args.no_keyreport
    send(b'\x1b[0m\x1b[?25l\x1b[2J')
    text(send, 1, max(1, args.col), f"{nice_name(g.game)[:44]}  - {sess['host'][:16]}'s game", "1;32")
    help_panels(send, args, True, "PLAYER 2")
    if reports:
        send(b'\x1b[=1h\x1b[=2h')
    physical, got_report = set(), False
    timed = TimedHolds(args.hold, args.carry)
    pending, last_mask, last_send = b'', -1, 0.0
    last_esc, last_input = 0.0, time.monotonic()
    result, running, host_left = 'back', True, False
    try:
        while running:
            data = io_.read(1 / 60)
            if data is None:
                result = 'gone'; break
            now = time.monotonic()
            if data:
                last_input = now
                events, pending = parse(pending + data)
                for kind, *v in events:
                    if kind == 'report':
                        press, codes = v
                        got_report = True
                        for c in codes:
                            if press:
                                physical.add(c)
                                if c == EV_Q and physical & set(EV_CTRL):
                                    running = False
                                elif c == EV_ESC:
                                    if now - last_esc < 0.6:
                                        running = False
                                    last_esc = now
                            else:
                                physical.discard(c)
                    else:
                        k = v[0]
                        if k in (b'\x11', b'\x1b\x1b'):
                            running = False
                        elif k in COMBOS:
                            timed.press(COMBOS[k], now, timed.carry); timed.press('a', now)
                        elif k in KEYS:
                            timed.press(KEYS[k], now)
            if now - last_input > 600:
                break
            pressed = {EV[c] for c in physical if c in EV} if got_report else timed.held(now)
            mask = mask_of(pressed)
            if mask != last_mask or now - last_send > 0.5:
                g.buttons(mask); last_mask, last_send = mask, now
            for t, p in g.messages():
                if t == b'T':
                    send(p)
                elif t == b'X':
                    running, host_left = False, True
    finally:
        g.close()
        try:
            if reports:
                send(b'\x1b[=2l\x1b[=1l')
            send(b'\x1b[0m\x1b[2J\x1b[?25l')
        except OSError:
            result = 'gone'
    if host_left and result != 'gone':
        text(send, 10, 20, f"{sess['host'][:20]} ended the game.", "1;33")
        time.sleep(2)
    return result

# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('rom', nargs='?', default=None, help='a .nes file to play directly, or a folder to list')
    ap.add_argument('dropfile', nargs='?')
    ap.add_argument('--roms', default=None, help='folder of .nes files for the game list (default ./roms)')
    ap.add_argument('--core', default=None, help='libretro core (default: fceumm_libretro.so/.dll here)')
    ap.add_argument('--skip', type=int, default=3)
    ap.add_argument('--scale', type=float, default=None, help='picture height scale (default from nesdoor.ini)')
    ap.add_argument('--row', type=int, default=2); ap.add_argument('--col', type=int, default=None)
    ap.add_argument('--hold', type=int, default=100); ap.add_argument('--carry', type=int, default=650)
    ap.add_argument('--no-audio', action='store_true'); ap.add_argument('--no-keyreport', action='store_true')
    ap.add_argument('--io', default='auto', choices=['auto', 'stdio', 'socket', 'console'])
    ap.add_argument('--maxsecs', type=float, default=0)
    ap.add_argument('--no-check', action='store_true', help='skip the sixel terminal check')
    ap.add_argument('--name', default=None, help='player name (normally read from the drop file)')
    args, _ = ap.parse_known_args()

    # a single positional that isn't a ROM is the drop file (Mystic's %Pdoor32)
    direct = None
    if args.rom and os.path.isfile(args.rom) and args.rom.lower().endswith('.nes'):
        direct = args.rom
    elif args.rom and os.path.isdir(args.rom) and \
            any(f.lower().endswith('.nes') for f in os.listdir(args.rom)):
        args.roms = args.rom                                   # a folder of games
    elif args.rom and os.path.isdir(args.rom) and not args.dropfile:
        args.dropfile = args.rom                               # a node directory (Synchronet %n)
    elif args.rom and not args.dropfile:
        args.dropfile = args.rom
    romdir = args.roms or os.path.join(HERE, 'roms')

    ini = os.path.join(HERE, 'nesdoor.ini')
    if not os.path.exists(ini):
        try:
            open(ini, 'w').write(DEFAULT_INI)
        except OSError:
            pass
    cfg = configparser.ConfigParser(); cfg.read_string(DEFAULT_INI); cfg.read(ini)
    v = cfg['video']
    if args.scale is None:
        args.scale = float(v.get('scale', 1.5))
    args.xscale = args.scale * float(v.get('terminal_aspect', 1.2)) * float(v.get('pixel_aspect', 1.0))
    img_cols = int(256 * args.xscale / 8 + 0.999)
    if args.col is None:
        args.col = max(1, (80 - img_cols) // 2 + 1)

    core = args.core
    if core is None:
        for name in ('fceumm_libretro.so', 'fceumm_libretro.dll', 'nestopia_libretro.so', 'nestopia_libretro.dll'):
            if os.path.exists(os.path.join(HERE, name)):
                core = os.path.join(HERE, name); break
    emu = LibretroNES(core) if core else CynesNES()

    info = connmod.read_dropfile(args.dropfile)
    io_ = connmod.open_connection(args.io, info)
    term = connmod.detect_terminal(io_)
    send = io_.write
    state = {}
    if not args.no_check and not sixel_gate(io_, send, term['sixel']):
        try:
            send(b'\x1b[0m\x1b[2J\x1b[H\x1b[?25h')
        finally:
            emu.close(); io_.close()
        return
    name = (args.name or info.get('name') or os.environ.get('USER') or 'Player')[:20]
    sessions_dir = os.path.join(HERE, 'sessions')
    try:
        if direct:
            play_game(io_, send, emu, direct, term, cfg, args, term['leftover'], name=name)
        else:
            while True:
                try:
                    roms = sorted((f for f in os.listdir(romdir) if f.lower().endswith('.nes')), key=str.lower)
                except OSError:
                    roms = []
                pick = picker(io_, send, roms, state, sessions_dir)
                if pick is None:
                    break
                if pick[0] == 'join':
                    ok = controls_screen(io_, send, nice_name(pick[1]['game']),
                                         [("You are PLAYER 2 - you'll use controller 2", "1;33"),
                                          (f"in {pick[1]['host']}'s game. You both see the same screen.", "0;37")],
                                         "ENTER to join")
                    if ok is None:
                        break
                    if ok and join_game(io_, send, pick[1], term, args, name) == 'gone':
                        break
                    continue
                rom = pick[1]
                two_ok = getattr(emu, 'two_player', False)
                sv = Saves(name, rom) if getattr(emu, 'can_save', False) else None
                saved_at = sv.when('resume') if sv else None
                if saved_at:
                    lines = [(f"You have a saved game from {saved_at}.", "1;33"),
                             ("ENTER continues it, N starts over" + (", 2 continues with a friend." if two_ok else "."),
                              "0;37")]
                    label = "ENTER = continue   N = new game" + ("   2 = 2 players" if two_ok else "")
                elif two_ok:
                    lines = [("Press 2 to let a friend on another node join as PLAYER 2", "1;33"),
                             ("(it shows up at the top of their game list).", "0;37")]
                    label = "ENTER = 1 player   2 = 2 players"
                else:
                    lines, label = [("Controller 1", "1;33"), ("", "")], "ENTER to start"
                ok = controls_screen(io_, send, nice_name(rom), lines, label,
                                     allow_two=two_ok, allow_new=bool(saved_at), allow_erase=bool(sv and sv.any()))
                if ok is None:
                    break
                if ok == 'erase':
                    yes = confirm_erase(io_, send, nice_name(rom))
                    if yes is None:
                        break
                    if not yes:
                        continue
                    sv.erase()
                    saved_at, ok = None, True
                if not ok:
                    continue
                two = ok == 'two'
                resume = bool(saved_at) and ok != 'new'
                host = twoplayer.Host(sessions_dir, name, rom) if two else None
                try:
                    if play_game(io_, send, emu, os.path.join(romdir, rom), term, cfg, args, host=host,
                                 name=name, resume=resume) == 'gone':
                        break
                except RuntimeError as e:                      # bad or unsupported ROM
                    send(b'\x1b[0m\x1b[2J')
                    text(send, 10, 15, f"Couldn't start that game: {e}", "1;31")
                    time.sleep(2.5)
                finally:
                    if host:
                        host.close()
    finally:
        try:
            send(b'\x1b[0m\x1b[2J\x1b[H\x1b[?25h')
        except OSError:
            pass
        emu.close()
        io_.close()

if __name__ == '__main__':
    main()
