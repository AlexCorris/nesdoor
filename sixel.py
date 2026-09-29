"""Small sixel encoder for game graphics (few colors per image).

Color registers are kept STABLE for the whole session: a given RGB color always
uses the same register number. Some terminals (SyncTERM among them) share color
registers between images, so re-numbering colors in each new image would
repaint pixels already on screen - visible as flicker or colors swapping.
Registers start at 16 so the 16 standard text colors are never touched.
"""
import numpy as np

FIRST_REG, LAST_REG = 16, 255
_reg = {}                                   # packed RGB -> register number

def _register(c):
    r = _reg.get(c)
    if r is None:
        if len(_reg) >= LAST_REG - FIRST_REG + 1:
            _reg.clear()                    # palette full (very unusual): start over
        r = _reg[c] = FIRST_REG + len(_reg)
    return r

def encode(frame, scale=1, xscale=None):
    """frame: HxWx3 uint8 array. scale = vertical (and horizontal unless xscale
    is given). Returns sixel bytes (DCS ... ST)."""
    xscale = scale if xscale is None else xscale
    if scale != 1 or xscale != 1:
        h0, w0 = frame.shape[:2]
        ys = (np.arange(int(h0 * scale)) / scale).astype(int)
        xs = (np.arange(int(w0 * xscale)) / xscale).astype(int)
        frame = frame[ys][:, xs]
    h, w, _ = frame.shape
    packed = (frame[..., 0].astype(np.uint32) << 16) | (frame[..., 1].astype(np.uint32) << 8) | frame[..., 2]
    colors, idx = np.unique(packed, return_inverse=True)
    idx = idx.reshape(h, w)
    regs = [_register(int(c)) for c in colors]
    pad = (-h) % 6
    if pad:
        idx = np.vstack([idx, np.full((pad, w), -1, idx.dtype)])
    out = [b'\x1bP0;1;0q"1;1;%d;%d' % (w, h)]
    for n, c in enumerate(colors):
        r, g, b = (int(c) >> 16) & 255, (int(c) >> 8) & 255, int(c) & 255
        out.append(b'#%d;2;%d;%d;%d' % (regs[n], round(r * 100 / 255), round(g * 100 / 255), round(b * 100 / 255)))
    weights = (1 << np.arange(6)).reshape(6, 1)
    for band in range(idx.shape[0] // 6):
        rows = idx[band * 6:band * 6 + 6]
        present = np.unique(rows[rows >= 0])
        first = True
        for n in present:
            bits = ((rows == n) * weights).sum(0)          # 0..63 per column
            nz = np.nonzero(bits)[0]
            if not first:
                out.append(b'$')                             # carriage return within band
            first = False
            out.append(b'#%d' % regs[n])
            out.append(_rle((bits[:nz[-1] + 1] + 63).astype(np.uint8).tobytes()))
        out.append(b'-')                                     # next band
    out.append(b'\x1b\\')
    return b''.join(out)

def _rle(data):
    res = bytearray()
    i, n = 0, len(data)
    while i < n:
        j, c = i, data[i]
        while j < n and data[j] == c:
            j += 1
        run = j - i
        res += (b'!%d' % run + bytes([c])) if run > 3 else bytes([c]) * run
        i = j
    return bytes(res)
