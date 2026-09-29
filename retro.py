"""
Minimal libretro frontend (ctypes). Loads an emulator core (.so/.dll), runs
frames, and hands back the picture as an RGB array and the audio as int16
stereo samples. Enough for NES/C64/etc. software-rendered cores.
"""
import ctypes as C, os, numpy as np

class retro_game_info(C.Structure):
    _fields_ = [('path', C.c_char_p), ('data', C.c_void_p), ('size', C.c_size_t), ('meta', C.c_char_p)]
class retro_system_timing(C.Structure):
    _fields_ = [('fps', C.c_double), ('sample_rate', C.c_double)]
class retro_game_geometry(C.Structure):
    _fields_ = [('base_width', C.c_uint), ('base_height', C.c_uint), ('max_width', C.c_uint),
                ('max_height', C.c_uint), ('aspect_ratio', C.c_float)]
class retro_system_av_info(C.Structure):
    _fields_ = [('geometry', retro_game_geometry), ('timing', retro_system_timing)]

ENV_T = C.CFUNCTYPE(C.c_bool, C.c_uint, C.c_void_p)
VIDEO_T = C.CFUNCTYPE(None, C.c_void_p, C.c_uint, C.c_uint, C.c_size_t)
AUDIO_T = C.CFUNCTYPE(None, C.c_int16, C.c_int16)
AUDIOB_T = C.CFUNCTYPE(C.c_size_t, C.POINTER(C.c_int16), C.c_size_t)
POLL_T = C.CFUNCTYPE(None)
STATE_T = C.CFUNCTYPE(C.c_int16, C.c_uint, C.c_uint, C.c_uint, C.c_uint)

class retro_variable(C.Structure):
    _fields_ = [('key', C.c_char_p), ('value', C.c_char_p)]

# Core options we answer; unknown ones get the core's own default (we return False).
DEFAULT_OPTIONS = {
    'fceumm_sndvolume': '10', 'fceumm_sndquality': 'Low', 'fceumm_sndlowpass': 'disabled',
    'fceumm_apu_1': 'enabled', 'fceumm_apu_2': 'enabled', 'fceumm_apu_3': 'enabled',
    'fceumm_apu_4': 'enabled', 'fceumm_apu_5': 'enabled', 'fceumm_apu_fds': 'enabled',
    'fceumm_apu_s5b': 'enabled', 'fceumm_apu_n163': 'enabled', 'fceumm_apu_vrc6': 'enabled',
    'fceumm_apu_vrc7': 'enabled', 'fceumm_apu_mmc5': 'enabled', 'fceumm_region': 'Auto',
    'fceumm_palette': 'default', 'fceumm_up_down_allowed': 'disabled',
    'fceumm_overscan_v_top': '0', 'fceumm_overscan_v_bottom': '0',
    'fceumm_overscan_h_left': '0', 'fceumm_overscan_h_right': '0',
}

# RetroPad button ids
B, Y, SELECT, START, UP, DOWN, LEFT, RIGHT, A = 0, 1, 2, 3, 4, 5, 6, 7, 8

class Core:
    def __init__(self, core_path, rom_path=None, workdir='.'):
        self.lib = C.CDLL(os.path.abspath(core_path))
        self.workdir = os.path.abspath(workdir).encode()
        self.fmt = 0                                   # 0 = 0RGB1555, 1 = XRGB8888, 2 = RGB565
        self.frame = None
        self.audio = []
        self.buttons = 0                               # bitmask of RetroPad ids, controller 1
        self.buttons2 = 0                              # controller 2
        self.options = dict(DEFAULT_OPTIONS)
        self._optbufs = {}
        self.loaded = False
        self._keep = [ENV_T(self._env), VIDEO_T(self._video), AUDIO_T(self._sample),
                      AUDIOB_T(self._batch), POLL_T(lambda: None), STATE_T(self._state)]
        L = self.lib
        L.retro_set_environment(self._keep[0])
        L.retro_set_video_refresh(self._keep[1])
        L.retro_set_audio_sample(self._keep[2])
        L.retro_set_audio_sample_batch(self._keep[3])
        L.retro_set_input_poll(self._keep[4])
        L.retro_set_input_state(self._keep[5])
        L.retro_load_game.argtypes = [C.POINTER(retro_game_info)]
        L.retro_load_game.restype = C.c_bool
        L.retro_init()
        if rom_path:
            self.load(rom_path)

    def load(self, rom_path):
        """Load a game (unloading any previous one). The core stays initialized."""
        self.unload()
        self.rom = open(rom_path, 'rb').read()
        self._rombuf = C.create_string_buffer(self.rom, len(self.rom))
        gi = retro_game_info(os.path.abspath(rom_path).encode(), C.cast(self._rombuf, C.c_void_p), len(self.rom), None)
        if not self.lib.retro_load_game(C.byref(gi)):
            raise RuntimeError("core could not load this ROM")
        self.loaded = True
        self.frame, self.audio, self.buttons, self.buttons2 = None, [], 0, 0
        av = retro_system_av_info()
        self.lib.retro_get_system_av_info(C.byref(av))
        self.fps, self.sample_rate = av.timing.fps, av.timing.sample_rate

    def unload(self):
        if self.loaded:
            self.lib.retro_unload_game()
            self.loaded = False

    def _env(self, cmd, data):
        cmd &= 0xFFFF
        if cmd == 10:                                  # SET_PIXEL_FORMAT
            fmt = C.cast(data, C.POINTER(C.c_int))[0]
            if fmt in (0, 1, 2):
                self.fmt = fmt; return True
            return False
        if cmd in (9, 31):                             # GET_SYSTEM_DIRECTORY / GET_SAVE_DIRECTORY
            C.cast(data, C.POINTER(C.c_char_p))[0] = C.c_char_p(self.workdir)
            return True
        if cmd == 15:                                  # GET_VARIABLE
            var = C.cast(data, C.POINTER(retro_variable))[0]
            key = (var.key or b'').decode('latin-1')
            if key in self.options:
                buf = self._optbufs.setdefault(key, C.create_string_buffer(self.options[key].encode()))
                C.cast(data, C.POINTER(retro_variable))[0].value = C.cast(buf, C.c_char_p)
                return True
            return False
        if cmd == 47:                                  # GET_AUDIO_VIDEO_ENABLE: both on
            C.cast(data, C.POINTER(C.c_int))[0] = 3; return True
        if cmd == 3:                                   # GET_CAN_DUPE
            C.cast(data, C.POINTER(C.c_bool))[0] = True; return True
        return False

    def _video(self, data, w, h, pitch):
        if not data:
            return                                     # duped frame: keep the last one
        if self.fmt == 1:
            a = np.ctypeslib.as_array(C.cast(data, C.POINTER(C.c_uint8)), (h, pitch))
            px = a[:, :w * 4].reshape(h, w, 4)
            self.frame = px[:, :, [2, 1, 0]].copy()
        else:
            a = np.ctypeslib.as_array(C.cast(data, C.POINTER(C.c_uint16)), (h, pitch // 2))[:, :w].astype(np.uint32)
            if self.fmt == 2:
                r, g, b = (a >> 11) & 31, (a >> 5) & 63, a & 31
                self.frame = np.dstack([r * 255 // 31, g * 255 // 63, b * 255 // 31]).astype(np.uint8)
            else:
                r, g, b = (a >> 10) & 31, (a >> 5) & 31, a & 31
                self.frame = np.dstack([r * 255 // 31, g * 255 // 31, b * 255 // 31]).astype(np.uint8)

    def _sample(self, l, r):
        self.audio.append(np.array([[l, r]], np.int16))

    def _batch(self, data, frames):
        self.audio.append(np.ctypeslib.as_array(data, (frames * 2,)).reshape(frames, 2).copy())
        return frames

    def _state(self, port, device, index, id_):
        if port > 1 or device != 1:                    # joypads 1 and 2 only
            return 0
        b = self.buttons if port == 0 else self.buttons2
        if id_ == 256:                                 # JOYPAD_MASK
            return b
        return 1 if b & (1 << id_) else 0

    def step(self):
        self.lib.retro_run()
        return self.frame

    def reset(self):
        """Like pressing the console's RESET button."""
        self.lib.retro_reset()

    # ---- save states (a snapshot of the whole machine)
    def save_state(self):
        L = self.lib
        L.retro_serialize_size.restype = C.c_size_t
        n = L.retro_serialize_size()
        if not n:
            return None
        buf = C.create_string_buffer(n)
        L.retro_serialize.argtypes = [C.c_void_p, C.c_size_t]
        L.retro_serialize.restype = C.c_bool
        return buf.raw if L.retro_serialize(buf, n) else None

    def load_state(self, data):
        L = self.lib
        L.retro_unserialize.argtypes = [C.c_char_p, C.c_size_t]
        L.retro_unserialize.restype = C.c_bool
        return bool(data) and L.retro_unserialize(data, len(data))

    # ---- battery-backed cartridge RAM (the game's own save, e.g. Zelda's file)
    def _sram(self):
        L = self.lib
        L.retro_get_memory_data.restype = C.c_void_p
        L.retro_get_memory_data.argtypes = [C.c_uint]
        L.retro_get_memory_size.restype = C.c_size_t
        L.retro_get_memory_size.argtypes = [C.c_uint]
        return L.retro_get_memory_data(0), L.retro_get_memory_size(0)

    def sram_get(self):
        p, n = self._sram()
        return C.string_at(p, n) if p and n else None

    def sram_set(self, data):
        p, n = self._sram()
        if p and n and data:
            C.memmove(p, data, min(n, len(data)))
            return True
        return False

    def take_audio(self):
        """All audio produced since the last call, as int16 array (n, 2)."""
        if not self.audio:
            return np.zeros((0, 2), np.int16)
        a = np.concatenate(self.audio); self.audio = []
        return a

    def close(self):
        try:
            self.unload(); self.lib.retro_deinit()
        except Exception:
            pass
