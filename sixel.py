"""Small sixel encoder for game graphics (few colors per image).

Color registers are kept STABLE for the whole session: a given RGB color always
uses the same register number. Some terminals (SyncTERM among them) share color
registers between images, so re-numbering colors in each new image would
repaint pixels already on screen - visible as flicker or colors swapping.
Registers start at 16 so the 16 standard text colors are never touched.

The work is done with whole-array numpy operations: Python only loops over the
colors and over the (band, color) rows of the output, never over pixels or
runs, so a full NES frame encodes in a few milliseconds.
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
    h0, w0 = frame.shape[:2]
    h, w = int(h0 * scale), int(w0 * xscale)
    if not (h and w):                       # nothing to draw
        return b'\x1bP0;1;0q"1;1;%d;%d%s\x1b\\' % (w, h, b'-' * (-(-h // 6)))
    # Colors are found on the unscaled frame (fewer pixels), then the color
    # index image is scaled - the same nearest-neighbour picture as scaling first.
    packed = (frame[..., 0].astype(np.uint32) << 16) | (frame[..., 1].astype(np.uint32) << 8) | frame[..., 2]
    colors = np.unique(packed)
    idx = np.searchsorted(colors, packed)
    if scale != 1 or xscale != 1:
        ys = (np.arange(h) / scale).astype(int)
        xs = (np.arange(w) / xscale).astype(int)
        idx = idx[ys][:, xs]
        if not (_covers(ys, h0) and _covers(xs, w0)):
            used = np.bincount(idx.ravel(), minlength=len(colors)) > 0
            if not used.all():              # a dropped row or column took a color with it
                idx = (np.cumsum(used) - 1)[idx]
                colors = colors[used]
    ncolors = len(colors)
    regs = [_register(int(c)) for c in colors]
    out = [b'\x1bP0;1;0q"1;1;%d;%d' % (w, h)]
    for n, c in enumerate(colors):
        r, g, b = (int(c) >> 16) & 255, (int(c) >> 8) & 255, int(c) & 255
        out.append(b'#%d;2;%d;%d;%d' % (regs[n], round(r * 100 / 255), round(g * 100 / 255), round(b * 100 / 255)))

    # codes[band, color, x]: the 6-bit sixel for that color in that column.
    # Padding rows below the picture get color |ncolors|, which is dropped.
    bands = -(-h // 6)
    pad = bands * 6 - h
    if pad:
        idx = np.vstack([idx, np.full((pad, w), ncolors, idx.dtype)])
    idx = idx.reshape(bands, 6, w)
    xi = np.arange(w)[None, :]
    base = np.arange(bands)[:, None] * ((ncolors + 1) * w) + xi
    codes = np.zeros(bands * (ncolors + 1) * w, np.uint8)
    for bit in range(6):
        codes[base + idx[:, bit, :] * w] |= np.uint8(1 << bit)
    codes = codes.reshape(bands, ncolors + 1, w)[:, :ncolors]

    # One output row per (band, color) that has any pixels, in band then color
    # order, trimmed after its last pixel.
    nonzero = codes != 0
    present = nonzero.any(axis=2)
    last = w - 1 - np.argmax(nonzero[:, :, ::-1], axis=2)
    keep = present[:, :, None] & (xi[None] <= last[:, :, None])
    row_band, row_color = np.nonzero(present)
    row_len = last[row_band, row_color] + 1
    body, row_start = _rle_rows(codes[keep] + 63, row_len)

    prev_band = 0
    for i, (band, color) in enumerate(zip(row_band.tolist(), row_color.tolist())):
        if band != prev_band:
            out.append(b'-')                 # next band
            prev_band = band
        elif i:
            out.append(b'$')                 # carriage return within band
        out.append(b'#%d' % regs[color])
        out.append(body[row_start[i]:row_start[i + 1]])
    out.append(b'-\x1b\\')
    return b''.join(out)

def _covers(picks, n):
    """Do the scaled |picks| (ascending) include every one of 0..n-1?"""
    return len(picks) > 0 and picks[0] == 0 and picks[-1] == n - 1 and bool((np.diff(picks) <= 1).all())

def _rle_rows(data, row_len):
    """Run-length encode the concatenated sixel rows in |data| (uint8 sixel
    characters), whose lengths are |row_len|. A run of more than 3 becomes
    '!<count><char>', shorter runs are written out. Runs never cross rows.
    Returns (bytes, byte offset of each row's start plus one past the end)."""
    n = len(data)
    row_first = np.concatenate([[0], np.cumsum(row_len)[:-1]])
    starts = np.ones(n, bool)
    starts[1:] = data[1:] != data[:-1]
    starts[row_first] = True
    starts = np.flatnonzero(starts)
    runs = np.diff(np.append(starts, n))
    chars = data[starts]

    long_ = runs > 3
    digits = np.ones(len(runs), np.int64)
    for p in range(1, len(str(int(runs.max())))):
        digits += runs >= 10 ** p
    size = np.where(long_, digits + 2, runs)
    at = np.cumsum(size) - size
    buf = np.empty(int(size.sum()), np.uint8)

    s = ~long_                              # short runs: the character, repeated
    rep = runs[s]
    first = np.repeat(at[s] - (np.cumsum(rep) - rep), rep)
    buf[first + np.arange(int(rep.sum()))] = np.repeat(chars[s], rep)

    at_l, runs_l, digits_l = at[long_], runs[long_], digits[long_]
    buf[at_l] = ord('!')
    for d in range(int(digits_l.max(initial=0))):
        has = digits_l > d                  # digit d, counting from the left
        place = 10 ** (digits_l[has] - 1 - d)
        buf[at_l[has] + 1 + d] = ord('0') + (runs_l[has] // place) % 10
    buf[at_l + 1 + digits_l] = chars[long_]

    first_run = np.searchsorted(starts, row_first)
    row_start = np.append(at[first_run], len(buf)).tolist()
    return buf.tobytes(), row_start
