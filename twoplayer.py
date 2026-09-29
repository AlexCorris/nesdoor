"""
Two-player link between doors on the same machine.

The HOST door runs the emulator and advertises its game in ./sessions.
A second caller picks it from the game list; their door (the GUEST) connects,
sends controller-2 buttons, and receives exactly what the host's caller sees
(the same sixel frames and audio), which it passes straight to its terminal.

Wire format: 1-byte type + 4-byte big-endian length + payload.
  guest -> host:  H name | B 2-byte button mask | Q
  host -> guest:  W game title | F (full) | T terminal bytes | X (host left)
"""
import json, os, queue, select, socket, struct, threading, time

def pack(t, payload=b''):
    return t + struct.pack('>I', len(payload)) + payload

class Reader:
    def __init__(self):
        self.buf = b''
    def feed(self, data):
        self.buf += data
        out = []
        while len(self.buf) >= 5:
            n = struct.unpack('>I', self.buf[1:5])[0]
            if len(self.buf) < 5 + n:
                break
            out.append((self.buf[:1], self.buf[5:5 + n]))
            self.buf = self.buf[5 + n:]
        return out

def _alive(pid):
    """Is that process still running? (Never use os.kill(pid, 0) on Windows: it kills.)"""
    if not pid:
        return False
    if os.name == 'nt':
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))          # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259                  # STILL_ACTIVE
    try:
        os.kill(pid, 0); return True
    except PermissionError:
        return True
    except OSError:
        return False

def list_sessions(folder):
    out = []
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for fn in names:
        if not fn.endswith('.json'):
            continue
        p = os.path.join(folder, fn)
        try:
            d = json.load(open(p))
        except (OSError, ValueError):
            continue
        if not _alive(d.get('pid', 0)) or d.get('guest'):
            if not _alive(d.get('pid', 0)):
                try:
                    os.remove(p)
                except OSError:
                    pass
            continue
        out.append(d)
    return sorted(out, key=lambda d: d.get('started', 0))

class Host:
    def __init__(self, folder, name, game):
        self.folder, self.name, self.game = folder, name, game
        self.ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.ls.bind(('127.0.0.1', 0)); self.ls.listen(1); self.ls.setblocking(False)
        self.port = self.ls.getsockname()[1]
        self.client, self.reader = None, Reader()
        self.guest, self.buttons, self.just_joined, self.just_left = None, 0, False, False
        self.q = queue.Queue(maxsize=90)
        os.makedirs(folder, exist_ok=True)
        self.path = os.path.join(folder, f'{os.getpid()}.json')
        self._advertise()
        threading.Thread(target=self._writer, daemon=True).start()

    def _advertise(self):
        d = {'pid': os.getpid(), 'port': self.port, 'host': self.name, 'game': self.game,
             'started': time.time(), 'guest': self.guest}
        try:
            with open(self.path, 'w') as f:
                json.dump(d, f)
        except OSError:
            pass

    def _writer(self):
        while True:
            item = self.q.get()
            if item is None:
                return
            c, data = item
            try:
                c.sendall(data)
            except OSError:
                pass

    def poll(self):
        """Accept a guest, read their buttons. Call every frame."""
        self.just_joined = self.just_left = False
        if self.client is None:
            try:
                c, _ = self.ls.accept()
                c.setblocking(False)
                self.client, self.reader = c, Reader()
            except (BlockingIOError, OSError):
                return
        try:
            r, _, _ = select.select([self.client], [], [], 0)
            if not r:
                return
            data = self.client.recv(4096)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            data = b''
        if not data:
            self._drop(); return
        for t, p in self.reader.feed(data):
            if t == b'H':
                if self.guest is None:
                    self.guest = p.decode('utf-8', 'replace')[:20] or 'Player 2'
                    self.just_joined = True
                    self._direct(pack(b'W', self.game.encode('utf-8', 'replace')))
                    self._advertise()
            elif t == b'B' and len(p) == 2:
                self.buttons = struct.unpack('>H', p)[0]
            elif t == b'Q':
                self._drop(); return

    def _direct(self, data):
        try:
            self.client.setblocking(True); self.client.sendall(data); self.client.setblocking(False)
        except OSError:
            pass

    def _drop(self):
        try:
            self.client.close()
        except OSError:
            pass
        was = self.guest
        self.client, self.guest, self.buttons = None, None, 0
        self.just_left = was is not None
        self._advertise()

    def send(self, data, important=False):
        """Mirror terminal output to the guest. Frames may be dropped if they fall behind."""
        if self.client is None or self.guest is None:
            return
        item = (self.client, pack(b'T', data))
        try:
            if important:
                self.q.put(item, timeout=1)
            else:
                self.q.put_nowait(item)
        except queue.Full:
            pass

    def close(self):
        try:
            os.remove(self.path)
        except OSError:
            pass
        if self.client:
            self._direct(pack(b'X'))
            try:
                self.client.close()
            except OSError:
                pass
        self.q.put(None)
        self.ls.close()

class Guest:
    def __init__(self, port, name):
        self.s = socket.create_connection(('127.0.0.1', port), timeout=3)
        self.s.sendall(pack(b'H', name.encode('utf-8', 'replace')))
        self.reader, self.q = Reader(), queue.Queue()
        self.game = None
        self.s.settimeout(3)
        buf = b''
        end = time.monotonic() + 3
        while self.game is None and time.monotonic() < end:
            data = self.s.recv(4096)
            if not data:
                raise OSError("host closed")
            for t, p in self.reader.feed(data):
                if t == b'W':
                    self.game = p.decode('utf-8', 'replace')
                elif t == b'F':
                    raise OSError("game is full")
                else:
                    self.q.put((t, p))
        if self.game is None:
            raise OSError("no answer from host")
        self.s.settimeout(None)
        threading.Thread(target=self._rd, daemon=True).start()
    def _rd(self):
        try:
            while True:
                data = self.s.recv(65536)
                if not data:
                    break
                for m in self.reader.feed(data):
                    self.q.put(m)
        except OSError:
            pass
        self.q.put((b'X', b''))
    def buttons(self, mask):
        try:
            self.s.sendall(pack(b'B', struct.pack('>H', mask)))
        except OSError:
            pass
    def messages(self):
        out = []
        while True:
            try:
                out.append(self.q.get_nowait())
            except queue.Empty:
                return out
    def close(self):
        try:
            self.s.sendall(pack(b'Q')); self.s.close()
        except OSError:
            pass
