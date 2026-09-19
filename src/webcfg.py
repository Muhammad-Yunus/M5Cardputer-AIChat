# ============================================================
#  webcfg.py - web configuration server for AI CHAT (Cardputer)
#
#  Deliberately split from main.py: the MicroPython heap on this device is
#  very tight (the TLS handshake needs one large block), so this module is
#  loaded only IN CONFIG MODE and released again when returning to chat mode.
#
#  main.py loads it through exec(), so the file may live in
#  /flash/res/, /flash/, or next to main.py - without sys.path.
#
#  The page carries only what is needed: BASE_URL, TOKEN, MODEL.
# ============================================================
import socket

NAMES = ("BASE_URL", "TOKEN", "MODEL")

_SRV = None
_PORT = 0
_MSG = ""
_BUFS = {}


def version():
    return "webcfg 4"


def port():
    return _PORT


def last():
    return _MSG


def start(ports=(80, 8080, 8000)):
    """Open the server socket. Returns the port number, or 0 on failure."""
    global _SRV, _PORT, _MSG
    stop()
    for p in ports:
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", p))
            s.listen(1)
            s.setblocking(False)
            _SRV = s
            _PORT = p
            _MSG = "server ready on port " + str(p)
            return p
        except Exception:
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass
            _MSG = "cannot bind port " + str(p)
    _PORT = 0
    return 0


def stop():
    """Close the server socket and free its buffers."""
    global _SRV, _PORT
    s = _SRV
    _SRV = None
    _PORT = 0
    if s is not None:
        try:
            s.close()
        except Exception:
            pass
    try:
        _BUFS.clear()
    except Exception:
        pass


def _esc(t):
    """Escape HTML and drop anything the font cannot show."""
    out = []
    for c in str(t):
        if c == "&":
            out.append("&amp;")
        elif c == "<":
            out.append("&lt;")
        elif c == ">":
            out.append("&gt;")
        elif c == "'" or c == '"':
            out.append("&#" + str(ord(c)) + ";")
        elif ord(c) < 0x20 or ord(c) > 0x7E:
            out.append("?")
        else:
            out.append(c)
    return "".join(out)


def mask(t):
    """Never send the stored token back to the browser."""
    if not t:
        return "(empty)"
    if len(t) <= 4:
        return "****"
    return "****" + t[-4:]


def _unq(s):
    """Decode %XX and + in one form value."""
    out = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "%" and i + 3 <= n:
            try:
                out.append(chr(int(s[i + 1:i + 3], 16)))
                i += 3
                continue
            except Exception:
                pass
        if c == "+":
            out.append(" ")
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _prm(q):
    d = {}
    for p in q.split("&"):
        if p:
            k, _sep, v = p.partition("=")
            d[_unq(k)] = _unq(v)
    return d


_CSS = (
    "*{box-sizing:border-box}"
    "body{margin:0;padding:18px;background:#0b0f16;color:#dbe3ee;"
    "font:15px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}"
    ".wrap{max-width:540px;margin:0 auto}"
    ".card{background:#121826;border:1px solid #223047;border-radius:14px;"
    "padding:18px;margin-bottom:14px}"
    "h1{font-size:19px;margin:0;color:#7fdcff;letter-spacing:.5px}"
    ".sub{color:#7d8aa0;font-size:12px;margin:2px 0 14px}"
    "label{display:block;color:#8fa0b8;font-size:11px;letter-spacing:1px;margin:14px 0 6px}"
    "input{width:100%;padding:11px 12px;border-radius:9px;border:1px solid #2a3648;"
    "background:#0d121c;color:#eaf2ff;font:inherit;outline:none}"
    "input:focus{border-color:#3cc7f1;box-shadow:0 0 0 3px rgba(60,199,241,.2)}"
    ".hint{color:#6d7a90;font-size:11px;margin-top:5px}"
    "button{background:#2aa9d6;color:#04121b;border:0;border-radius:10px;"
    "padding:12px 20px;font:inherit;font-weight:700;cursor:pointer;margin-top:18px}"
    ".pill{display:inline-block;padding:4px 10px;border-radius:999px;font-size:11px;"
    "font-weight:700;margin:0 6px 4px 0}"
    ".ok{background:#12351f;color:#5ef08a}.er{background:#3a1620;color:#ff8a9c}"
    ".un{background:#232a38;color:#a9b6c9}"
    ".msg{padding:11px 13px;border-radius:10px;margin-bottom:14px;font-size:13px}"
    ".mok{background:#12351f;color:#93f0b0;border:1px solid #1d5c33}"
    ".mer{background:#3a1620;color:#ffb3bf;border:1px solid #6b2334}"
    ".kv{color:#7d8aa0;font-size:12px;line-height:1.9}")


def _page(msg, good, st):
    """base, token, model, API status text, extra device info."""
    base, tok, model, api, info = st
    if api == "connected":
        pill = "<span class='pill ok'>API connected</span>"
    elif api == "error":
        pill = "<span class='pill er'>API error</span>"
    else:
        pill = "<span class='pill un'>API not tested</span>"
    if msg:
        kotak = "<div class='msg " + ("mok" if good else "mer") + "'>" + msg + "</div>"
    else:
        kotak = ""
    return ("<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>AI CHAT CONFIG</title><style>" + _CSS + "</style></head><body>"
            "<div class='wrap'>" + kotak +
            "<div class='card'>"
            "<h1>AI CHAT - Cardputer</h1>"
            "<div class='sub'>API connection settings</div>" + pill +
            "<form action='/save'>"
            "<label>BASE URL</label>"
            "<input name='base' value='" + _esc(base) +
            "' autocapitalize=off spellcheck=false>"
            "<div class='hint'>Example: https://api.openai.com/v1</div>"
            "<label>API TOKEN</label>"
            "<input name='token' type='password' value='' placeholder='" +
            _esc(mask(tok)) + "'>"
            "<div class='hint'>Leave empty to keep the current value.</div>"
            "<label>MODEL</label>"
            "<input name='model' value='" + _esc(model) + "'>"
            "<button type='submit'>SAVE</button>"
            "<div class='hint'>The API is tested at power-on and every time you go "
            "back to chat mode.</div>"
            "</form></div>"
            "<div class='card'><div class='kv'>" + _esc(info) + "</div></div>"
            "<div class='kv' style='text-align:center'>this page is served directly "
            "by the Cardputer</div>"
            "</div></body></html>")


def _reply(c, code, ctype, body):
    if not isinstance(body, bytes):
        body = body.encode()
    try:
        c.sendall(("HTTP/1.0 " + code + "\r\nContent-Type: " + ctype +
                   "\r\nContent-Length: " + str(len(body)) +
                   "\r\nConnection: close\r\nCache-Control: no-store\r\n\r\n").encode())
        i = 0
        n = len(body)
        while i < n:
            c.sendall(body[i:i + 512])
            i += 512
    except Exception:
        pass
    try:
        c.close()
    except Exception:
        pass


def poll(get_state, on_save):
    """Serve at most one request; the accept is non-blocking.

    get_state() -> (base, token, model, api, info) - values shown on the page
    on_save(dict) -> (message, ok)                 - store the configuration
    """
    global _MSG
    if _SRV is None:
        return
    try:
        c, _addr = _SRV.accept()
    except OSError:
        return          # nobody has connected yet
    except Exception:
        stop()
        return
    try:
        c.settimeout(1)
    except Exception:
        pass
    d = b""
    try:
        d = c.recv(1024)
    except Exception:
        d = b""
    if not d:
        try:
            c.close()
        except Exception:
            pass
        return
    try:
        _serve(c, d, get_state, on_save)
    except Exception as e:
        _MSG = str(e)[:40]
        try:
            c.close()
        except Exception:
            pass


def _serve(c, d, get_state, on_save):
    k = id(c)
    b = _BUFS.get(k, b"") + d
    if b"\r\n\r\n" not in b:
        if len(_BUFS) > 4:
            _BUFS.clear()
        _BUFS[k] = b[-1024:]
        return
    try:
        _BUFS.pop(k, None)
    except Exception:
        pass
    t = "/"
    try:
        t = b.split(b"\n", 1)[0].split(b" ")[1].decode()
    except Exception:
        t = "/"
    q = ""
    i = t.find("?")
    if i >= 0:
        t, q = t[:i], t[i + 1:]
    if t == "/save":
        msg, ok = on_save(_prm(q))
        _reply(c, "200 OK", "text/html; charset=utf-8",
               _page(msg + " <a href='/'>back</a>", ok, get_state()))
    elif t == "/favicon.ico":
        _reply(c, "404 Not Found", "text/plain", "no icon")
    else:
        _reply(c, "200 OK", "text/html; charset=utf-8", _page("", True, get_state()))