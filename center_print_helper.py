# -*- coding: utf-8 -*-
"""Center ERP print helper.

Runs on the center's Windows laptop. Receives receipt jobs from the website
(https://center-erp-xi.vercel.app) on 127.0.0.1 only and prints them straight to
the Windows printer, using the same drawing code as the original desktop app.
"""
import hmac
import html
import json
import os
import secrets
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import thermal_printer as tp

VERSION = "1.0.0"
PORT = int(os.environ.get("CENTER_PRINT_PORT", "17653"))
DEFAULT_ORIGINS = ["https://center-erp-xi.vercel.app"]
MAX_BODY = 64 * 1024

APP_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "CenterPrintHelper")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
LOG_PATH = os.path.join(APP_DIR, "helper.log")


def log(msg):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 500_000:
            os.replace(LOG_PATH, LOG_PATH + ".old")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(msg) + "\n")
    except Exception:
        pass


def load_config():
    os.makedirs(APP_DIR, exist_ok=True)
    cfg = {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    changed = False
    if not isinstance(cfg.get("token"), str) or len(cfg.get("token", "")) < 16:
        # 12 readable characters grouped 4-4-4, easy to type from the setup page
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        raw = "".join(secrets.choice(alphabet) for _ in range(12))
        cfg["token"] = f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"
        changed = True
    if not isinstance(cfg.get("origins"), list) or not cfg["origins"]:
        cfg["origins"] = list(DEFAULT_ORIGINS)
        changed = True
    if changed:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg


CONFIG = {}


def norm_token(t):
    return "".join(ch for ch in str(t or "").upper() if ch.isalnum())


def token_ok(supplied):
    a = norm_token(supplied)
    b = norm_token(CONFIG.get("token"))
    return bool(a) and hmac.compare_digest(a.encode(), b.encode())


def allowed_origin(origin):
    return bool(origin) and origin in CONFIG.get("origins", [])


SETUP_PAGE = """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>مساعد الطباعة - سنتر الأوائل</title>
<style>body{{font-family:Tahoma,Arial,sans-serif;background:#f1f5f9;margin:0;padding:24px;color:#0f172a}}
.box{{max-width:560px;margin:auto;background:#fff;border-radius:14px;padding:22px;box-shadow:0 4px 18px #0001}}
.code{{font-size:34px;letter-spacing:4px;font-weight:800;direction:ltr;text-align:center;background:#eff6ff;border:2px dashed #2563eb;border-radius:12px;padding:14px;margin:14px 0;user-select:all}}
button{{font-family:inherit;font-size:16px;padding:10px 18px;border:0;border-radius:10px;background:#2563eb;color:#fff;cursor:pointer}}
.ok{{color:#059669;font-weight:700}}.bad{{color:#dc2626;font-weight:700}}small{{color:#64748b}}</style></head><body><div class="box">
<h2>🖨️ مساعد الطباعة يعمل</h2>
<p class="ok">الحالة: شغال (الإصدار {version})</p>
<p>الطابعة الحالية: <b>{printer}</b><br>{printer_note}</p>
<p>رمز الربط (اكتبه مرة واحدة في الموقع: الإعدادات ← الطباعة الحرارية ← رمز مساعد الطباعة):</p>
<div class="code">{token}</div>
<button onclick="t()">طباعة إيصال تجربة</button> <span id="r"></span>
<p><small>هذه الصفحة تُفتح من هذا الجهاز فقط. لا تشارك الرمز مع أحد.</small></p></div>
<script>async function t(){{r.textContent='...';try{{const x=await fetch('/test',{{method:'POST',headers:{{'X-Helper-Token':'{token}'}}}});const j=await x.json();r.textContent=j.ok?'✅ تم إرسال التجربة للطابعة':'❌ '+(j.error||'فشل');}}catch(e){{r.textContent='❌ '+e}}}}</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "CenterPrintHelper/" + VERSION
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log("http " + (fmt % args))

    def _host_ok(self):
        host = (self.headers.get("Host") or "").lower()
        return host in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def _cors(self):
        origin = self.headers.get("Origin")
        if allowed_origin(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Private-Network", "true")

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _html(self, code, text):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def _origin_blocked(self):
        """Browsers always send Origin on cross-site requests. Refuse any origin we do not know."""
        origin = self.headers.get("Origin")
        if origin and not allowed_origin(origin) and origin != f"http://127.0.0.1:{PORT}":
            self._json(403, {"ok": False, "error": "origin_not_allowed"})
            return True
        return False

    def _read_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n > MAX_BODY:
            return None
        raw = self.rfile.read(n) if n else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _authed(self):
        return token_ok(self.headers.get("X-Helper-Token"))

    def do_OPTIONS(self):
        if not self._host_ok():
            return self._json(403, {"ok": False, "error": "bad_host"})
        origin = self.headers.get("Origin")
        if not allowed_origin(origin):
            return self._json(403, {"ok": False, "error": "origin_not_allowed"})
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Helper-Token")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self._host_ok():
            return self._json(403, {"ok": False, "error": "bad_host"})
        path = self.path.split("?", 1)[0]
        if path == "/":
            if self.headers.get("Origin"):
                return self._json(403, {"ok": False, "error": "forbidden"})
            info = tp.list_windows_printers()
            printer = info.get("default_printer") or "غير محددة"
            note = "" if info.get("pywin32_available") else "<span class='bad'>تنبيه: محرك طباعة ويندوز غير متاح</span>"
            return self._html(200, SETUP_PAGE.format(
                version=VERSION, printer=html.escape(printer), printer_note=note, token=CONFIG["token"]))
        if path == "/status":
            if self._origin_blocked():
                return
            info = tp.list_windows_printers()
            out = {"ok": True, "app": "center-print-helper", "version": VERSION,
                   "pywin32_available": bool(info.get("pywin32_available")), "authorized": self._authed()}
            if out["authorized"]:
                out["default_printer"] = info.get("default_printer", "")
                out["printers"] = info.get("printers", [])
            return self._json(200, out)
        return self._json(404, {"ok": False, "error": "not_found"})

    def do_POST(self):
        if not self._host_ok():
            return self._json(403, {"ok": False, "error": "bad_host"})
        if self._origin_blocked():
            return
        path = self.path.split("?", 1)[0]
        if path not in ("/print", "/test"):
            return self._json(404, {"ok": False, "error": "not_found"})
        if not self._authed():
            return self._json(401, {"ok": False, "status": "unauthorized", "error": "رمز الربط غير صحيح"})
        data = self._read_json()
        if data is None:
            return self._json(400, {"ok": False, "status": "error", "error": "طلب غير صالح"})
        try:
            if path == "/test":
                return self._handle_test(data)
            return self._handle_print(data)
        except Exception as e:  # never crash the server
            log("error " + traceback.format_exc())
            return self._json(500, {"ok": False, "status": "error", "error": str(e)})

    @staticmethod
    def _cfg_from(data):
        cfg = tp.load_thermal_config(None)
        incoming = data.get("config")
        if isinstance(incoming, dict):
            for k in tp.DEFAULT_THERMAL_CONFIG:
                if k in incoming and incoming[k] is not None:
                    cfg[k] = str(incoming[k])
        return cfg

    def _handle_print(self, data):
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        cfg = self._cfg_from(data)
        action = str(payload.get("action") or "attendance").strip()
        if cfg.get("thermal_enabled", "1") != "1" and action == "attendance":
            return self._json(200, {"status": "skipped_disabled"})
        code = str(payload.get("code") or "---").strip()
        group = str(payload.get("group") or "---").strip()
        try:
            guard = int(float(cfg.get("thermal_duplicate_guard_sec") or 300))
        except (TypeError, ValueError):
            guard = 300
        if action == "attendance" and tp.check_and_mark_duplicate(
                code, group, guard, force_confirm=bool(payload.get("force_confirm"))):
            return self._json(403, {"status": "confirm_duplicate",
                                    "message": "تم طباعة إيصال لهذا الطالب خلال آخر بضع دقائق"})
        if not tp.PYWIN32_AVAILABLE:
            return self._json(500, {"status": "error", "error": "محرك طباعة ويندوز غير متاح في المساعد"})
        res = tp.print_thermal_receipt_async(payload, cfg)
        log(f"print queued code={code} printer={res.get('printer')} style={res.get('style_index')}")
        return self._json(200, {"status": "success", **res})

    def _handle_test(self, data):
        cfg = self._cfg_from(data)
        sample = {"name": "أحمد محمد محمود (تجربة طباعة)", "code": "3483",
                  "group": "مجموعة 3 ثانوي - السبت 4م", "stage": "الصف الثالث الثانوي",
                  "amount_paid": 80, "receipt_no": "TEST-001", "action": "test"}
        res = tp.print_thermal_receipt_sync(sample, cfg)
        if not res.get("ok"):
            return self._json(500, {"ok": False, "error": res.get("error", "تعذرت الطباعة")})
        return self._json(200, {"ok": True, **res})


def main():
    global CONFIG
    # windowed (no console) builds have no stdout/stderr
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, open(os.devnull, "w"))
    CONFIG = load_config()
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        log("port busy: another helper instance is probably running")
        return 0
    log(f"helper {VERSION} listening on 127.0.0.1:{PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
