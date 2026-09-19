# ============================================================
#  AI CHAT - M5Stack Cardputer (UIFlow2 / MicroPython)
#  Chat client for OpenAI-compatible APIs (/chat/completions)
#
#  Configuration (BASE_URL, TOKEN, MODEL, ...) lives in:
#      /flash/res/chat_config.txt
#  This program never overwrites that file; put your TOKEN there.
#
#  Keyboard: the MatrixKeyboard callback only fills a small queue; all
#  drawing and sound happen in loop(). If the callback never delivers a
#  code, the program falls back to polling get_key(), and tick() is called
#  whenever the driver provides it (CardKB/Tab5 need it).
#  IMPORTANT: after boot the Cardputer keyboard sleeps (low-power). Hold FN
#  and press a key so events start arriving - this is by design.
#  Header: W/- = WiFi, xx% = battery, K<n> = keys received,
#          KX = keyboard object failed to init, dot 1 = API status,
#          dot 2 = web configuration server.
#
#  Modes:
#    CHAT MODE (default)    : AI chat only. The web module is not loaded at
#                             all, keeping the heap free for the TLS handshake.
#    CONFIG MODE (button A) : start a small web server so BASE_URL/TOKEN/
#                             MODEL can be edited from a browser. Press BtnA
#                             again to go back to chat.
#
#  The API is tested at power-on and whenever chat mode is (re)entered, never
#  while config mode is running: that keeps the config screen instant and
#  leaves the heap free for the web server.
#
#  The web configuration server lives in its own file, webcfg.py, which is
#  uploaded next to the program as res/webcfg.py. It is loaded only in config
#  mode and released again on the way back to chat, so chat mode keeps the
#  heap free for the TLS handshake. Upload BOTH files.
#
#  IMPORTANT NOTE: requests2 is blocking and has no official timeout, so a
#  single API call can freeze the device. That is why the API test runs
#  when entering config mode, not automatically on every boot.
# ============================================================
import os
import time

import M5
from M5 import *

# ---- optional imports, wrapped so other firmwares do not crash ----
try:
    import json
except ImportError:
    json = None

try:
    import network
except ImportError:
    network = None

try:
    import requests2
except ImportError:
    requests2 = None

try:
    import gc
except ImportError:
    gc = None

try:
    import socket
except ImportError:
    socket = None

try:
    from hardware import MatrixKeyboard
except ImportError:
    MatrixKeyboard = None

try:
    from unit import KeyCode
except ImportError:
    class KeyCode:
        KEYCODE_ENTER = 0x0D


# ============================================================
#  CONFIGURATION (defaults, overridden by chat_config.txt)
# ============================================================
CFG_NAME = "chat_config.txt"
CFG_PATH = "/flash/res/" + CFG_NAME
CFG_PATHS = (CFG_PATH, "/flash/res/" + CFG_NAME, "/sd/" + CFG_NAME)

BASE_URL = "https://api.openai.com/v1"
TOKEN = ""
MODEL = "gpt-4o-mini"
SYSTEM_PROMPT = "You are a helpful assistant. Answer briefly."
MAX_TOKENS = 320
HISTORY_LIMIT = 8          # exchanges kept on screen and in memory
HIST_CHARS = 2400          # max characters of history sent per request
MEM_MIN = 26000            # below this free heap, old lines are dropped before sending
WIFI_SSID = ""             # optional; leave empty when WiFi is already set up by the firmware
WIFI_PASS = ""

CONFIG_STR = ("BASE_URL", "TOKEN", "MODEL", "SYSTEM_PROMPT", "WIFI_SSID", "WIFI_PASS")


# ============================================================
#  COLORS & GEOMETRY (filled in during setup)
# ============================================================
C_BG = 0x000000
C_HDR = 0x101833
C_TITLE = 0x3CC7F1
C_DIM = 0x8A94A6
C_OK = 0x33FF66
C_WARN = 0xFFCC00
C_ERR = 0xFF5555
C_USER = 0x7FE7FF
C_AI = 0xFFFFFF
C_BAD = 0xFF8A8A
C_INPUT_BG = 0x1A1A1A
C_INPUT = 0x00FF99
C_STATUS_BG = 0x101010

W = 240
H = 135
HDR_H = 13
CHAT_Y = 13
INPUT_H = 16
STATUS_H = 15
CHAT_H = 91
CHAT_ROWS = 6
LH = 15
CH_W = 6
CHAT_W = 224
IN_CAP = 36
IN_BUF_MAX = 240
INPUT_Y = 104
STATUS_Y = 120
DOT_X = 50                 # API status dot in the header


# ============================================================
#  STATE
# ============================================================
S_TYPE = 0
S_WAIT = 1
S_READ = 2

STATE = S_TYPE
IN_BUF = ""
HIST = []             # list of (tag, text): "U" user, "A" ai, "!" error
VIEW = []             # HIST wrapped into lines for the chat area
READ_LINES = []
READ_PAGE = 0
READ_PAGES = 1
PENDING = False

KB = None
KB_EVENTS = 0
KB_ERR = ""
LAST_CODE = -1
WLAN = None

KEY_Q = []            # raw keycode queue (filled by callback / polling)
CB_SEEN = False       # callback delivered a code -> polling disabled
KEY_EVENTS = 0        # number of keys actually processed
POLL_MS = 12          # keyboard polling interval (ms) while the callback is silent
LAST_POLL = 0
TICK_OK = False       # driver has tick() and it was called fine
HINTED = False        # wake-up hint already shown
START_T = 0

BEEP_ON = True
BEEP_VOL = 110
SPK_OK = False
SPK_ERR = 0
BEEP_CH = 7           # dedicated beep channel so tones do not pile up
NOTE = (523, 587, 659, 784, 880, 1047)   # pentatonic C5..C6

API_STATE = 0         # 0 not checked yet, 1 OK, 2 error
HTTP_TMO = True       # try passing the timeout argument to requests2?

MODE_CHAT = 0         # normal mode: AI chat only
MODE_CONFIG = 1       # config mode: web server running
MODE = MODE_CHAT
WCM = None             # webcfg module while config mode is on (None in chat)
WEB_MSG = ""           # last message from the web server
CFG_MSG = ""          # API test result shown when entering config mode
CFG_STATE = 0         # 0 preparing, 1 web server running, 2 failed
BTN = None            # BtnA object when the firmware provides one
BTN_DOWN = False      # previous pressed state (edge detection)
BTN_LAST = None       # last action time (debounce); None = never pressed
BTN_SEEN = False      # has the button ever really been pressed?
NET_MSG = ""          # DNS/TCP diagnosis result

DIRTY_HDR = True
DIRTY_CHAT = True
DIRTY_INPUT = True
DIRTY_STATUS = True

STATUS_TXT = ""
STATUS_COL = C_DIM

FONT = None
FONT_NAME = ""
_TW = None
SPEC_BACK = ()
SPEC_ESC = ()
SPEC_ENTER = ()
SPEC_UP = ()
SPEC_DOWN = ()
SPEC_LEFT = ()
SPEC_RIGHT = ()
KEY_ENTER = 0x0D

LAST_TICK = 0
LAST_PING = 0

PRESET_HELP = "Enter send | ESC clear | /clear | /model"

# ASCII folding. The Latin-1 map is packed into one 64-character string
# (0xC0..0xFF); '.' means that character is dropped. A full dict would cost
# several KB of heap, and heap here is very tight.
_FOLD1 = ("AAAAAA.CEEEEIIII.NOOOOO..UUUUY.."
          "aaaaaa.ceeeeiiii.nooooo..uuuuy.y")
_FOLD_HI = {
    0xA0: " ", 0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"',
    0x2013: "-", 0x2014: "-", 0x2026: "...", 0x20AC: "EUR",
}


def fold_ascii(s):
    """The built-in font is ASCII only -> fold accents, drop the rest."""
    out = []
    for ch in str(s):
        o = ord(ch)
        if o < 0x80:
            out.append(ch)
        elif 0xC0 <= o <= 0xFF:
            r = _FOLD1[o - 0xC0]
            if r != ".":
                out.append(r)
        else:
            r = _FOLD_HI.get(o)
            if r is not None:
                out.append(r)
    return "".join(out)


def short_err(e):
    t = fold_ascii(str(e)).replace("\n", " ")
    if len(t) > 60:
        t = t[:60]
    return t


def mem_collect():
    """Collect garbage before HTTPS calls: TLS needs one large memory block."""
    if gc is None:
        return
    try:
        gc.collect()
    except Exception:
        pass


def mem_free():
    if gc is None:
        return -1
    try:
        return gc.mem_free()
    except Exception:
        return -1


def mem_txt():
    m = mem_free()
    if m < 0:
        return "?"
    return str(m // 1024) + " KB"


def mem_hdr():
    """Free heap shown in the header (e.g. '58k') - this number decides
    whether the TLS handshake still fits."""
    m = mem_free()
    if m < 0:
        return "-"
    return str(m // 1024) + "k"


# ============================================================
#  SOUND: variative beeps
# ============================================================
def spk_init():
    global SPK_OK
    try:
        SPK_OK = bool(Speaker.begin())
    except Exception as e:
        SPK_OK = False
        print("speaker:", e)
        return
    try:
        Speaker.setVolume(BEEP_VOL)
    except Exception:
        pass


def beep(freq, dur, cut=True):
    """Short non-blocking tone. cut=True stops the previous tone."""
    global SPK_OK, SPK_ERR
    if (not BEEP_ON) or (not SPK_OK):
        return
    try:
        Speaker.tone(int(freq), int(dur), BEEP_CH, cut)
        return
    except TypeError:
        pass
    except Exception:
        SPK_ERR += 1
        if SPK_ERR >= 3:
            SPK_OK = False
        return
    # firmware without the channel argument
    try:
        Speaker.tone(int(freq), int(dur))
    except Exception:
        SPK_ERR += 1
        if SPK_ERR >= 3:
            SPK_OK = False


def beep_key(code):
    """Every letter key gets its own tone (pentatonic, never dissonant)."""
    if code == 0x20:
        beep(392, 26)
    else:
        beep(NOTE[code % 6], 22)


def beep_enter():
    beep(784, 40, True)
    beep(1175, 70, False)      # False = wait for the previous tone to finish


def beep_del():
    beep(330, 28, True)


def beep_esc():
    beep(262, 45, True)


def beep_page(up):
    if up:
        beep(988, 20, True)
    else:
        beep(740, 20, True)


def beep_answer():
    beep(784, 55, True)
    beep(988, 55, False)
    beep(1319, 90, False)


def beep_error():
    beep(196, 90, True)
    beep(165, 140, False)


def beep_ready():
    beep(659, 45, True)
    beep(988, 70, False)


# ============================================================
#  CONFIG FILE: read KEY = VALUE lines
# ============================================================
def _read_text(path):
    try:
        f = open(path, "r")
        try:
            return f.read()
        finally:
            f.close()
    except Exception:
        return None


def find_config():
    for p in CFG_PATHS:
        if _read_text(p) is not None:
            return p
    return None


def load_config():
    path = find_config()
    if path is None:
        return None
    txt = _read_text(path)
    if txt is None:
        return None
    for raw in txt.split("\n"):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        # strip a trailing comment only when preceded by a space
        sp = val.find(" #")
        if sp >= 0:
            val = val[:sp].strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        if key in CONFIG_STR:
            globals()[key] = val
    return path


# ============================================================
#  DISPLAY
# ============================================================
def text_w(s):
    if _TW is not None:
        try:
            return _TW(s)
        except Exception:
            pass
    return CH_W * len(s)


def wrap_text(s, max_px):
    """Wrap text into lines that fit the screen."""
    lines = []
    for para in str(s).split("\n"):
        words = para.split(" ")
        cur = ""
        for w in words:
            cand = w if cur == "" else cur + " " + w
            if text_w(cand) <= max_px:
                cur = cand
                continue
            if cur:
                lines.append(cur)
                cur = ""
            while text_w(w) > max_px and len(w) > 1:
                cut = len(w) * max_px // max(1, text_w(w))
                if cut < 1:
                    cut = 1
                lines.append(w[:cut])
                w = w[cut:]
            cur = w
        lines.append(cur)
    if len(lines) > 200:
        lines = lines[:200]
        lines.append("... (truncated)")
    return lines


def set_status(txt, col):
    global STATUS_TXT, STATUS_COL, DIRTY_STATUS
    STATUS_TXT = txt
    STATUS_COL = col
    DIRTY_STATUS = True


def key_tag():
    """Keyboard summary for the header: KX = failed, K<n> = number of keys."""
    if KB is None:
        return "KX"
    return "K" + str(KEY_EVENTS)


def draw_header():
    M5.Lcd.fillRect(0, 0, W, HDR_H, C_HDR)
    M5.Lcd.setTextColor(C_TITLE, C_HDR)
    M5.Lcd.drawString("AI CHAT", 3, 0)
    # API status dot: green OK, red error, grey not checked yet
    if API_STATE == 1:
        dcol = C_OK
    elif API_STATE == 2:
        dcol = C_ERR
    else:
        dcol = C_DIM
    M5.Lcd.fillRect(DOT_X, 4, 6, 6, dcol)
    wifi = "W" if wifi_ok() else "-"
    try:
        bat = str(int(Power.getBatteryLevel()))
    except Exception:
        bat = "-"
    info = wifi + " " + bat + "% " + mem_hdr() + " " + key_tag()
    x = W - 4 - text_w(info)
    if x < DOT_X + 10:
        x = DOT_X + 10
    M5.Lcd.setTextColor(C_DIM, C_HDR)
    M5.Lcd.drawString(info, x, 0)


def draw_tagged(lines, top):
    y = top
    for tag, txt in lines:
        if tag == "U" or tag == "U ":
            col = C_USER
            pre = "U "
        elif tag == "A" or tag == "A ":
            col = C_AI
            pre = "A "
        else:
            col = C_BAD
            pre = "! "
        M5.Lcd.setTextColor(col, C_BG)
        M5.Lcd.drawString(pre + txt, 2, y)
        y += LH


def draw_chat():
    M5.Lcd.fillRect(0, CHAT_Y, W, CHAT_H, C_BG)
    if STATE == S_READ:
        start = READ_PAGE * CHAT_ROWS
        draw_tagged(READ_LINES[start:start + CHAT_ROWS], CHAT_Y + 1)
        return
    if not VIEW:
        M5.Lcd.setTextColor(C_DIM, C_BG)
        M5.Lcd.drawString("BtnA: AI connection config mode", 2, CHAT_Y + 2)
        M5.Lcd.drawString("ESC clear | /clear | /model", 2, CHAT_Y + 2 + LH)
        M5.Lcd.drawString("Silent? hold FN + press a key", 2, CHAT_Y + 2 + 2 * LH)
        return
    draw_tagged(VIEW[-CHAT_ROWS:], CHAT_Y + 1)


def draw_input():
    M5.Lcd.fillRect(0, INPUT_Y, W, INPUT_H, C_INPUT_BG)
    s = IN_BUF
    if len(s) > IN_CAP:
        s = "<" + s[-(IN_CAP - 1):]
    M5.Lcd.setTextColor(C_INPUT, C_INPUT_BG)
    M5.Lcd.drawString("> " + s + "_", 3, INPUT_Y + 1)


def draw_status():
    M5.Lcd.fillRect(0, STATUS_Y, W, H - STATUS_Y, C_STATUS_BG)
    M5.Lcd.setTextColor(STATUS_COL, C_STATUS_BG)
    txt = STATUS_TXT
    while text_w(txt) > W - 6 and len(txt) > 4:
        txt = txt[:-2]
    M5.Lcd.drawString(txt, 3, STATUS_Y + 1)


def render():
    global DIRTY_HDR, DIRTY_CHAT, DIRTY_INPUT, DIRTY_STATUS
    if DIRTY_HDR:
        draw_header()
        DIRTY_HDR = False
    if DIRTY_CHAT:
        if MODE == MODE_CONFIG:
            draw_config()
        else:
            draw_chat()
        DIRTY_CHAT = False
    if DIRTY_INPUT:
        if MODE == MODE_CHAT:
            draw_input()
        DIRTY_INPUT = False
    if DIRTY_STATUS:
        draw_status()
        DIRTY_STATUS = False


# ============================================================
#  HISTORY
# ============================================================
def rebuild_view():
    global VIEW, DIRTY_CHAT
    lines = []
    for tag, txt in HIST:
        for ln in wrap_text(txt, CHAT_W):
            lines.append((tag, ln))
    VIEW = lines
    DIRTY_CHAT = True


def add_exchange(tag, txt):
    global HIST
    HIST.append((tag, fold_ascii(txt).strip()))
    limit = HISTORY_LIMIT
    if limit < 2:
        limit = 2
    while len(HIST) > limit:
        HIST.pop(0)
    rebuild_view()


def open_reader(txt):
    global READ_LINES, READ_PAGE, READ_PAGES, STATE, DIRTY_CHAT
    rows = []
    for ln in wrap_text(txt, CHAT_W):
        rows.append(("A", ln))
    READ_LINES = rows
    READ_PAGE = 0
    READ_PAGES = (len(rows) + CHAT_ROWS - 1) // CHAT_ROWS
    if READ_PAGES < 1:
        READ_PAGES = 1
    STATE = S_READ
    DIRTY_CHAT = True


# ============================================================
#  NETWORK
# ============================================================
def wifi_ok():
    if WLAN is None:
        return False
    try:
        return bool(WLAN.isconnected())
    except Exception:
        return False


def wifi_init():
    global WLAN
    if network is None:
        return
    try:
        WLAN = network.WLAN(network.STA_IF)
        WLAN.active(True)
    except Exception as e:
        WLAN = None
        print("wifi:", e)
        return
    if WIFI_SSID and not wifi_ok():
        set_status("Connecting WiFi...", C_WARN)
        render()
        try:
            WLAN.connect(WIFI_SSID, WIFI_PASS)
            t0 = time.ticks_ms()
            while not wifi_ok() and time.ticks_diff(time.ticks_ms(), t0) < 15000:
                M5.update()
                time.sleep_ms(200)
        except Exception as e:
            print("wifi connect:", e)


# ============================================================
#  API
# ============================================================
def url_chat():
    b = BASE_URL.strip().rstrip("/")
    if b.endswith("/chat/completions"):
        return b
    return b + "/chat/completions"


def url_models():
    b = BASE_URL.strip().rstrip("/")
    if b.endswith("/chat/completions"):
        b = b[:-len("/chat/completions")]
    return b + "/models"


def parse_answer(raw):
    if json is None:
        return None, "json module not available"
    try:
        data = json.loads(raw)
    except Exception:
        return None, "reply is not JSON: " + fold_ascii(raw)[:50]
    if isinstance(data, dict) and data.get("error"):
        return None, "API error: " + fold_ascii(str(data.get("error")))[:60]
    if not isinstance(data, dict):
        return None, "unknown reply format"
    ch = data.get("choices")
    if not ch:
        return None, "no 'choices' in the reply"
    c0 = ch[0]
    txt = ""
    if isinstance(c0, dict):
        msg = c0.get("message")
        if isinstance(msg, dict):
            c = msg.get("content")
            if isinstance(c, str):
                txt = c
            elif isinstance(c, list):
                parts = []
                for it in c:
                    if isinstance(it, dict) and isinstance(it.get("text"), str):
                        parts.append(it["text"])
                txt = " ".join(parts)
        if not txt and isinstance(c0.get("text"), str):
            txt = c0["text"]
    if not txt:
        return None, "empty reply"
    return txt, None


def http_hint(code, raw):
    if code == 401:
        return "401: wrong or empty TOKEN"
    if code == 403:
        return "403: access denied"
    if code == 404:
        return "404: check BASE_URL (needs /v1 ?)"
    if code == 429:
        return "429: too many requests"
    body = fold_ascii(raw)[:40].replace("\n", " ")
    return "HTTP " + str(code) + ": " + body


def net_probe(url):
    """Separate DNS / TCP / TLS problems so errors do not mislead."""
    global NET_MSG
    NET_MSG = ""
    if socket is None:
        NET_MSG = "socket module not available"
        return
    host = url
    i = host.find("://")
    scheme = host[:i] if i >= 0 else "https"
    if i >= 0:
        host = host[i + 3:]
    j = host.find("/")
    if j >= 0:
        host = host[:j]
    port = 80 if scheme == "http" else 443
    k = host.find(":")
    if k >= 0:
        try:
            port = int(host[k + 1:])
        except Exception:
            pass
        host = host[:k]
    if host == "":
        NET_MSG = "empty host in BASE_URL"
        return
    ip = ""
    try:
        infos = socket.getaddrinfo(host, port)
        ip = str(infos[0][-1][0])
    except Exception as e:
        NET_MSG = "DNS failed: " + short_err(e)
        return
    try:
        s = socket.socket()
        s.settimeout(6)
        s.connect((ip, port))
        s.close()
    except Exception as e:
        NET_MSG = "TCP " + ip + ":" + str(port) + " failed: " + short_err(e)
        return
    NET_MSG = "network to " + ip + ":" + str(port) + " OK"


def http_get(url, headers, timeout=15):
    """GET; the timeout argument is tried first, because official requests2 has
    none (it can hang). Once the firmware rejects that argument it is not retried."""
    global HTTP_TMO
    if timeout and HTTP_TMO:
        try:
            return requests2.get(url, headers=headers, timeout=timeout)
        except TypeError:
            HTTP_TMO = False
    return requests2.get(url, headers=headers)


def http_post_chat(url, payload, headers, timeout=25):
    global HTTP_TMO
    if timeout and HTTP_TMO:
        try:
            return requests2.post(url, json=payload, headers=headers, timeout=timeout)
        except TypeError:
            HTTP_TMO = False
    try:
        return requests2.post(url, json=payload, headers=headers)
    except TypeError:
        return requests2.post(url, data=json.dumps(payload), headers=headers)


def free_history():
    """Drop the oldest exchanges while the heap is too tight for the TLS
    handshake. The chat keeps working; only old lines disappear, and the
    status bar says so. Does nothing when memory is comfortable."""
    global HIST
    dropped = 0
    free = mem_free()
    while free >= 0 and free < MEM_MIN and len(HIST) > 2:
        HIST.pop(0)
        dropped += 1
        free += 512            # rough size of one dropped line
    if dropped:
        rebuild_view()
    return dropped


def messages_payload():
    """Request body. Only the newest HISTORY that fits in HIST_CHARS is sent:
    every extra kilobyte of payload makes the TLS handshake harder, and that
    is what used to fail with errno 12 (ENOMEM) on this device."""
    msgs = []
    if SYSTEM_PROMPT:
        msgs.append({"role": "system", "content": SYSTEM_PROMPT})
    total = len(SYSTEM_PROMPT)
    picked = []
    for tag, txt in reversed(HIST):
        total += len(txt) + 24
        if picked and total > HIST_CHARS:
            break
        picked.append((tag, txt))
    for tag, txt in reversed(picked):
        if tag == "U":
            msgs.append({"role": "user", "content": txt})
        elif tag == "A":
            msgs.append({"role": "assistant", "content": txt})
    return msgs


def api_chat():
    if requests2 is None:
        return None, "requests2 not available"
    if json is None:
        return None, "json module not available"
    if not BASE_URL.startswith("http"):
        return None, "BASE_URL must be http(s)://..."
    payload = {
        "model": MODEL,
        "messages": messages_payload(),
        "max_tokens": MAX_TOKENS,
        "stream": False,
    }
    hdr = {"Content-Type": "application/json", "Authorization": "Bearer " + TOKEN}
    mem_collect()
    try:
        r = http_post_chat(url_chat(), payload, hdr)
    except Exception as e:
        return None, "connection failed: " + short_err(e)
    try:
        code = r.status_code
        raw = r.text
    except Exception as e:
        return None, "broken reply: " + short_err(e)
    finally:
        try:
            r.close()
        except Exception:
            pass
    if code != 200:
        return None, http_hint(code, raw)
    return parse_answer(raw)


def api_selftest():
    """Check BASE_URL + TOKEN without typing anything."""
    global API_STATE, DIRTY_HDR
    if not BASE_URL.startswith("http"):
        API_STATE = 2
        DIRTY_HDR = True
        set_status("BASE_URL is empty (chat_config.txt)", C_ERR)
        return
    if TOKEN == "":
        API_STATE = 2
        DIRTY_HDR = True
        set_status("TOKEN is empty - fill in chat_config.txt", C_WARN)
        return
    if requests2 is None:
        API_STATE = 2
        DIRTY_HDR = True
        set_status("requests2 not available in this firmware", C_ERR)
        return
    if not wifi_ok():
        API_STATE = 2
        DIRTY_HDR = True
        set_status("WiFi not connected - chat cannot work", C_ERR)
        return
    global NET_MSG
    set_status("Checking network...", C_WARN)
    render()
    net_probe(url_chat())
    if " OK" not in NET_MSG:
        # DNS/TCP already failed: do not attempt HTTPS, requests2 blocks and
        # could leave the screen stuck on "Checking API..."
        API_STATE = 2
        DIRTY_HDR = True
        set_status("API: " + NET_MSG, C_ERR)
        return
    set_status("Checking API... (may take a while)", C_WARN)
    render()
    mem_collect()
    try:
        r = http_get(url_models(), {"Authorization": "Bearer " + TOKEN})
        code = r.status_code
        r.close()
    except Exception as e:
        API_STATE = 2
        DIRTY_HDR = True
        set_status("API: " + short_err(e) + " | " + NET_MSG, C_ERR)
        return
    if code == 200:
        API_STATE = 1
        DIRTY_HDR = True
        set_status("API OK (" + MODEL + ") - ready to chat", C_OK)
        beep_ready()
    elif code == 401:
        API_STATE = 2
        DIRTY_HDR = True
        set_status("API 401: wrong TOKEN", C_ERR)
    elif code == 404:
        API_STATE = 2
        DIRTY_HDR = True
        set_status("API 404: check BASE_URL", C_ERR)
    else:
        API_STATE = 2
        DIRTY_HDR = True
        set_status("API HTTP " + str(code), C_ERR)


# ============================================================
#  CHAT MODE / CONFIG MODE
#  In chat mode the web server socket is closed, so the heap stays as free
#  as possible for the TLS handshake (the cause of errno 12 ENOMEM).
# ============================================================
def web_ip():
    if WLAN is None:
        return ""
    try:
        if not WLAN.isconnected():
            return ""
        return str(WLAN.ifconfig()[0])
    except Exception:
        return ""


def web_url():
    ip = web_ip()
    if WCM is None or ip == "":
        return ""
    p = WCM.port()
    if not p:
        return ""
    if p == 80:
        return "http://" + ip + "/"
    return "http://" + ip + ":" + str(p) + "/"


def _one_line(v):
    return str(v).replace("\n", " ").replace("\r", " ").strip()


# ============================================================
#  WEB MODULE (webcfg.py) - loaded only in config mode
#  Keeping the web server in its own file means chat mode never pays for it
#  in heap, which is what keeps errno 12 (ENOMEM) away.
# ============================================================
class _NS:
    """Namespace wrapper for a module loaded through exec().

    exec() gets a plain dict, because in MicroPython `instance.__dict__` is
    not a real dict and exec() rejects it with a TypeError. Attribute access
    is forwarded to that dict so main.py can still call WCM.start().
    """
    def __init__(self, d):
        self.d = d

    def __getattr__(self, name):
        try:
            return self.d[name]
        except KeyError:
            raise AttributeError(name)


def web_load():
    """Load webcfg.py. exec(src) is tried first: the builtin compile() is not
    always present in MicroPython firmware, while exec(str) always is."""
    global WCM, WEB_MSG
    if WCM is not None:
        return WCM
    mem_collect()
    WEB_MSG = ""
    for p in ("/flash/res/webcfg.py", "/flash/webcfg.py", "res/webcfg.py", "webcfg.py"):
        try:
            src = open(p).read()
        except Exception:
            continue
        for attempt in (0, 1):
            ns = {"__name__": "webcfg"}
            try:
                if attempt == 0:
                    exec(src, ns)
                else:
                    exec(compile(src, p, "exec"), ns)
            except Exception as e:
                WEB_MSG = "failed to load " + p + ": " + fold_ascii(str(e))[:70]
                mem_collect()
                continue
            WCM = _NS(ns)
            WEB_MSG = "web module from " + p
            return WCM
    if WEB_MSG == "":
        WEB_MSG = "webcfg.py not found"
    return None


def web_unload():
    """Release the web module from memory (used in chat mode)."""
    global WCM, CFG_STATE
    if WCM is not None:
        try:
            WCM.stop()
        except Exception:
            pass
    WCM = None
    CFG_STATE = 0
    mem_collect()


def web_state():
    if API_STATE == 1:
        api = "connected"
    elif API_STATE == 2:
        api = "error"
    else:
        api = "not tested"
    info = ("IP " + (web_ip() or "-") + " | port " +
            str(WCM.port() if WCM is not None else 0) + " | mem " + mem_txt() +
            " | " + WEB_MSG + (" | " + NET_MSG if NET_MSG else ""))
    return (BASE_URL, TOKEN, MODEL, api, info)


def web_save(params):
    """Apply the form: an empty field means the value is kept."""
    global BASE_URL, TOKEN, MODEL, API_STATE, DIRTY_HDR
    changes = []
    if "base" in params:
        v = _one_line(params["base"])
        if v:
            BASE_URL = v
            changes.append("BASE_URL")
    if "token" in params:
        v = _one_line(params["token"])
        if v:
            TOKEN = v
            changes.append("TOKEN")
    if "model" in params:
        v = _one_line(params["model"])
        if v:
            MODEL = v
            changes.append("MODEL")
    ok = save_config_file()
    API_STATE = 0
    DIRTY_HDR = True
    if changes:
        msg = "Saved: " + ", ".join(changes)
    else:
        msg = "No changes"
    if ok:
        msg += ". It is re-tested when you go back to chat mode."
    return msg, ok


def save_config_file():
    """Write the configuration to chat_config.txt; comments/foreign lines are kept."""
    global WEB_MSG
    path = find_config()
    if path is None:
        path = CFG_PATH
        try:
            os.mkdir("/flash/res")
        except Exception:
            pass
        try:
            os.mkdir("/flash/res/config")
        except Exception:
            pass
    kept = []
    txt = _read_text(path)
    if txt is not None:
        for raw in txt.split("\n"):
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#"):
                kept.append(line)
                continue
            if "=" in line and line.split("=", 1)[0].strip() in CONFIG_STR:
                continue
            kept.append(line)
    out = []
    for k in CONFIG_STR:
        out.append(k + " = " + str(globals().get(k, "")))
    text = ""
    if kept:
        text = "\n".join(kept) + "\n"
    text += "# --- managed through the web page ---\n" + "\n".join(out) + "\n"
    try:
        f = open(path, "w")
        try:
            f.write(text)
        finally:
            f.close()
        return True
    except Exception as e:
        WEB_MSG = "failed to write " + path + ": " + short_err(e)
        return False


def btn_init():
    global BTN
    BTN = globals().get("BtnA", None)


def mode_chat(check=False):
    """Normal mode: no web server, no web module in memory. check=True runs
    the API test once - at power-on and when coming back from config mode."""
    global MODE, DIRTY_CHAT, DIRTY_HDR, DIRTY_STATUS, HINTED
    MODE = MODE_CHAT
    web_unload()
    HINTED = False
    DIRTY_HDR = True
    DIRTY_CHAT = True
    DIRTY_STATUS = True
    set_status(PRESET_HELP, C_DIM)
    if check:
        render()          # show the chat screen first, the test blocks
        api_selftest()


def mode_config():
    """Config mode: start the web server and nothing else. The API test is
    deliberately NOT run here, so this screen appears at once and the heap
    stays free for the server."""
    global MODE, CFG_MSG, CFG_STATE, DIRTY_CHAT, DIRTY_HDR, DIRTY_STATUS
    MODE = MODE_CONFIG
    CFG_STATE = 0           # the screen shows "Waiting..." until the server is ready
    CFG_MSG = ""
    DIRTY_HDR = True
    DIRTY_STATUS = True
    DIRTY_CHAT = True
    render()                # show "Waiting..." before the module work
    web_load()
    if WCM is not None and WCM.start():
        CFG_STATE = 1
        CFG_MSG = "Ready. Open the address above from your phone or laptop."
    elif WCM is not None:
        CFG_STATE = 2
        CFG_MSG = WCM.last()
    else:
        CFG_STATE = 2
        CFG_MSG = WEB_MSG
    DIRTY_CHAT = True
    set_status("BtnA: back to chat", C_DIM)


def btn_poll(now):
    """BtnA switches modes. isPressed() gives edge detection + debounce, so
    holding the button does not flip the mode back and forth."""
    global BTN_DOWN, BTN_LAST, BTN_SEEN
    if BTN is None:
        return
    edge = False
    f = getattr(BTN, "isPressed", None)
    if callable(f):
        try:
            down = bool(f())
        except Exception:
            return
        edge = down and not BTN_DOWN
        BTN_DOWN = down
    else:
        g = getattr(BTN, "wasPressed", None)
        if not callable(g):
            return
        try:
            edge = bool(g())
        except Exception:
            return
    if not edge:
        return
    BTN_SEEN = True
    if BTN_LAST is not None and time.ticks_diff(now, BTN_LAST) < 400:
        return
    BTN_LAST = now
    if MODE == MODE_CHAT:
        mode_config()
    else:
        mode_chat(True)


def web_poll(now):
    """Serve the config page while config mode is on."""
    global WEB_MSG
    if MODE != MODE_CONFIG or WCM is None:
        return
    try:
        WCM.poll(web_state, web_save)
    except Exception as e:
        WEB_MSG = short_err(e)


def draw_config():
    M5.Lcd.fillRect(0, CHAT_Y, W, CHAT_H, C_BG)
    M5.Lcd.setTextColor(C_TITLE, C_BG)
    M5.Lcd.drawString("MODE CONFIG", 2, CHAT_Y + 2)
    M5.Lcd.setTextColor(C_DIM, C_BG)
    M5.Lcd.drawString("MODEL: " + fold_ascii(MODEL), 2, CHAT_Y + 2 + LH)
    url = web_url()
    if CFG_STATE == 0:
        # still preparing: do not report failure before it really failed
        M5.Lcd.setTextColor(C_WARN, C_BG)
        M5.Lcd.drawString("Waiting...", 2, CHAT_Y + 2 + 2 * LH)
    elif url:
        M5.Lcd.setTextColor(C_OK, C_BG)
        M5.Lcd.drawString("Open: " + url, 2, CHAT_Y + 2 + 2 * LH)
    else:
        M5.Lcd.setTextColor(C_ERR, C_BG)
        M5.Lcd.drawString("Web server failed", 2, CHAT_Y + 2 + 2 * LH)
    # messages are wrapped so error text is not cut off on screen
    y = CHAT_Y + 2 + 3 * LH
    bottom = CHAT_Y + CHAT_H - LH
    seen = []
    for msg, col in ((CFG_MSG, C_WARN),
                     ("" if (url or CFG_STATE == 0) else WEB_MSG, C_ERR),
                     (NET_MSG, C_DIM)):
        if not msg or msg in seen:
            continue        # do not print the same message twice
        seen.append(msg)
        M5.Lcd.setTextColor(col, C_BG)
        for ln in wrap_text(fold_ascii(msg), W - 4):
            if y > bottom:
                return
            M5.Lcd.drawString(ln, 2, y)
            y += LH


# ============================================================
#  CHAT FLOW
# ============================================================
def handle_command(txt):
    global BEEP_ON, DIRTY_HDR
    cmd = txt.strip().lower()
    if cmd == "/clear":
        HIST[:] = []
        rebuild_view()
        set_status("History cleared", C_OK)
    elif cmd == "/model":
        set_status("Model: " + MODEL, C_OK)
    elif cmd == "/beep":
        BEEP_ON = not BEEP_ON
        set_status("Beep " + ("ON" if BEEP_ON else "OFF"), C_OK)
        if BEEP_ON:
            beep_ready()
    elif cmd == "/keys":
        set_status("Keys:" + str(KEY_EVENTS) + " callback:" +
                   ("yes" if CB_SEEN else "no") + " code:" + code_hex(LAST_CODE), C_OK)
    else:
        set_status("Unknown command: " + cmd, C_WARN)


def code_hex(v):
    return hex(v) if v >= 0 else "-"


def do_send():
    global IN_BUF, STATE, PENDING, DIRTY_INPUT, API_STATE, DIRTY_HDR
    PENDING = False
    msg = IN_BUF.strip()
    if msg == "":
        set_status("Type a message first, then ENTER", C_WARN)
        return
    if msg.startswith("/"):
        IN_BUF = ""
        DIRTY_INPUT = True
        handle_command(msg)
        return
    if TOKEN == "":
        set_status("TOKEN is empty - fill in chat_config.txt", C_ERR)
        return
    if not wifi_ok():
        API_STATE = 2
        DIRTY_HDR = True
        set_status("WiFi not connected", C_ERR)
        return
    IN_BUF = ""
    DIRTY_INPUT = True
    add_exchange("U", msg)
    cut = free_history()
    STATE = S_WAIT
    if cut:
        set_status("Waiting for the reply... (" + str(cut) + " old line(s) trimmed "
                   "for memory)", C_WARN)
    else:
        set_status("Waiting for the reply...", C_WARN)
    render()
    ans, err = api_chat()
    if err is not None:
        API_STATE = 2
        DIRTY_HDR = True
        add_exchange("!", err)
        STATE = S_TYPE
        set_status(err, C_ERR)
        beep_error()
    else:
        API_STATE = 1
        DIRTY_HDR = True
        add_exchange("A", ans)
        open_reader(ans)
        beep_answer()
        if READ_PAGES > 1:
            set_status("ENTER: next (1/" + str(READ_PAGES) + ")", C_OK)
        else:
            # a short reply is already visible in the history -> type right away
            STATE = S_TYPE
            set_status("Reply received - ready to type", C_OK)
    render()


def enter_action():
    global STATE, PENDING, READ_PAGE, DIRTY_CHAT
    if STATE == S_WAIT:
        return
    if STATE == S_READ:
        if READ_PAGE + 1 < READ_PAGES:
            READ_PAGE += 1
            DIRTY_CHAT = True
            set_status("ENTER: next (" + str(READ_PAGE + 1) + "/" + str(READ_PAGES) + ")", C_OK)
        else:
            STATE = S_TYPE
            DIRTY_CHAT = True
            set_status("Ready for a new message", C_DIM)
        return
    PENDING = True


def backspace():
    global IN_BUF, DIRTY_INPUT, STATE, DIRTY_CHAT
    if STATE == S_READ:
        STATE = S_TYPE
        DIRTY_CHAT = True
        return
    if IN_BUF:
        IN_BUF = IN_BUF[:-1]
        DIRTY_INPUT = True


def escape_action():
    global IN_BUF, DIRTY_INPUT, STATE, READ_PAGE, DIRTY_CHAT
    if STATE == S_READ:
        STATE = S_TYPE
        DIRTY_CHAT = True
        set_status("Ready for a new message", C_DIM)
        return
    if IN_BUF:
        IN_BUF = ""
        DIRTY_INPUT = True
        set_status("Input cleared", C_DIM)


def page_move(step):
    global READ_PAGE, DIRTY_CHAT
    if STATE != S_READ:
        return
    p = READ_PAGE + step
    if p < 0:
        p = 0
    if p > READ_PAGES - 1:
        p = READ_PAGES - 1
    if p != READ_PAGE:
        READ_PAGE = p
        DIRTY_CHAT = True
        set_status("Page " + str(p + 1) + "/" + str(READ_PAGES), C_OK)


# ============================================================
#  KEYBOARD
# ============================================================
def _num(name):
    v = getattr(KeyCode, name, None)
    if v is None:
        return None
    try:
        return int(v)
    except Exception:
        return None


def _spec(name):
    """Special codes: drop invalid ones and those clashing with typed characters."""
    v = _num(name)
    if v is None:
        return None
    if 0x20 <= v <= 0x7E:
        return None
    if v <= 0:
        return None
    return v


def build_key_tables():
    global SPEC_BACK, SPEC_ESC, SPEC_ENTER, SPEC_UP, SPEC_DOWN, SPEC_LEFT, SPEC_RIGHT
    global KEY_ENTER
    KEY_ENTER = _num("KEYCODE_ENTER")
    if KEY_ENTER is None:
        KEY_ENTER = 0x0D
    cand = {
        "SPEC_BACK": (_spec("KEYCODE_BACKSPACE"), _spec("KEYCODE_BACK"),
                      _spec("KEYCODE_DEL"), 0x08, 0x7F),
        "SPEC_ESC": (_spec("KEYCODE_ESC"), _spec("KEYCODE_ESCAPE"), 0x1B),
        "SPEC_UP": (_spec("KEYCODE_UP"), _spec("KEYCODE_ARROW_UP")),
        "SPEC_DOWN": (_spec("KEYCODE_DOWN"), _spec("KEYCODE_ARROW_DOWN")),
        "SPEC_LEFT": (_spec("KEYCODE_LEFT"), _spec("KEYCODE_ARROW_LEFT")),
        "SPEC_RIGHT": (_spec("KEYCODE_RIGHT"), _spec("KEYCODE_ARROW_RIGHT")),
    }
    SPEC_BACK = _clean(cand["SPEC_BACK"])
    SPEC_ESC = _clean(cand["SPEC_ESC"])
    SPEC_UP = _clean(cand["SPEC_UP"])
    SPEC_DOWN = _clean(cand["SPEC_DOWN"])
    SPEC_LEFT = _clean(cand["SPEC_LEFT"])
    SPEC_RIGHT = _clean(cand["SPEC_RIGHT"])
    # Enter: official sentinel + the usual newline codes
    ent = []
    for v in (KEY_ENTER, 0x0D, 0x0A):
        if v is not None and v > 0 and v not in ent and not (0x20 <= v <= 0x7E):
            ent.append(v)
    SPEC_ENTER = tuple(ent)


def _clean(seq):
    out = []
    for v in seq:
        if v is None:
            continue
        if v <= 0 or 0x20 <= v <= 0x7E:
            continue
        if v in out:
            continue
        out.append(v)
    return tuple(out)


def read_key_code(dev):
    """Read a key code; tolerant of int/bytes/str/None."""
    if dev is None:
        return None
    try:
        raw = dev.get_key()
    except Exception:
        return None
    if raw is None or type(raw) is bool:
        return None
    if isinstance(raw, (bytes, bytearray, str)):
        if len(raw) == 0:
            return None
        try:
            v = int(raw)
        except Exception:
            v = raw[0] if not isinstance(raw, str) else ord(raw[0])
    else:
        try:
            v = int(raw)
        except Exception:
            return None
    return v if v > 0 else None


def push_key(code, from_cb):
    """The callback/polling only fills the queue; heavy work happens in loop()."""
    global CB_SEEN
    if code is None:
        return
    if from_cb:
        CB_SEEN = True
    if len(KEY_Q) < 24:
        KEY_Q.append(code)


def kb_event(dev):
    push_key(read_key_code(dev), True)


def kb_tick():
    """Some UIFlow2 keyboard drivers (CardKB/Tab5) only deliver events after
    tick(). Called only when the method exists, so drivers without it are safe."""
    global TICK_OK
    if KB is None or not TICK_OK:
        return
    try:
        KB.tick()
    except Exception:
        TICK_OK = False


def poll_keyboard(now):
    """Fallback: some firmwares send no events, but get_key() still works."""
    global LAST_POLL
    if KB is None or CB_SEEN:
        return
    if time.ticks_diff(now, LAST_POLL) < POLL_MS:
        return
    LAST_POLL = now
    push_key(read_key_code(KB), False)


def handle_key(code):
    global KEY_EVENTS, LAST_CODE, DIRTY_HDR, IN_BUF, DIRTY_INPUT, STATE, DIRTY_CHAT
    KEY_EVENTS += 1
    LAST_CODE = code
    DIRTY_HDR = True
    if 0x20 <= code <= 0x7E:
        if STATE == S_WAIT:
            return
        if STATE == S_READ:
            STATE = S_TYPE
            IN_BUF = ""
            DIRTY_CHAT = True
            set_status(PRESET_HELP, C_DIM)
        if len(IN_BUF) < IN_BUF_MAX:
            IN_BUF += chr(code)
            DIRTY_INPUT = True
        beep_key(code)
        return
    if code in SPEC_BACK:
        if STATE != S_WAIT:
            beep_del()
            backspace()
        return
    if code in SPEC_ESC:
        if STATE != S_WAIT:
            beep_esc()
            escape_action()
        return
    if code in SPEC_UP or code in SPEC_LEFT:
        beep_page(True)
        page_move(-1)
        return
    if code in SPEC_DOWN or code in SPEC_RIGHT:
        beep_page(False)
        page_move(1)
        return
    if code in SPEC_ENTER or (KEY_ENTER > 0x7E and code >= KEY_ENTER):
        if STATE == S_WAIT:
            return
        beep_enter()
        enter_action()
        return
    # unknown code: show its value so it can be mapped, without a destructive action
    set_status("Unknown key code: " + hex(code), C_WARN)


def kb_init():
    global KB, KB_ERR, TICK_OK
    if MatrixKeyboard is None:
        KB_ERR = "MatrixKeyboard not present in firmware"
        return
    try:
        KB = MatrixKeyboard()
        KB.set_callback(kb_event)
        TICK_OK = callable(getattr(KB, "tick", None))
    except Exception as e:
        KB = None
        KB_ERR = short_err(e)


# ============================================================
#  SETUP / LOOP
# ============================================================
def pick_font():
    global FONT, FONT_NAME, CH_W, LH, CHAT_W, IN_CAP
    fonts = getattr(Widgets, "FONTS", None)
    for nm in ("Montserrat12", "DejaVu12", "Montserrat14", "DejaVu9", "Montserrat16"):
        f = getattr(fonts, nm, None) if fonts is not None else None
        if f is not None:
            FONT = f
            FONT_NAME = nm
            break
    size = 12
    for i in range(len(FONT_NAME)):
        ch = FONT_NAME[i]
        if ch >= "0" and ch <= "9":
            try:
                size = int(FONT_NAME[i:])
            except Exception:
                size = 12
            break
    CH_W = int(size * 0.55)
    if CH_W < 5:
        CH_W = 5
    LH = size + 3
    if LH < 13:
        LH = 13
    CHAT_W = W - 4 - 2 * CH_W
    IN_CAP = (W - 10) // CH_W - 2


def setup():
    global FONT, FONT_NAME, _TW, W, H, CHAT_H, CHAT_ROWS, INPUT_Y, STATUS_Y, DOT_X
    global DIRTY_HDR, DIRTY_CHAT, DIRTY_INPUT, DIRTY_STATUS, LAST_TICK, START_T

    M5.begin()
    try:
        Widgets.setRotation(1)
    except Exception:
        pass
    try:
        W = M5.Display.width()
        H = M5.Display.height()
        if W < H:
            Widgets.setRotation(1)
            W = M5.Display.width()
            H = M5.Display.height()
    except Exception:
        W = 240
        H = 135

    pick_font()
    if FONT is not None:
        M5.Lcd.setFont(FONT)
    _TW = getattr(M5.Lcd, "textWidth", None)
    if not callable(_TW):
        _TW = None

    CHAT_H = H - HDR_H - INPUT_H - STATUS_H
    if CHAT_H < LH * 2:
        CHAT_H = LH * 2
    CHAT_ROWS = CHAT_H // LH
    if CHAT_ROWS < 1:
        CHAT_ROWS = 1
    INPUT_Y = HDR_H + CHAT_H
    STATUS_Y = INPUT_Y + INPUT_H
    DOT_X = 3 + text_w("AI CHAT") + 6

    Widgets.fillScreen(C_BG)
    build_key_tables()
    cfg = load_config()
    spk_init()
    wifi_init()
    kb_init()
    btn_init()

    if cfg is None:
        set_status("chat_config.txt not found - using the defaults", C_WARN)
    else:
        print("config:", cfg)
    if KB is None and KB_ERR:
        set_status("Keyboard: " + KB_ERR, C_ERR)

    DIRTY_HDR = True
    DIRTY_CHAT = True
    DIRTY_INPUT = True
    DIRTY_STATUS = True
    render()
    mode_chat(True)      # first API check happens here
    m = mem_free()
    if m >= 0 and m < 32768:
        print("warning: heap", mem_txt(), "is tight for HTTPS")
    render()
    LAST_TICK = time.ticks_ms()
    START_T = LAST_TICK
    if STATUS_TXT == "":
        set_status(PRESET_HELP, C_DIM)


def loop():
    global LAST_TICK, LAST_PING, PENDING, DIRTY_HDR, HINTED
    M5.update()
    now = time.ticks_ms()
    kb_tick()
    poll_keyboard(now)
    btn_poll(now)
    web_poll(now)
    while KEY_Q:
        handle_key(KEY_Q.pop(0))
    if PENDING:
        do_send()
    if time.ticks_diff(now, LAST_TICK) > 4000:
        LAST_TICK = now
        DIRTY_HDR = True
    if time.ticks_diff(now, LAST_PING) > 9000:
        LAST_PING = now
        if not wifi_ok():
            set_status("WiFi disconnected", C_ERR)
    # the Cardputer keyboard sleeps after boot: remind once if no key arrived
    # escape hatch: with no keyboard and no button, open config mode so the
    # web page stays reachable
    if MODE == MODE_CHAT and (not BTN_SEEN) and KEY_EVENTS == 0 and \
            time.ticks_diff(now, START_T) > 30000:
        mode_config()
        return
    if MODE == MODE_CHAT and KEY_EVENTS == 0 and (not HINTED) and \
            time.ticks_diff(now, START_T) > 12000:
        HINTED = True
        set_status("Hold FN and press a key to wake the keyboard", C_WARN)
    render()
    time.sleep_ms(5)


if __name__ == "__main__":
    try:
        setup()
        while True:
            loop()
    except (Exception, KeyboardInterrupt) as e:
        try:
            from utility import print_error_msg

            print_error_msg(e)
        except ImportError:
            print("please update to latest firmware")
