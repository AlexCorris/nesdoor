"""
Connection layer for BBS doors: the game only calls read() and write().

  stdio   - Linux/Unix: BBS redirects the caller to stdin/stdout
            (Mystic STDIO, Synchronet "Intercept I/O", ENiGMA stdio)
  socket  - DOOR32 socket handle from door32.sys (Windows boards, and Unix
            boards configured to pass the socket). Handles telnet codes.
            A BBSDEV.DRP "socket" is already a clean byte stream, so no
            telnet handling is applied to it.
  console - local play in a Windows terminal (testing)

Drop files, in order of preference:
  BBSDEV.DRP  - found through the BBSDEV_DRP environment variable (the modern
                format, https://realdeuce.github.io/bbsdev.drp/), or given
                on the command line
  DOOR32.SYS  - line 7 alias, line 2 socket handle
  DOOR.SYS    - line 36 alias
"""
import calendar, hashlib, os, select, socket, sys, time

IS_WIN = os.name == 'nt'
IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240

class TelnetFilter:
    """Strips telnet commands from incoming bytes, keeping state across reads.
    Also turns CR NUL / CR LF into a single CR."""
    def __init__(self):
        self.state, self.last_cr = 'data', False
    def feed(self, data):
        out = bytearray()
        for b in data:
            s = self.state
            if s == 'data':
                if b == IAC:
                    self.state = 'iac'
                elif self.last_cr and b in (0, 10):
                    pass                                # CR NUL or CR LF -> CR
                else:
                    out.append(b)
                self.last_cr = (b == 13)
            elif s == 'iac':
                if b == IAC:
                    out.append(IAC); self.state = 'data'
                elif b in (WILL, WONT, DO, DONT):
                    self.state = 'opt'
                elif b == SB:
                    self.state = 'sb'
                else:
                    self.state = 'data'
            elif s == 'opt':
                self.state = 'data'
            elif s == 'sb':
                if b == IAC:
                    self.state = 'sb_iac'
            elif s == 'sb_iac':
                self.state = 'data' if b == SE else 'sb'
        return bytes(out)

class StdioConn:
    def __init__(self):
        self.in_fd, self.out_fd = sys.stdin.fileno(), sys.stdout.fileno()
        self.old_tty = None
        if os.isatty(self.in_fd):
            import termios, tty
            self.old_tty = termios.tcgetattr(self.in_fd)
            tty.setraw(self.in_fd)
    def read(self, timeout):
        r, _, _ = select.select([self.in_fd], [], [], max(0.0, timeout))
        if not r:
            return b''
        try:
            data = os.read(self.in_fd, 512)
        except (BlockingIOError, InterruptedError):
            return b''
        return data if data else None                   # None = caller gone
    def write(self, data):
        mv = memoryview(data)
        while mv:
            try:
                mv = mv[os.write(self.out_fd, mv):]
            except BlockingIOError:
                select.select([], [self.out_fd], [], 1.0)
    def close(self):
        if self.old_tty:
            import termios
            termios.tcsetattr(self.in_fd, termios.TCSADRAIN, self.old_tty)

class SocketConn:
    def __init__(self, handle, clean=False):
        self.sock = socket.socket(fileno=int(handle))   # adopt the BBS's socket
        self.sock.setblocking(False)
        self.clean = clean                              # BBSDEV.DRP: no telnet layer
        self.telnet = TelnetFilter()
    def read(self, timeout):
        r, _, _ = select.select([self.sock], [], [], max(0.0, timeout))
        if not r:
            return b''
        try:
            data = self.sock.recv(512)
        except (BlockingIOError, InterruptedError):
            return b''
        except OSError:
            return None
        if not data:
            return None
        return data if self.clean else self.telnet.feed(data)
    def write(self, data):
        data = bytes(data)
        if not self.clean:
            data = data.replace(b'\xff', b'\xff\xff')        # escape IAC
        mv = memoryview(data)
        while mv:
            try:
                mv = mv[self.sock.send(mv):]
            except BlockingIOError:
                select.select([], [self.sock], [], 1.0)
    def close(self):
        try:
            self.sock.setblocking(True)
            self.sock.detach()                          # hand socket back; BBS closes it
        except OSError:
            pass

class WinConsoleConn:
    """Local testing in Windows Terminal (which supports sixel)."""
    ARROWS = {b'H': b'\x1b[A', b'P': b'\x1b[B', b'M': b'\x1b[C', b'K': b'\x1b[D'}
    def __init__(self):
        import msvcrt
        self.msvcrt = msvcrt
        os.system('')                                   # enable VT processing
        self.out = sys.stdout.buffer
    def read(self, timeout):
        end = time.monotonic() + max(0.0, timeout)
        while True:
            if self.msvcrt.kbhit():
                out = bytearray()
                while self.msvcrt.kbhit():
                    ch = self.msvcrt.getch()
                    if ch in (b'\x00', b'\xe0'):
                        out += self.ARROWS.get(self.msvcrt.getch(), b'')
                    else:
                        out += ch
                return bytes(out)
            if time.monotonic() >= end:
                return b''
            time.sleep(0.005)
    def write(self, data):
        self.out.write(data); self.out.flush()
    def close(self):
        pass

class Deadline:
    """Wraps a connection so that at the BBS's forced logoff time (BBSDEV.DRP
    line 11) reads report the caller as gone - every door loop already treats
    that as 'save what matters and exit'."""
    def __init__(self, conn, deadline):
        self.conn, self.deadline = conn, deadline
    def read(self, timeout):
        left = self.deadline - time.time()
        if left <= 0:
            return None
        data = self.conn.read(min(timeout, left))
        return None if time.time() >= self.deadline and data == b'' else data
    def write(self, data):
        self.conn.write(data)
    def close(self):
        self.conn.close()

def open_connection(mode, dropinfo):
    """mode: 'auto', 'stdio', 'socket', 'console'."""
    if mode == 'auto':
        if dropinfo.get('mode') == 'socket' or (dropinfo.get('handle') and IS_WIN):
            mode = 'socket'
        elif IS_WIN:
            mode = 'console'
        else:
            mode = 'stdio'
    if mode == 'socket':
        if not dropinfo.get('handle'):
            raise SystemExit("socket mode needs a drop file with a socket handle")
        c = SocketConn(dropinfo['handle'], clean=dropinfo.get('clean', False))
    elif mode == 'console':
        c = WinConsoleConn()
    else:
        c = StdioConn()
    if dropinfo.get('deadline'):
        c = Deadline(c, dropinfo['deadline'])
    return c

# ------------------------------------------------------------------ drop files
def _read_lines(path):
    with open(path, encoding='cp437', errors='replace') as f:
        return [l.strip() for l in f.read().splitlines()]

class DropFileError(ValueError):
    pass

BBSDEV_MODES = {'local', 'stdio', 'socket'}        # the ones these doors can use

def parse_bbsdev(path):
    """Parse and validate a BBSDEV.DRP (format 1.x). Raises DropFileError."""
    raw = open(path, 'rb').read()
    if raw.startswith(b'\xef\xbb\xbf'):
        raise DropFileError("byte-order mark not allowed")
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        raise DropFileError("not valid UTF-8")
    if '\r' in text.replace('\r\n', ''):
        raise DropFileError("bare CR line ending")
    lines = text.replace('\r\n', '\n').split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    if len(lines) < 19:
        raise DropFileError(f"only {len(lines)} lines, need 19")
    ver = lines[0].split('.')
    if len(ver) != 2 or not all(v.isdigit() for v in ver):
        raise DropFileError(f"bad version {lines[0]!r}")
    if ver[0] != '1':
        raise DropFileError(f"unsupported major version {lines[0]}")
    L = lines[:19]                                  # newer minor versions may append lines
    for i, v in enumerate(L):
        if v != v.strip() or any(ord(ch) < 32 or 127 <= ord(ch) < 160 for ch in v):
            raise DropFileError(f"line {i + 1} has whitespace or control characters")
    comm, param = L[1], L[2]
    if comm not in ('local', 'stdio', 'socket', 'serial', 'winserial', 'uart', 'fossil'):
        raise DropFileError(f"unknown communications type {comm!r}")
    if comm not in BBSDEV_MODES:
        raise DropFileError(f"communications type {comm!r} isn't supported by this door")
    if comm in ('local', 'stdio') and param:
        raise DropFileError("line 3 must be empty for local/stdio")
    if comm == 'socket' and not param.isdigit():
        raise DropFileError("line 3 must be a socket number")
    for n in (4, 5, 6, 7, 12, 13, 14, 15, 16, 17, 18, 19):
        if not L[n - 1]:
            raise DropFileError(f"line {n} is empty")
    for n in (6, 7):
        if not L[n - 1].isdigit() or not 1 <= int(L[n - 1]) <= 65535:
            raise DropFileError(f"line {n} must be 1-65535")
    for n in (8, 9, 19):
        if L[n - 1] not in ('Y', 'N'):
            raise DropFileError(f"line {n} must be Y or N")
    if not (L[16].isdigit() or L[16] in ('sysop', 'cosysop')):
        raise DropFileError("line 17 must be a number, sysop or cosysop")
    if not L[17].isdigit():
        raise DropFileError("line 18 must be a node number")
    cterm = None
    if L[9]:
        parts = L[9].split('.')
        if not all(p.isdigit() for p in parts) or len(parts) < 2:
            raise DropFileError("line 10 must be a dotted CTerm revision")
        cterm = (int(parts[0]), int(parts[1]))
    deadline = None
    if L[10]:
        try:
            deadline = calendar.timegm(time.strptime(L[10], '%Y-%m-%dT%H:%M:%SZ'))
        except ValueError:
            raise DropFileError("line 11 must be YYYY-MM-DDTHH:MM:SSZ")
    return {
        'format': 'BBSDEV.DRP ' + lines[0], 'mode': comm, 'handle': int(param) if comm == 'socket' else None,
        'clean': True, 'name': L[3], 'key': L[4], 'width': int(L[5]), 'height': int(L[6]),
        'ansi': L[7] == 'Y', 'rip': L[8] == 'Y', 'cterm': cterm, 'deadline': deadline,
        'encoding': L[11], 'language': L[12], 'bbs_software': L[13], 'board': L[14], 'sysop': L[15],
        'level': L[16], 'is_sysop': L[16] in ('sysop', 'cosysop'), 'node': int(L[17]), 'local_display': L[18] == 'Y',
    }

def user_id(info):
    """A stable, filesystem-safe id for per-player files: from the BBSDEV.DRP
    user key when there is one (survives alias changes), else from the alias."""
    import re
    if info.get('key'):
        return 'key-' + hashlib.sha256(info['key'].encode('utf-8')).hexdigest()[:16]
    return re.sub(r'[^A-Za-z0-9_.-]', '_', info.get('name') or 'player')[:30] or 'player'

def text_codec(info):
    """Python codec for text sent to the caller (BBSDEV.DRP line 12), default CP437."""
    import codecs
    enc = (info.get('encoding') or 'IBM437').strip()
    try:
        return codecs.lookup(enc).name
    except LookupError:
        return 'cp437'

def read_dropfile(arg):
    """Returns a dict with at least 'name' and 'handle'. Looks for BBSDEV.DRP
    first (BBSDEV_DRP environment variable, or a BBSDEV.DRP path/folder on the
    command line), then DOOR32.SYS, then DOOR.SYS. Accepts a file path, a node
    directory, or Mystic-style '.../door32' without extension."""
    info = {'name': None, 'handle': None}
    tried = []
    env = os.environ.get('BBSDEV_DRP')
    if env:
        tried.append(env)
    if arg:
        if os.path.isdir(arg):
            tried += [os.path.join(arg, n) for n in ('BBSDEV.DRP', 'bbsdev.drp')]
        elif os.path.basename(arg).lower() == 'bbsdev.drp':
            tried.append(arg)
    for p in tried:
        if os.path.isfile(p):
            try:
                return parse_bbsdev(p)
            except (DropFileError, OSError) as e:
                print(f"BBSDEV.DRP ignored ({p}): {e}", file=sys.stderr)
    if not arg:
        return info
    dirs = [arg] if os.path.isdir(arg) else [os.path.dirname(arg) or '.']
    d32 = [] if os.path.isdir(arg) else [arg, arg + '.sys', arg + '.SYS']
    d32 += [os.path.join(dirs[0], n) for n in ('door32.sys', 'DOOR32.SYS')]
    for p in d32:
        if os.path.isfile(p) and os.path.basename(p).lower().startswith('door32'):
            try:
                l = _read_lines(p)
            except OSError:
                continue
            # 1 comm type (0 local, 1 serial, 2 telnet), 2 handle, 6 real name, 7 alias
            if len(l) >= 2 and l[0] == '2' and l[1].isdigit():
                info['handle'] = int(l[1])
            if len(l) >= 7:
                info['name'] = l[6] or l[5] or None
            return info
    for p in [arg] + [os.path.join(dirs[0], n) for n in ('DOOR.SYS', 'door.sys')]:
        if os.path.isfile(p) and os.path.basename(p).lower() == 'door.sys':
            try:
                l = _read_lines(p)
            except OSError:
                continue
            # line 10 = full name, line 36 = alias (52-line DOOR.SYS)
            info['name'] = (l[35] if len(l) >= 36 and l[35] else (l[9] if len(l) >= 10 else None))
            return info
    return info

# ------------------------------------------------------------------ terminal check
def detect_terminal(conn, wait=1.5):
    """Ask the terminal what it is. Returns dict:
       sixel: True/False/None (None = no answer), cterm: (major, minor) for
       SyncTERM's CTerm or None, leftover: unconsumed input bytes.
    SyncTERM answers CSI c with CSI = 67;84;101;114;109;<rev> c ("CTerm" + revision)
    and CSI < c with CSI < 0;... c where 4 = pixel operations (sixel)."""
    conn.write(b'\x1b[c\x1b[<c')
    buf, end = b'', time.monotonic() + wait
    got_da = got_cterm = False
    info = {'sixel': False, 'cterm': None}
    while time.monotonic() < end and not (got_da and got_cterm):
        data = conn.read(end - time.monotonic())
        if data is None:
            break
        buf += data
        while True:
            i = buf.find(b'\x1b[')
            if i < 0:
                break
            j = i + 2
            while j < len(buf) and (48 <= buf[j] <= 63):
                j += 1
            if j >= len(buf):
                break
            if buf[j:j + 1] == b'c':
                body = buf[i + 2:j].decode('ascii', 'replace')
                params = body[1:].split(';') if body[:1] in '?<=' else body.split(';')
                if body.startswith('<'):
                    got_cterm = True
                    if '4' in params[1:]:
                        info['sixel'] = True
                elif body.startswith('='):
                    got_da = True
                    if params[:5] == ['67', '84', '101', '114', '109']:
                        try:
                            info['cterm'] = (int(params[5]), int(params[6]) if len(params) > 6 else 0)
                        except (ValueError, IndexError):
                            info['cterm'] = (0, 0)
                elif body.startswith('?'):
                    got_da = True
                    if '4' in params[1:]:
                        info['sixel'] = True
                buf = buf[:i] + buf[j + 1:]
            else:
                break
    if not (got_da or got_cterm):
        info['sixel'] = None
    info['leftover'] = buf
    return info

def detect_sixel(conn, wait=1.5):
    """Backward-compatible wrapper."""
    info = detect_terminal(conn, wait)
    return info['sixel'], info['leftover']
