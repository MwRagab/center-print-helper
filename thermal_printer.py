import datetime
import html
import random
import threading
import time
import traceback

try:
    import win32print
    import win32ui
    import win32con
    import win32gui
    PYWIN32_AVAILABLE = True
except ImportError:
    win32print = win32ui = win32con = win32gui = None
    PYWIN32_AVAILABLE = False

_PRINT_LOCK = threading.Lock()
_RECENT_PRINTS = {}  # (code, group) -> unix_timestamp

DEFAULT_THERMAL_CONFIG = {
    "thermal_enabled": "1",
    "thermal_printer_name": "",
    "thermal_style_mode": "auto_daily",
    "thermal_scale": "0.75",
    "thermal_font_name": "IBM Plex Sans Arabic",
    "thermal_header_title": "",
    "thermal_footer_text": "⚠️ عند ضياع الوصل يتم دفع ثمن الحصة",
    "thermal_show_stage": "1",
    "thermal_show_amount": "1",
    "thermal_duplicate_guard_sec": "300",
}

STYLES_META = [
    {"id": "1", "name": "1 — الكلاسيكي بالخطوط الأفقية", "desc": "تاريخ ووقت بالعرض مع فواصل أفقية نظيفة"},
    {"id": "2", "name": "2 — الإطار الثلاثي المرتب", "desc": "تقسيم 3 خانات بحدود متساوية"},
    {"id": "3", "name": "3 — البيضاوي واليوم البارز", "desc": "اسم اليوم بخط ضخم وإطار بيضاوي للاسم والكود"},
    {"id": "4", "name": "4 — الكود المقلوب والتنبيه العلوي", "desc": "شريط كود أسود في الأعلى وتنبيه ضياع الوصل فوقه"},
    {"id": "5", "name": "5 — الكود العملاق وبادج النجاح", "desc": "كود ضخم بالعرض ومستطيل أسود لعبارة تم الحضور"},
    {"id": "6", "name": "6 — الكبسولات والبطاقات المنفصلة", "desc": "كبسولات دائرية للتاريخ والوقت والنجاح والكود"},
    {"id": "7", "name": "7 — الصناديق المزدوجة السريعة", "desc": "تصميم متراص وحدود مستديرة متناسقة"},
    {"id": "8", "name": "8 — الأوائل العصري (Modern Ticket ✨)", "desc": "هيدر أنيق باسم السنتر وكود معكوس ومبلغ مدفوع واضح"},
]

DAY_NAMES_AR = {
    "Sat": "السبت", "Sun": "الأحد", "Mon": "الاثنين",
    "Tue": "الثلاثاء", "Wed": "الأربعاء", "Thu": "الخميس", "Fri": "الجمعة",
}


def load_thermal_config(settings_dict: dict | None = None) -> dict:
    cfg = dict(DEFAULT_THERMAL_CONFIG)
    if settings_dict:
        for k in DEFAULT_THERMAL_CONFIG:
            if k in settings_dict and settings_dict[k] is not None:
                cfg[k] = str(settings_dict[k])
    return cfg


def list_windows_printers() -> dict:
    if not PYWIN32_AVAILABLE:
        return {
            "pywin32_available": False,
            "default_printer": "",
            "printers": [],
        }
    default_printer = ""
    try:
        default_printer = win32print.GetDefaultPrinter() or ""
    except Exception:
        default_printer = ""

    printers = []
    try:
        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        for p in win32print.EnumPrinters(flags):
            # p[2] is printer name
            if len(p) > 2 and p[2]:
                printers.append(str(p[2]))
    except Exception:
        if default_printer:
            printers.append(default_printer)

    if default_printer and default_printer not in printers:
        printers.insert(0, default_printer)

    return {
        "pywin32_available": True,
        "default_printer": default_printer,
        "printers": printers,
    }


def resolve_style_index(style_mode: str = "auto_daily", dt: datetime.datetime | None = None) -> int:
    mode = (style_mode or "auto_daily").strip()
    if mode == "auto_daily":
        today_str = (dt or datetime.datetime.now()).strftime("%Y-%m-%d")
        return random.Random(today_str).randint(1, 7)
    try:
        idx = int(mode)
        return max(1, min(8, idx))
    except Exception:
        return 8


def check_and_mark_duplicate(code: str, group: str, guard_sec: int, force_confirm: bool = False) -> bool:
    """Returns True if this print is blocked as a recent duplicate and force_confirm is False."""
    if force_confirm or guard_sec <= 0:
        _RECENT_PRINTS[(str(code).strip(), str(group).strip())] = time.time()
        return False
    key = (str(code).strip(), str(group).strip())
    now_ts = time.time()
    prev_ts = _RECENT_PRINTS.get(key)
    if prev_ts is not None and (now_ts - prev_ts) < guard_sec:
        return True
    _RECENT_PRINTS[key] = now_ts
    # Prune stale keys periodically
    if len(_RECENT_PRINTS) > 400:
        cutoff = now_ts - max(guard_sec, 600)
        for k, ts in list(_RECENT_PRINTS.items()):
            if ts < cutoff:
                _RECENT_PRINTS.pop(k, None)
    return False


def _prepare_receipt_fields(payload: dict, cfg: dict) -> dict:
    now = datetime.datetime.now()
    day_ar = DAY_NAMES_AR.get(now.strftime("%a"), now.strftime("%a"))
    date_str = f"{day_ar} {now.strftime('%m/%d')}"
    time_str = now.strftime("%I:%M %p")

    raw_name = str(payload.get("name") or "طالب")
    is_guest = bool(payload.get("is_guest")) or ("طالب ضيف" in raw_name)
    name_str = raw_name.replace("طالب ضيف", "").strip() or "طالب"

    code = str(payload.get("code") or "---").strip() or "---"
    raw_group = str(payload.get("group") or "---").strip()
    if raw_group and "تم الحضور" not in raw_group:
        group_display = f"مجموعة: {raw_group}"
    else:
        group_display = raw_group or "---"

    show_stage = str(cfg.get("thermal_show_stage", "1")) != "0"
    raw_stage = str(payload.get("stage") or "").strip() if show_stage else ""

    show_amount = str(cfg.get("thermal_show_amount", "1")) != "0"
    amount_paid = payload.get("amount_paid")
    receipt_no = str(payload.get("receipt_no") or "").strip()
    payment_line = ""
    if show_amount:
        parts = []
        if amount_paid is not None and str(amount_paid) != "":
            try:
                amt_val = float(amount_paid)
                if amt_val > 0:
                    amt_txt = f"{int(amt_val) if amt_val.is_integer() else round(amt_val, 2)} ج.م"
                    parts.append(f"المدفوع: {amt_txt}")
            except (TypeError, ValueError):
                pass
        if receipt_no:
            parts.append(f"إيصال #{receipt_no}")
        payment_line = "  |  ".join(parts)

    header_title = str(cfg.get("thermal_header_title") or "").strip()
    footer_text = str(cfg.get("thermal_footer_text") or "").strip()

    try:
        scale = max(0.5, min(1.25, float(cfg.get("thermal_scale") or 0.75)))
    except (TypeError, ValueError):
        scale = 0.75

    style_index = payload.get("style_override")
    if style_index is not None:
        try:
            style_index = max(1, min(8, int(style_index)))
        except (TypeError, ValueError):
            style_index = resolve_style_index(cfg.get("thermal_style_mode", "auto_daily"), now)
    else:
        style_index = resolve_style_index(cfg.get("thermal_style_mode", "auto_daily"), now)

    return {
        "now": now,
        "day_ar": day_ar,
        "date_str": date_str,
        "time_str": time_str,
        "name_str": name_str,
        "is_guest": is_guest,
        "code": code,
        "raw_group": raw_group,
        "group_display": group_display,
        "raw_stage": raw_stage,
        "payment_line": payment_line,
        "header_title": header_title,
        "footer_text": footer_text,
        "success_text": "تم تسجيل الحضور بنجاح",
        "scale": scale,
        "font_main": str(cfg.get("thermal_font_name") or "IBM Plex Sans Arabic").strip() or "IBM Plex Sans Arabic",
        "style_index": style_index,
    }


def _draw_text(dc, text, x, y, font_object, text_color=0x000000):
    dc.SelectObject(font_object)
    dc.SetTextColor(text_color)
    dc.SetBkMode(win32con.TRANSPARENT)
    dc.TextOut(int(x), int(y), text)


def _draw_centered_text(dc, text, y, page_width, font_object, text_color=0x000000):
    dc.SelectObject(font_object)
    dc.SetTextColor(text_color)
    dc.SetBkMode(win32con.TRANSPARENT)
    try:
        size = dc.GetTextExtent(text)
        text_width = size[0]
        x = (page_width - text_width) // 2
        dc.TextOut(int(x), int(y), text)
        return text_width, size[1]
    except Exception:
        return 100, 35


def print_thermal_receipt_sync(payload: dict, cfg: dict) -> dict:
    """Renders receipt directly to the selected Windows thermal printer via GDI."""
    if not PYWIN32_AVAILABLE:
        return {"ok": False, "error": "مكتبة الطباعة pywin32 غير متوفرة في بيئة Python الحالية"}

    fields = _prepare_receipt_fields(payload, cfg)
    printer_name = (cfg.get("thermal_printer_name") or "").strip()
    if not printer_name:
        try:
            printer_name = win32print.GetDefaultPrinter()
        except Exception:
            printer_name = ""
    if not printer_name:
        return {"ok": False, "error": "لا توجد طابعة افتراضية معرفة في الويندوز"}

    SCALE = fields["scale"]
    S = lambda v: int(v * SCALE)
    font_main = fields["font_main"]
    style_index = fields["style_index"]

    with _PRINT_LOCK:
        hDC = None
        try:
            hDC = win32ui.CreateDC()
            hDC.CreatePrinterDC(printer_name)
            hDC.StartDoc("Center ERP Thermal Receipt")
            hDC.StartPage()

            PAGE_WIDTH = hDC.GetDeviceCaps(win32con.HORZRES)
            name_str = fields["name_str"]
            code = fields["code"]
            group_display = fields["group_display"]
            raw_stage = fields["raw_stage"]
            date_str = fields["date_str"]
            time_str = fields["time_str"]
            day_ar = fields["day_ar"]
            success_text = fields["success_text"]

            font_date = win32ui.CreateFont({"name": font_main, "height": S(48), "weight": 900})
            font_time = win32ui.CreateFont({"name": "Courier New", "height": S(24), "weight": 900})
            font_success = win32ui.CreateFont({"name": font_main, "height": S(24), "weight": 800})
            font_code = win32ui.CreateFont({"name": "Courier New", "height": S(50), "weight": 900})
            font_group = win32ui.CreateFont({"name": font_main, "height": S(28), "weight": 900})
            font_stage = win32ui.CreateFont({"name": font_main, "height": S(22), "weight": 600})
            font_footer2 = win32ui.CreateFont({"name": font_main, "height": S(22), "weight": 800})

            name_font_height = S(50)
            font_name_obj = win32ui.CreateFont({"name": font_main, "height": name_font_height, "weight": 900})
            hDC.SelectObject(font_name_obj)
            n_w, _n_h = hDC.GetTextExtent(name_str)
            max_name_width = max(S(120), PAGE_WIDTH - S(20))
            if n_w > max_name_width and n_w > 0:
                ratio = max_name_width / float(n_w)
                name_font_height = max(S(28), int(name_font_height * ratio))
                font_name_obj = win32ui.CreateFont({"name": font_main, "height": name_font_height, "weight": 900})

            pen_solid = win32ui.CreatePen(win32con.PS_SOLID, 2, 0x000000)
            pen_thin = win32ui.CreatePen(win32con.PS_SOLID, 1, 0x000000)
            brush_white = win32ui.CreateBrush(win32con.BS_SOLID, 0xFFFFFF, 0)
            brush_black = win32ui.CreateBrush(win32con.BS_SOLID, 0x000000, 0)

            y = S(5)

            if fields["header_title"] and style_index != 8:
                font_hdr = win32ui.CreateFont({"name": font_main, "height": S(26), "weight": 900})
                _draw_centered_text(hDC, fields["header_title"], y, PAGE_WIDTH, font_hdr)
                y += S(28)

            if style_index == 1:
                hDC.SelectObject(pen_solid)
                _draw_centered_text(hDC, f"{date_str}  |  {time_str}", y, PAGE_WIDTH, font_date)
                y += S(45)
                hDC.MoveTo(S(30), int(y))
                hDC.LineTo(PAGE_WIDTH - S(30), int(y))
                y += S(8)
                _draw_centered_text(hDC, success_text, y, PAGE_WIDTH, font_success)
                y += S(30)
                _draw_centered_text(hDC, name_str, y, PAGE_WIDTH, font_name_obj)
                y += S(48)
                _draw_centered_text(hDC, f"ID: {code}", y, PAGE_WIDTH, font_code)
                y += S(48)
                hDC.MoveTo(S(50), int(y))
                hDC.LineTo(PAGE_WIDTH - S(50), int(y))
                y += S(8)
                _draw_centered_text(hDC, group_display, y, PAGE_WIDTH, font_group)
                y += S(30)
                if raw_stage:
                    _draw_centered_text(hDC, raw_stage, y, PAGE_WIDTH, font_stage)
                    y += S(25)

            elif style_index == 2:
                hDC.SelectObject(pen_solid)
                _draw_centered_text(hDC, f"{date_str}   {time_str}", y, PAGE_WIDTH, font_date)
                y += S(45)
                hDC.MoveTo(S(20), int(y))
                hDC.LineTo(PAGE_WIDTH - S(20), int(y))
                y += S(8)
                _draw_centered_text(hDC, success_text, y, PAGE_WIDTH, font_success)
                y += S(28)
                hDC.MoveTo(S(20), int(y))
                hDC.LineTo(PAGE_WIDTH - S(20), int(y))
                y += S(12)
                _draw_centered_text(hDC, name_str, y, PAGE_WIDTH, font_name_obj)
                y += S(48)
                _draw_centered_text(hDC, f"ID: {code}", y, PAGE_WIDTH, font_code)
                y += S(48)
                hDC.MoveTo(S(20), int(y))
                hDC.LineTo(PAGE_WIDTH - S(20), int(y))
                y += S(8)
                _draw_centered_text(hDC, group_display, y, PAGE_WIDTH, font_group)
                y += S(30)
                if raw_stage:
                    _draw_centered_text(hDC, raw_stage, y, PAGE_WIDTH, font_stage)
                    y += S(25)

            elif style_index == 3:
                font_massive = win32ui.CreateFont({"name": font_main, "height": S(60), "weight": 900})
                _draw_centered_text(hDC, day_ar, y, PAGE_WIDTH, font_massive)
                y += S(55)
                _draw_centered_text(hDC, f"{date_str} - {time_str}", y, PAGE_WIDTH, font_date)
                y += S(45)
                _draw_centered_text(hDC, success_text, y, PAGE_WIDTH, font_success)
                y += S(30)
                hDC.SelectObject(pen_solid)
                hDC.SelectObject(brush_white)
                win32gui.Ellipse(hDC.GetSafeHdc(), S(20), int(y), int(PAGE_WIDTH - S(20)), int(y + S(130)))
                _draw_centered_text(hDC, name_str, y + S(25), PAGE_WIDTH, font_name_obj)
                _draw_centered_text(hDC, f"ID: {code}", y + S(70), PAGE_WIDTH, font_code)
                y += S(140)
                _draw_centered_text(hDC, group_display, y, PAGE_WIDTH, font_group)
                y += S(30)
                if raw_stage:
                    _draw_centered_text(hDC, raw_stage, y, PAGE_WIDTH, font_stage)
                    y += S(25)

            elif style_index == 4:
                if fields["footer_text"]:
                    _draw_centered_text(hDC, fields["footer_text"], y, PAGE_WIDTH, font_footer2)
                    y += S(25)
                hDC.SelectObject(brush_black)
                win32gui.RoundRect(hDC.GetSafeHdc(), S(20), int(y), int(PAGE_WIDTH - S(20)), int(y + S(55)), S(20), S(20))
                _draw_centered_text(hDC, f"ID: {code}", y + S(5), PAGE_WIDTH, font_code, text_color=0xFFFFFF)
                y += S(70)
                _draw_centered_text(hDC, name_str, y, PAGE_WIDTH, font_name_obj)
                y += S(50)
                _draw_centered_text(hDC, group_display, y, PAGE_WIDTH, font_group)
                y += S(30)
                if raw_stage:
                    _draw_centered_text(hDC, raw_stage, y, PAGE_WIDTH, font_stage)
                    y += S(25)
                _draw_centered_text(hDC, success_text, y, PAGE_WIDTH, font_success)
                y += S(25)
                _draw_centered_text(hDC, f"{date_str} - {time_str}", y, PAGE_WIDTH, font_date)
                y += S(40)

            elif style_index == 5:
                font_giant_code = win32ui.CreateFont({"name": "Courier New", "height": S(60), "weight": 900})
                _draw_centered_text(hDC, f"ID: {code}", y, PAGE_WIDTH, font_giant_code)
                y += S(65)
                hDC.SelectObject(pen_solid)
                hDC.MoveTo(S(50), int(y))
                hDC.LineTo(PAGE_WIDTH - S(50), int(y))
                y += S(10)
                _draw_centered_text(hDC, name_str, y, PAGE_WIDTH, font_name_obj)
                y += S(50)
                _draw_centered_text(hDC, group_display, y, PAGE_WIDTH, font_group)
                y += S(30)
                if raw_stage:
                    _draw_centered_text(hDC, raw_stage, y, PAGE_WIDTH, font_stage)
                    y += S(25)
                hDC.SelectObject(brush_black)
                win32gui.RoundRect(hDC.GetSafeHdc(), S(30), int(y), int(PAGE_WIDTH - S(30)), int(y + S(35)), S(10), S(10))
                _draw_centered_text(hDC, success_text, y + S(5), PAGE_WIDTH, font_success, text_color=0xFFFFFF)
                y += S(45)
                _draw_centered_text(hDC, f"{date_str}  |  {time_str}", y, PAGE_WIDTH, font_date)
                y += S(40)

            elif style_index == 6:
                hDC.SelectObject(font_date)
                d_w, d_h = hDC.GetTextExtent(date_str)
                hDC.SelectObject(font_time)
                t_w, t_h = hDC.GetTextExtent(time_str)
                hDC.SelectObject(font_success)
                s_w, s_h = hDC.GetTextExtent(success_text)

                pad_x = S(10)
                pad_y = S(6)
                box_h = max(d_h, t_h, s_h) + (pad_y * 2)
                hDC.SelectObject(pen_solid)

                hDC.SelectObject(brush_white)
                d_x2 = PAGE_WIDTH - S(8)
                d_x1 = d_x2 - d_w - (pad_x * 2)
                win32gui.RoundRect(hDC.GetSafeHdc(), int(d_x1), int(y), int(d_x2), int(y + box_h), S(15), S(15))
                _draw_text(hDC, date_str, d_x1 + pad_x, y + pad_y, font_date)

                hDC.SelectObject(brush_white)
                t_x1 = S(8)
                t_x2 = t_x1 + t_w + (pad_x * 2)
                win32gui.RoundRect(hDC.GetSafeHdc(), int(t_x1), int(y), int(t_x2), int(y + box_h), S(15), S(15))
                _draw_text(hDC, time_str, t_x1 + pad_x, y + pad_y + (d_h - t_h) // 2, font_time)

                hDC.SelectObject(brush_black)
                s_x1 = (PAGE_WIDTH - s_w) // 2 - pad_x
                s_x2 = s_x1 + s_w + (pad_x * 2)
                win32gui.RoundRect(hDC.GetSafeHdc(), int(s_x1), int(y), int(s_x2), int(y + box_h), S(15), S(15))
                _draw_centered_text(hDC, success_text, y + pad_y + (d_h - s_h) // 2, PAGE_WIDTH, font_success, text_color=0xFFFFFF)

                y += box_h + S(15)

                code_text = f" ID: {code} "
                hDC.SelectObject(font_code)
                c_w, c_h = hDC.GetTextExtent(code_text)
                c_box_w, c_box_h = c_w + S(15), c_h + S(10)

                hDC.SelectObject(font_name_obj)
                n_w, n_h = hDC.GetTextExtent(name_str)
                row_h = max(n_h, c_box_h)

                c_y = y + (row_h - c_box_h) // 2
                hDC.SelectObject(pen_solid)
                hDC.SelectObject(brush_white)
                win32gui.RoundRect(hDC.GetSafeHdc(), S(10), int(c_y), int(S(10) + c_box_w), int(c_y + c_box_h), S(15), S(15))
                _draw_text(hDC, code_text, S(18), c_y + S(5), font_code)

                n_x = PAGE_WIDTH - S(10) - n_w
                n_y = y + (row_h - n_h) // 2
                _draw_text(hDC, name_str, n_x, n_y, font_name_obj)
                y += row_h + S(15)

                hDC.SelectObject(font_group)
                _g_w, g_h = hDC.GetTextExtent(group_display)
                hDC.SelectObject(font_stage)
                stg_h = hDC.GetTextExtent(raw_stage)[1] if raw_stage else 0
                rect_pad = S(8)
                rect_y2 = y + g_h + (stg_h + S(5) if raw_stage else 0) + (rect_pad * 2)
                hDC.SelectObject(pen_solid)
                hDC.SelectObject(brush_black)
                win32gui.RoundRect(hDC.GetSafeHdc(), S(10), int(y), int(PAGE_WIDTH - S(10)), int(rect_y2), S(20), S(20))
                _draw_centered_text(hDC, group_display, y + rect_pad, PAGE_WIDTH, font_group, text_color=0xFFFFFF)
                if raw_stage:
                    _draw_centered_text(hDC, raw_stage, y + rect_pad + g_h + S(5), PAGE_WIDTH, font_stage, text_color=0xFFFFFF)
                y = rect_y2 + S(15)

            elif style_index == 7:
                hDC.SelectObject(pen_solid)
                hDC.SelectObject(brush_white)
                hDC.SelectObject(font_date)
                d_w, d_h = hDC.GetTextExtent(date_str)
                hDC.SelectObject(font_time)
                t_w, t_h = hDC.GetTextExtent(time_str)
                box_h = d_h + S(8)

                win32gui.RoundRect(hDC.GetSafeHdc(), S(8), int(y), int(S(8) + t_w + S(12)), int(y + box_h), S(12), S(12))
                _draw_text(hDC, time_str, S(14), y + S(4) + (box_h - t_h) // 2, font_time)
                d_x1 = PAGE_WIDTH - S(8) - d_w - S(12)
                win32gui.RoundRect(hDC.GetSafeHdc(), int(d_x1), int(y), int(PAGE_WIDTH - S(8)), int(y + box_h), S(12), S(12))
                _draw_text(hDC, date_str, d_x1 + S(6), y + S(4), font_date)
                hDC.SelectObject(font_success)
                s_w, s_h = hDC.GetTextExtent(success_text)
                s_x1 = (PAGE_WIDTH - s_w) // 2 - S(8)
                hDC.SelectObject(brush_black)
                win32gui.RoundRect(hDC.GetSafeHdc(), int(s_x1), int(y), int(s_x1 + s_w + S(16)), int(y + box_h), S(12), S(12))
                _draw_centered_text(hDC, success_text, y + S(4) + (box_h - s_h) // 2, PAGE_WIDTH, font_success, text_color=0xFFFFFF)
                y += box_h + S(15)

                hDC.SelectObject(brush_white)
                hDC.SelectObject(font_code)
                c_w, c_h = hDC.GetTextExtent(f" ID: {code} ")
                win32gui.RoundRect(hDC.GetSafeHdc(), S(10), int(y), int(S(10) + c_w + S(10)), int(y + c_h + S(10)), S(12), S(12))
                _draw_text(hDC, f" ID: {code} ", S(15), y + S(5), font_code)
                hDC.SelectObject(font_name_obj)
                n_w, n_h = hDC.GetTextExtent(name_str)
                _draw_text(hDC, name_str, PAGE_WIDTH - S(10) - n_w, y + S(8), font_name_obj)
                y += max(c_h + S(10), n_h) + S(15)

                hDC.SelectObject(brush_black)
                hDC.SelectObject(font_group)
                g_h = hDC.GetTextExtent(group_display)[1]
                hDC.SelectObject(font_stage)
                stg_h = hDC.GetTextExtent(raw_stage)[1] if raw_stage else 0
                win32gui.RoundRect(hDC.GetSafeHdc(), S(10), int(y), int(PAGE_WIDTH - S(10)), int(y + g_h + (stg_h + S(5) if raw_stage else 0) + S(12)), S(15), S(15))
                _draw_centered_text(hDC, group_display, y + S(6), PAGE_WIDTH, font_group, text_color=0xFFFFFF)
                if raw_stage:
                    _draw_centered_text(hDC, raw_stage, y + S(6) + g_h + S(5), PAGE_WIDTH, font_stage, text_color=0xFFFFFF)
                y += g_h + (stg_h + S(5) if raw_stage else 0) + S(20)

            else:  # Style 8: Modern Alawael Ticket
                hDC.SelectObject(pen_solid)
                hdr = fields["header_title"] or "سنتر الأوائل التعليمي"
                hDC.SelectObject(brush_black)
                win32gui.RoundRect(hDC.GetSafeHdc(), S(8), int(y), int(PAGE_WIDTH - S(8)), int(y + S(38)), S(14), S(14))
                _draw_centered_text(hDC, hdr, y + S(7), PAGE_WIDTH, font_success, text_color=0xFFFFFF)
                y += S(44)
                _draw_centered_text(hDC, f"{date_str}  •  {time_str}", y, PAGE_WIDTH, font_stage)
                y += S(28)
                hDC.SelectObject(brush_white)
                win32gui.RoundRect(hDC.GetSafeHdc(), S(12), int(y), int(PAGE_WIDTH - S(12)), int(y + S(104)), S(16), S(16))
                _draw_centered_text(hDC, name_str, y + S(10), PAGE_WIDTH, font_name_obj)
                _draw_centered_text(hDC, f"ID: {code}", y + S(56), PAGE_WIDTH, font_code)
                y += S(114)
                _draw_centered_text(hDC, group_display, y, PAGE_WIDTH, font_group)
                y += S(28)
                if raw_stage:
                    _draw_centered_text(hDC, raw_stage, y, PAGE_WIDTH, font_stage)
                    y += S(22)
                _draw_centered_text(hDC, f"✓ {success_text}", y, PAGE_WIDTH, font_success)
                y += S(26)

            if fields["payment_line"]:
                _draw_centered_text(hDC, fields["payment_line"], y, PAGE_WIDTH, font_stage)
                y += S(24)

            TARGET_Y = S(340)
            if y < TARGET_Y:
                y = TARGET_Y

            if fields["is_guest"]:
                guest_text = "طالب ضيف"
                font_guest_obj = win32ui.CreateFont({"name": font_main, "height": S(30), "weight": 900})
                hDC.SelectObject(font_guest_obj)
                g_w, _g_h = hDC.GetTextExtent(guest_text)
                g_box_w = g_w + S(30)
                g_x1 = (PAGE_WIDTH - g_box_w) // 2
                hDC.SelectObject(pen_solid)
                hDC.SelectObject(brush_black)
                win32gui.RoundRect(hDC.GetSafeHdc(), int(g_x1), int(y), int(g_x1 + g_box_w), int(y + S(35)), S(15), S(15))
                _draw_text(hDC, guest_text, g_x1 + S(15), y + S(4), font_guest_obj, text_color=0xFFFFFF)
                y += S(45)

            if style_index != 4 and fields["footer_text"]:
                hDC.SelectObject(pen_thin)
                hDC.MoveTo(S(30), int(y))
                hDC.LineTo(PAGE_WIDTH - S(30), int(y))
                y += S(8)
                _draw_centered_text(hDC, fields["footer_text"], y, PAGE_WIDTH, font_footer2)
                y += S(20)

            hDC.EndPage()
            hDC.EndDoc()
            return {"ok": True, "printer": printer_name, "style_index": style_index}
        except Exception as e:
            traceback.print_exc()
            return {"ok": False, "error": str(e)}
        finally:
            try:
                if hDC is not None:
                    hDC.DeleteDC()
            except Exception:
                pass


def print_thermal_receipt_async(payload: dict, cfg: dict) -> dict:
    fields = _prepare_receipt_fields(payload, cfg)
    printer_name = (cfg.get("thermal_printer_name") or "").strip() or (list_windows_printers().get("default_printer") or "")
    t = threading.Thread(
        target=print_thermal_receipt_sync,
        args=(dict(payload), dict(cfg)),
        daemon=True,
    )
    t.start()
    return {
        "ok": True,
        "queued": True,
        "printer": printer_name,
        "style_index": fields["style_index"],
    }


def render_receipt_preview_html(payload: dict, cfg: dict) -> tuple[str, int]:
    """Generates a faithful 80mm thermal paper HTML preview without triggering hardware printing."""
    f = _prepare_receipt_fields(payload, cfg)
    esc = html.escape
    st_idx = f["style_index"]
    zoom = round(min(1.18, max(0.76, f["scale"] / 0.75)), 2)

    hdr_html = f'<div class="tp-hdr-custom">{esc(f["header_title"])}</div>' if (f["header_title"] and st_idx != 8) else ""
    stage_html = f'<div class="tp-stage">{esc(f["raw_stage"])}</div>' if f["raw_stage"] else ""
    stage_inv_html = f'<div class="tp-stage-inv">{esc(f["raw_stage"])}</div>' if f["raw_stage"] else ""
    pay_html = f'<div class="tp-pay">{esc(f["payment_line"])}</div>' if f["payment_line"] else ""
    guest_html = '<div class="tp-guest">طالب ضيف</div>' if f["is_guest"] else ""
    footer_html = (
        f'<div class="tp-footer">{esc(f["footer_text"])}</div>'
        if (f["footer_text"] and st_idx != 4) else ""
    )

    if st_idx == 1:
        body_html = f"""
          {hdr_html}
          <div class="tp-dt-big">{esc(f['date_str'])} &nbsp;|&nbsp; <span dir="ltr">{esc(f['time_str'])}</span></div>
          <div class="tp-hr-solid"></div>
          <div class="tp-success">{esc(f['success_text'])}</div>
          <div class="tp-name">{esc(f['name_str'])}</div>
          <div class="tp-code" dir="ltr">ID: {esc(f['code'])}</div>
          <div class="tp-hr-mid"></div>
          <div class="tp-group">{esc(f['group_display'])}</div>
          {stage_html}
        """
    elif st_idx == 2:
        body_html = f"""
          {hdr_html}
          <div class="tp-dt-big">{esc(f['date_str'])} &nbsp;&nbsp; <span dir="ltr">{esc(f['time_str'])}</span></div>
          <div class="tp-hr-full"></div>
          <div class="tp-success">{esc(f['success_text'])}</div>
          <div class="tp-hr-full"></div>
          <div class="tp-name" style="margin-top:8px;">{esc(f['name_str'])}</div>
          <div class="tp-code" dir="ltr">ID: {esc(f['code'])}</div>
          <div class="tp-hr-full"></div>
          <div class="tp-group">{esc(f['group_display'])}</div>
          {stage_html}
        """
    elif st_idx == 3:
        body_html = f"""
          {hdr_html}
          <div class="tp-massive">{esc(f['day_ar'])}</div>
          <div class="tp-dt-big">{esc(f['date_str'])} - <span dir="ltr">{esc(f['time_str'])}</span></div>
          <div class="tp-success">{esc(f['success_text'])}</div>
          <div class="tp-ellipse">
            <div class="tp-name">{esc(f['name_str'])}</div>
            <div class="tp-code" dir="ltr">ID: {esc(f['code'])}</div>
          </div>
          <div class="tp-group">{esc(f['group_display'])}</div>
          {stage_html}
        """
    elif st_idx == 4:
        warn_top = f'<div class="tp-warn-top">{esc(f["footer_text"])}</div>' if f["footer_text"] else ""
        body_html = f"""
          {hdr_html}
          {warn_top}
          <div class="tp-black-pill" dir="ltr">ID: {esc(f['code'])}</div>
          <div class="tp-name" style="margin:6px 0;">{esc(f['name_str'])}</div>
          <div class="tp-group">{esc(f['group_display'])}</div>
          {stage_html}
          <div class="tp-success">{esc(f['success_text'])}</div>
          <div class="tp-dt-big">{esc(f['date_str'])} - <span dir="ltr">{esc(f['time_str'])}</span></div>
        """
    elif st_idx == 5:
        body_html = f"""
          {hdr_html}
          <div class="tp-code-giant" dir="ltr">ID: {esc(f['code'])}</div>
          <div class="tp-hr-mid"></div>
          <div class="tp-name">{esc(f['name_str'])}</div>
          <div class="tp-group">{esc(f['group_display'])}</div>
          {stage_html}
          <div class="tp-black-bar">{esc(f['success_text'])}</div>
          <div class="tp-dt-big">{esc(f['date_str'])} &nbsp;|&nbsp; <span dir="ltr">{esc(f['time_str'])}</span></div>
        """
    elif st_idx == 6:
        body_html = f"""
          {hdr_html}
          <div class="tp-cap-row">
            <span class="tp-cap-white">{esc(f['date_str'])}</span>
            <span class="tp-cap-black">{esc(f['success_text'])}</span>
            <span class="tp-cap-white" dir="ltr">{esc(f['time_str'])}</span>
          </div>
          <div class="tp-name-code-row">
            <span class="tp-name-inline">{esc(f['name_str'])}</span>
            <span class="tp-code-pill" dir="ltr">ID: {esc(f['code'])}</span>
          </div>
          <div class="tp-group-black">
            <div class="tp-group" style="color:#fff;">{esc(f['group_display'])}</div>
            {stage_inv_html}
          </div>
        """
    elif st_idx == 7:
        body_html = f"""
          {hdr_html}
          <div class="tp-cap-row">
            <span class="tp-cap-white" style="border-radius:8px;">{esc(f['date_str'])}</span>
            <span class="tp-cap-black" style="border-radius:8px;">{esc(f['success_text'])}</span>
            <span class="tp-cap-white" style="border-radius:8px;" dir="ltr">{esc(f['time_str'])}</span>
          </div>
          <div class="tp-name-code-row">
            <span class="tp-name-inline">{esc(f['name_str'])}</span>
            <span class="tp-code-pill" style="border-radius:8px;" dir="ltr">ID: {esc(f['code'])}</span>
          </div>
          <div class="tp-group-black" style="border-radius:10px;">
            <div class="tp-group" style="color:#fff;">{esc(f['group_display'])}</div>
            {stage_inv_html}
          </div>
        """
    else:  # Style 8: Modern Alawael Ticket
        hdr_title = f["header_title"] or "سنتر الأوائل التعليمي"
        body_html = f"""
          <div class="tp-modern-hdr">{esc(hdr_title)}</div>
          <div class="tp-modern-dt">{esc(f['date_str'])} &nbsp;•&nbsp; <span dir="ltr">{esc(f['time_str'])}</span></div>
          <div class="tp-modern-card">
            <div class="tp-name">{esc(f['name_str'])}</div>
            <div class="tp-modern-id" dir="ltr">ID: {esc(f['code'])}</div>
          </div>
          <div class="tp-group">{esc(f['group_display'])}</div>
          {stage_html}
          <div class="tp-modern-ok">✓ {esc(f['success_text'])}</div>
        """

    preview_html = f"""
      <div class="tp-paper-wrap">
        <div class="tp-paper" style="transform:scale({zoom});transform-origin:top center;" dir="rtl">
          <div class="tp-badge-style">استايل #{st_idx}</div>
          {body_html}
          {pay_html}
          {guest_html}
          {footer_html}
        </div>
      </div>
    """
    return preview_html, st_idx
