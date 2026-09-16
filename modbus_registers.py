"""Register catalog for the Modbus dashboard.

Implements the "Official 2024" LuxPower register map used by lxp-bridge and the
Home Assistant integration (https://github.com/ant0nkr/luxpower-ha-integration).
Items are grouped into categories matching the luxCloud app:

    Beep Audio / Application setting / Charge setting / Discharge setting / Battery

Each item describes how its value maps to a single holding register:

  kind
    toggle  - a single bit inside a 16-bit register (``bit``); whole-register
              values are also supported when ``bit`` is None.
    select  - discrete value ``options``; optionally a masked bit range via
              ``bit0`` + ``bitwidth``.
    number  - numeric range min/max (in display units) with optional ``scale``
              (raw = round(value / scale)); unit follows ``unit``.
    time    - packed ``(minute << 8) | hour``, value exchanged as "HH:MM"

  flags
    danger  - hidden behind the "Advanced" checkbox and rendered with a warning
              background in the UI; writes require explicit confirmation.
    verify  - not 100% confirmed against the real device; show a hint asking the
              user to verify the result after writing.
"""

KIND_TOGGLE = "toggle"
KIND_SELECT = "select"
KIND_NUMBER = "number"
KIND_TIME = "time"

CATEGORY_BEEP = "beep"
CATEGORY_APPLICATION = "application"
CATEGORY_CHARGE = "charge"
CATEGORY_DISCHARGE = "discharge"
CATEGORY_BATTERY = "battery"

CATEGORIES = [
    {"key": CATEGORY_BEEP, "name_en": "Beep Audio Enable/Disable", "name_vi": "Âm thanh còi báo"},
    {"key": CATEGORY_APPLICATION, "name_en": "Application setting", "name_vi": "Cài đặt ứng dụng"},
    {"key": CATEGORY_CHARGE, "name_en": "Charge setting", "name_vi": "Cài đặt sạc"},
    {"key": CATEGORY_DISCHARGE, "name_en": "Discharge setting", "name_vi": "Cài đặt xả"},
    {"key": CATEGORY_BATTERY, "name_en": "Battery", "name_vi": "Pin lưu trữ"},
]

_ITEMS = [
    # ---- Beep Audio ----
    {
        "key": "buzzer",
        "category": CATEGORY_BEEP,
        "name_en": "Beep audio",
        "name_vi": "âm thanh còi báo",
        "reg": 110,
        "kind": KIND_TOGGLE,
        "bit": 7,
    },

    # ---- Application setting ----
    {
        "key": "eps_voltage",
        "category": CATEGORY_APPLICATION,
        "name_en": "EPS output voltage",
        "name_vi": "Điện áp ngõ ra EPS",
        "reg": 90,
        "kind": KIND_SELECT,
        "options": [
            {"value": 208, "label_en": "208 V", "label_vi": "208 V"},
            {"value": 220, "label_en": "220 V", "label_vi": "220 V"},
            {"value": 230, "label_en": "230 V", "label_vi": "230 V"},
            {"value": 240, "label_en": "240 V", "label_vi": "240 V"},
            {"value": 277, "label_en": "277 V", "label_vi": "277 V"},
        ],
        "danger": True,
    },
    {
        "key": "eps_frequency",
        "category": CATEGORY_APPLICATION,
        "name_en": "EPS output frequency",
        "name_vi": "Tần số ngõ ra EPS",
        "reg": 91,
        "kind": KIND_SELECT,
        "options": [
            {"value": 50, "label_en": "50 Hz", "label_vi": "50 Hz"},
            {"value": 60, "label_en": "60 Hz", "label_vi": "60 Hz"},
        ],
        "danger": True,
    },
    {
        "key": "eps_seamless",
        "category": CATEGORY_APPLICATION,
        "name_en": "Seamless EPS switching",
        "name_vi": "Chuyển mạch EPS không gián đoạn",
        "reg": 21,
        "kind": KIND_TOGGLE,
        "bit": 8,
    },
    {
        "key": "hybrid",
        "category": CATEGORY_APPLICATION,
        "name_en": "Hybrid (EPS power backup)",
        "name_vi": "Hybrid (dự phòng điện EPS)",
        "reg": 21,
        "kind": KIND_TOGGLE,
        "bit": 0,
        "danger": True,
        "verify": True,
    },
    {
        "key": "grid_export_enable",
        "category": CATEGORY_APPLICATION,
        "name_en": "Grid export",
        "name_vi": "Cho phép xuất điện lưới",
        "reg": 21,
        "kind": KIND_TOGGLE,
        "bit": 15,
        "danger": True,
    },
    {
        "key": "grid_export_percent",
        "category": CATEGORY_APPLICATION,
        "name_en": "Grid export percent",
        "name_vi": "Phần trăm xuất điện lưới",
        "reg": 103,
        "kind": KIND_NUMBER,
        "unit": "%",
        "min": 0,
        "max": 100,
        "danger": True,
    },
    {
        "key": "ac_first_time_base_start",
        "category": CATEGORY_APPLICATION,
        "name_en": "AC first timer - base start",
        "name_vi": "Hẹn giờ ưu tiên AC - bắt đầu cơ bản",
        "reg": 152,
        "kind": KIND_TIME,
    },
    {
        "key": "ac_first_time_base_end",
        "category": CATEGORY_APPLICATION,
        "name_en": "AC first timer - base end",
        "name_vi": "Hẹn giờ ưu tiên AC - kết thúc cơ bản",
        "reg": 153,
        "kind": KIND_TIME,
    },
    {
        "key": "ac_first_time_p1_start",
        "category": CATEGORY_APPLICATION,
        "name_en": "AC first timer - period 1 start",
        "name_vi": "Hẹn giờ ưu tiên AC - bắt đầu chu kỳ 1",
        "reg": 154,
        "kind": KIND_TIME,
    },
    {
        "key": "ac_first_time_p1_end",
        "category": CATEGORY_APPLICATION,
        "name_en": "AC first timer - period 1 end",
        "name_vi": "Hẹn giờ ưu tiên AC - kết thúc chu kỳ 1",
        "reg": 155,
        "kind": KIND_TIME,
    },
    {
        "key": "ac_first_time_p2_start",
        "category": CATEGORY_APPLICATION,
        "name_en": "AC first timer - period 2 start",
        "name_vi": "Hẹn giờ ưu tiên AC - bắt đầu chu kỳ 2",
        "reg": 156,
        "kind": KIND_TIME,
    },
    {
        "key": "ac_first_time_p2_end",
        "category": CATEGORY_APPLICATION,
        "name_en": "AC first timer - period 2 end",
        "name_vi": "Hẹn giờ ưu tiên AC - kết thúc chu kỳ 2",
        "reg": 157,
        "kind": KIND_TIME,
    },
    {
        "key": "output_priority",
        "category": CATEGORY_APPLICATION,
        "name_en": "Output priority",
        "name_vi": "Ưu tiên nguồn ngõ ra",
        "reg": 145,
        "kind": KIND_SELECT,
        "options": [
            {"value": 0, "label_en": "Battery first", "label_vi": "Ắc quy trước"},
            {"value": 1, "label_en": "PV first", "label_vi": "Năng lượng mặt trời trước"},
            {"value": 2, "label_en": "AC first", "label_vi": "Lưới điện trước"},
        ],
    },
    {
        "key": "line_mode",
        "category": CATEGORY_APPLICATION,
        "name_en": "Line mode",
        "name_vi": "Chế độ dòng",
        "reg": 146,
        "kind": KIND_SELECT,
        "options": [
            {"value": 0, "label_en": "APL", "label_vi": "APL"},
            {"value": 1, "label_en": "UPS", "label_vi": "UPS"},
            {"value": 2, "label_en": "GEN", "label_vi": "GEN"},
        ],
    },

    # ---- Charge setting ----
    {
        "key": "ac_charge_enable",
        "category": CATEGORY_CHARGE,
        "name_en": "AC charging",
        "name_vi": "Nạp từ lưới AC",
        "reg": 21,
        "kind": KIND_TOGGLE,
        "bit": 7,
    },
    {
        "key": "charge_first_enable",
        "category": CATEGORY_CHARGE,
        "name_en": "Charge first",
        "name_vi": "Ưu tiên sạc",
        "reg": 21,
        "kind": KIND_TOGGLE,
        "bit": 11,
    },
    {
        "key": "ac_charge_type",
        "category": CATEGORY_CHARGE,
        "name_en": "AC charge type",
        "name_vi": "Kiểu sạc AC",
        "reg": 120,
        "kind": KIND_SELECT,
        "bit0": 1,
        "bitwidth": 3,
        "options": [
            {"value": 0, "label_en": "Off", "label_vi": "Tắt"},
            {"value": 1, "label_en": "Time", "label_vi": "Theo giờ"},
            {"value": 2, "label_en": "Voltage", "label_vi": "Theo điện áp"},
            {"value": 3, "label_en": "SOC", "label_vi": "Theo SOC"},
            {"value": 4, "label_en": "Voltage + Time", "label_vi": "Điện áp + giờ"},
            {"value": 5, "label_en": "SOC + Time", "label_vi": "SOC + giờ"},
        ],
    },
    {
        "key": "ac_charge_power",
        "category": CATEGORY_CHARGE,
        "name_en": "AC charging power",
        "name_vi": "Công suất sạc AC",
        "reg": 66,
        "kind": KIND_NUMBER,
        "unit": "%",
        "min": 0,
        "max": 100,
        "high_byte": True,
    },
    {
        "key": "ac_charge_soc_limit",
        "category": CATEGORY_CHARGE,
        "name_en": "AC charging SOC limit",
        "name_vi": "Giới hạn SOC sạc AC",
        "reg": 67,
        "kind": KIND_NUMBER,
        "unit": "%",
        "min": 0,
        "max": 100,
    },
    {
        "key": "ac_charge_time_1_start",
        "category": CATEGORY_CHARGE,
        "name_en": "AC charging period 1 start",
        "name_vi": "Nạp AC chu kỳ 1 - bắt đầu",
        "reg": 68,
        "kind": KIND_TIME,
    },
    {
        "key": "ac_charge_time_1_end",
        "category": CATEGORY_CHARGE,
        "name_en": "AC charging period 1 end",
        "name_vi": "Nạp AC chu kỳ 1 - kết thúc",
        "reg": 69,
        "kind": KIND_TIME,
    },
    {
        "key": "ac_charge_time_2_start",
        "category": CATEGORY_CHARGE,
        "name_en": "AC charging period 2 start",
        "name_vi": "Nạp AC chu kỳ 2 - bắt đầu",
        "reg": 70,
        "kind": KIND_TIME,
    },
    {
        "key": "ac_charge_time_2_end",
        "category": CATEGORY_CHARGE,
        "name_en": "AC charging period 2 end",
        "name_vi": "Nạp AC chu kỳ 2 - kết thúc",
        "reg": 71,
        "kind": KIND_TIME,
    },
    {
        "key": "ac_charge_time_3_start",
        "category": CATEGORY_CHARGE,
        "name_en": "AC charging period 3 start",
        "name_vi": "Nạp AC chu kỳ 3 - bắt đầu",
        "reg": 72,
        "kind": KIND_TIME,
    },
    {
        "key": "ac_charge_time_3_end",
        "category": CATEGORY_CHARGE,
        "name_en": "AC charging period 3 end",
        "name_vi": "Nạp AC chu kỳ 3 - kết thúc",
        "reg": 73,
        "kind": KIND_TIME,
    },
    {
        "key": "charge_first_power",
        "category": CATEGORY_CHARGE,
        "name_en": "Charge first power",
        "name_vi": "Công suất ưu tiên sạc",
        "reg": 74,
        "kind": KIND_NUMBER,
        "unit": "%",
        "min": 0,
        "max": 100,
    },
    {
        "key": "charge_first_soc_limit",
        "category": CATEGORY_CHARGE,
        "name_en": "Charge first SOC limit",
        "name_vi": "Giới hạn SOC ưu tiên sạc",
        "reg": 75,
        "kind": KIND_NUMBER,
        "unit": "%",
        "min": 0,
        "max": 101,
        "high_byte": True,
    },
    {
        "key": "charge_first_time_1_start",
        "category": CATEGORY_CHARGE,
        "name_en": "Charge first period 1 start",
        "name_vi": "Ưu tiên sạc chu kỳ 1 - bắt đầu",
        "reg": 76,
        "kind": KIND_TIME,
    },
    {
        "key": "charge_first_time_1_end",
        "category": CATEGORY_CHARGE,
        "name_en": "Charge first period 1 end",
        "name_vi": "Ưu tiên sạc chu kỳ 1 - kết thúc",
        "reg": 77,
        "kind": KIND_TIME,
    },
    {
        "key": "charge_first_time_2_start",
        "category": CATEGORY_CHARGE,
        "name_en": "Charge first period 2 start",
        "name_vi": "Ưu tiên sạc chu kỳ 2 - bắt đầu",
        "reg": 78,
        "kind": KIND_TIME,
    },
    {
        "key": "charge_first_time_2_end",
        "category": CATEGORY_CHARGE,
        "name_en": "Charge first period 2 end",
        "name_vi": "Ưu tiên sạc chu kỳ 2 - kết thúc",
        "reg": 79,
        "kind": KIND_TIME,
    },
    {
        "key": "charge_first_time_3_start",
        "category": CATEGORY_CHARGE,
        "name_en": "Charge first period 3 start",
        "name_vi": "Ưu tiên sạc chu kỳ 3 - bắt đầu",
        "reg": 80,
        "kind": KIND_TIME,
    },
    {
        "key": "charge_first_time_3_end",
        "category": CATEGORY_CHARGE,
        "name_en": "Charge first period 3 end",
        "name_vi": "Ưu tiên sạc chu kỳ 3 - kết thúc",
        "reg": 81,
        "kind": KIND_TIME,
    },
    {
        "key": "charge_current",
        "category": CATEGORY_CHARGE,
        "name_en": "Charge current",
        "name_vi": "Dòng sạc",
        "reg": 101,
        "kind": KIND_NUMBER,
        "unit": "A",
        "min": 0,
        "max": 140,
    },

    # ---- Discharge setting ----
    {
        "key": "discharge_control_type",
        "category": CATEGORY_DISCHARGE,
        "name_en": "Discharge control type",
        "name_vi": "Kiểu điều khiển xả",
        "reg": 120,
        "kind": KIND_SELECT,
        "bit0": 4,
        "bitwidth": 2,
        "options": [
            {"value": 0, "label_en": "Voltage", "label_vi": "Điện áp"},
            {"value": 1, "label_en": "SOC", "label_vi": "SOC"},
            {"value": 2, "label_en": "Voltage + SOC", "label_vi": "Điện áp + SOC"},
        ],
    },
    {
        "key": "forced_discharge_enable",
        "category": CATEGORY_DISCHARGE,
        "name_en": "Forced discharge",
        "name_vi": "Xả cưỡng bức",
        "reg": 21,
        "kind": KIND_TOGGLE,
        "bit": 10,
    },
    {
        "key": "forced_discharge_power",
        "category": CATEGORY_DISCHARGE,
        "name_en": "Forced discharge power",
        "name_vi": "Công suất xả cưỡng bức",
        "reg": 82,
        "kind": KIND_NUMBER,
        "unit": "%",
        "min": 0,
        "max": 100,
    },
    {
        "key": "forced_discharge_soc_limit",
        "category": CATEGORY_DISCHARGE,
        "name_en": "Forced discharge SOC limit",
        "name_vi": "Giới hạn SOC xả cưỡng bức",
        "reg": 83,
        "kind": KIND_NUMBER,
        "unit": "%",
        "min": 0,
        "max": 100,
    },
    {
        "key": "forced_discharge_time_1_start",
        "category": CATEGORY_DISCHARGE,
        "name_en": "Forced discharge period 1 start",
        "name_vi": "Xả cưỡng bức chu kỳ 1 - bắt đầu",
        "reg": 84,
        "kind": KIND_TIME,
    },
    {
        "key": "forced_discharge_time_1_end",
        "category": CATEGORY_DISCHARGE,
        "name_en": "Forced discharge period 1 end",
        "name_vi": "Xả cưỡng bức chu kỳ 1 - kết thúc",
        "reg": 85,
        "kind": KIND_TIME,
    },
    {
        "key": "forced_discharge_time_2_start",
        "category": CATEGORY_DISCHARGE,
        "name_en": "Forced discharge period 2 start",
        "name_vi": "Xả cưỡng bức chu kỳ 2 - bắt đầu",
        "reg": 86,
        "kind": KIND_TIME,
    },
    {
        "key": "forced_discharge_time_2_end",
        "category": CATEGORY_DISCHARGE,
        "name_en": "Forced discharge period 2 end",
        "name_vi": "Xả cưỡng bức chu kỳ 2 - kết thúc",
        "reg": 87,
        "kind": KIND_TIME,
    },
    {
        "key": "forced_discharge_time_3_start",
        "category": CATEGORY_DISCHARGE,
        "name_en": "Forced discharge period 3 start",
        "name_vi": "Xả cưỡng bức chu kỳ 3 - bắt đầu",
        "reg": 88,
        "kind": KIND_TIME,
    },
    {
        "key": "forced_discharge_time_3_end",
        "category": CATEGORY_DISCHARGE,
        "name_en": "Forced discharge period 3 end",
        "name_vi": "Xả cưỡng bức chu kỳ 3 - kết thúc",
        "reg": 89,
        "kind": KIND_TIME,
    },
    {
        "key": "warning_voltage",
        "category": CATEGORY_DISCHARGE,
        "name_en": "Battery low voltage alarm",
        "name_vi": "Cảnh báo điện áp ắc quy thấp",
        "reg": 162,
        "kind": KIND_NUMBER,
        "unit": "V",
        "min": 40,
        "max": 50,
        "scale": 0.1,
    },
    {
        "key": "warning_voltage_recovery",
        "category": CATEGORY_DISCHARGE,
        "name_en": "Battery low voltage alarm recovery",
        "name_vi": "Khôi phục cảnh báo điện áp thấp",
        "reg": 163,
        "kind": KIND_NUMBER,
        "unit": "V",
        "min": 42,
        "max": 52,
        "scale": 0.1,
    },
    {
        "key": "warning_soc",
        "category": CATEGORY_DISCHARGE,
        "name_en": "Battery low SOC alarm",
        "name_vi": "Cảnh báo SOC thấp",
        "reg": 164,
        "kind": KIND_NUMBER,
        "unit": "%",
        "min": 0,
        "max": 90,
    },
    {
        "key": "warning_soc_recovery",
        "category": CATEGORY_DISCHARGE,
        "name_en": "Battery low SOC alarm recovery",
        "name_vi": "Khôi phục cảnh báo SOC thấp",
        "reg": 165,
        "kind": KIND_NUMBER,
        "unit": "%",
        "min": 0,
        "max": 100,
    },
    {
        "key": "cutoff_soc_eps",
        "category": CATEGORY_DISCHARGE,
        "name_en": "Cut-off SOC (EPS / off-grid)",
        "name_vi": "Ngắt theo SOC (EPS / mất điện lưới)",
        "reg": 125,
        "kind": KIND_NUMBER,
        "unit": "%",
        "min": 0,
        "max": 100,
        "danger": True,
    },
    {
        "key": "cutoff_voltage_eps",
        "category": CATEGORY_DISCHARGE,
        "name_en": "Cut-off voltage (off-grid, lead-acid)",
        "name_vi": "Điện áp ngắt (mất lưới, ắc quy axit)",
        "reg": 100,
        "kind": KIND_NUMBER,
        "unit": "V",
        "min": 40,
        "max": 52,
        "scale": 0.1,
        "danger": True,
    },
    {
        "key": "cutoff_soc_grid",
        "category": CATEGORY_DISCHARGE,
        "name_en": "Cut-off SOC (grid-on)",
        "name_vi": "Ngắt theo SOC (có lưới)",
        "reg": 105,
        "kind": KIND_NUMBER,
        "unit": "%",
        "min": 10,
        "max": 100,
        "danger": True,
    },
    {
        "key": "cutoff_voltage_grid",
        "category": CATEGORY_DISCHARGE,
        "name_en": "Cut-off voltage (grid-on)",
        "name_vi": "Điện áp ngắt (có lưới)",
        "reg": 169,
        "kind": KIND_NUMBER,
        "unit": "V",
        "min": 40,
        "max": 56,
        "scale": 0.1,
        "danger": True,
    },
    {
        "key": "discharge_current",
        "category": CATEGORY_DISCHARGE,
        "name_en": "Discharge current",
        "name_vi": "Dòng xả",
        "reg": 102,
        "kind": KIND_NUMBER,
        "unit": "A",
        "min": 0,
        "max": 140,
    },

    # ---- Battery ----
    {
        "key": "battery_capacity",
        "category": CATEGORY_BATTERY,
        "name_en": "Battery capacity (unmatched battery)",
        "name_vi": "Dung lượng ắc quy (không có BMS)",
        "reg": 147,
        "kind": KIND_NUMBER,
        "unit": "Ah",
        "min": 0,
        "max": 10000,
        "danger": True,
    },
    {
        "key": "lead_acid_capacity",
        "category": CATEGORY_BATTERY,
        "name_en": "Lead-acid battery capacity",
        "name_vi": "Dung lượng ắc quy axit-chì",
        "reg": 204,
        "kind": KIND_NUMBER,
        "unit": "Ah",
        "min": 50,
        "max": 5000,
    },
    {
        "key": "nominal_voltage",
        "category": CATEGORY_BATTERY,
        "name_en": "Nominal battery voltage",
        "name_vi": "Điện áp danh định ắc quy",
        "reg": 148,
        "kind": KIND_NUMBER,
        "unit": "V",
        "min": 40,
        "max": 59,
        "scale": 0.1,
    },
    {
        "key": "equalization_voltage",
        "category": CATEGORY_BATTERY,
        "name_en": "Battery equalization voltage",
        "name_vi": "Điện áp cân bằng ắc quy",
        "reg": 149,
        "kind": KIND_NUMBER,
        "unit": "V",
        "min": 50,
        "max": 59,
        "scale": 0.1,
    },
    {
        "key": "equalization_interval",
        "category": CATEGORY_BATTERY,
        "name_en": "Battery equalization interval",
        "name_vi": "Chu kỳ cân bằng ắc quy",
        "reg": 150,
        "kind": KIND_NUMBER,
        "unit": "days",
        "min": 0,
        "max": 365,
    },
    {
        "key": "equalization_time",
        "category": CATEGORY_BATTERY,
        "name_en": "Battery equalization time",
        "name_vi": "Thời gian cân bằng ắc quy",
        "reg": 151,
        "kind": KIND_NUMBER,
        "unit": "h",
        "min": 0,
        "max": 24,
    },
]

_ITEM_INDEX = {item["key"]: item for item in _ITEMS}
_CATEGORY_INDEX = {item["key"]: item for item in CATEGORIES}


def categories() -> list:
    return list(CATEGORIES)


def get_item(key: str):
    return _ITEM_INDEX.get(key)


def get_items(category: str) -> list:
    return [item for item in _ITEMS if item["category"] == category]


def all_items() -> list:
    return list(_ITEMS)


def public_item(item: dict, language: str = "en") -> dict:
    """Return a serializable representation of an item for the REST API."""
    lang = "vi" if language and language.lower().startswith("vi") else "en"
    result = {
        "key": item["key"],
        "name": item.get("name_en") if lang == "en" else item.get("name_vi"),
        "reg": item["reg"],
        "kind": item["kind"],
        "danger": bool(item.get("danger")),
        "verify": bool(item.get("verify")),
    }
    if item.get("bit") is not None:
        result["bit"] = item["bit"]
    if item.get("bit0") is not None:
        result["bit0"] = item["bit0"]
    if item.get("bitwidth") is not None:
        result["bitwidth"] = item["bitwidth"]
    if item.get("unit"):
        result["unit"] = item["unit"]
    if item.get("min") is not None:
        result["min"] = item["min"]
    if item.get("max") is not None:
        result["max"] = item["max"]
    if item.get("scale") is not None:
        result["scale"] = item["scale"]
    if item.get("options"):
        result["options"] = [
            {"value": opt["value"], "label": opt["label_en"] if lang == "en" else opt["label_vi"]}
            for opt in item["options"]
        ]
    return result


def mask_for(item: dict) -> tuple:
    """Return (shift, mask) used to extract/merge the item's bits in its register."""
    if item.get("bit") is not None:
        return item["bit"], (0xFFFF & (1 << item["bit"]))
    if item.get("bit0") is not None:
        width = item.get("bitwidth") or 1
        shift = item["bit0"]
        mask = ((1 << width) - 1) << shift
        return shift, mask
    return 0, 0xFFFF


def extract_value(item: dict, raw: int) -> object:
    """Convert a raw register value into the item's display value."""
    kind = item["kind"]
    if kind == KIND_NUMBER and item.get("high_byte"):
        raw = (raw >> 8) & 0xFF
    else:
        shift, mask = mask_for(item)
        raw = (raw & mask) >> shift
    if kind == KIND_TIME:
        minute = (raw >> 8) & 0xFF
        hour = raw & 0xFF
        return "%02d:%02d" % (hour, minute)
    if kind == KIND_NUMBER and item.get("scale"):
        return round(raw * item["scale"], 2)
    return raw


def encode_value(item: dict, value) -> int:
    """Validate and convert a display value into the value to write.

    Returns an integer which is either a full register write value (when the
    item is a whole register) or the raw value for a masked bit range.
    For masked items callers should merge via ``merge_raw`` for read-modify-write.
    """
    kind = item["kind"]
    if kind == KIND_TOGGLE:
        return 1 if int(value) else 0
    if kind == KIND_SELECT:
        value = int(value)
        allowed = {opt["value"] for opt in item.get("options") or []}
        if allowed and value not in allowed:
            raise ValueError("Option %s not allowed for %s" % (value, item["key"]))
        return value
    if kind == KIND_NUMBER:
        value = float(value)
        if item.get("min") is not None and value < item["min"]:
            raise ValueError("Value %s below minimum %s" % (value, item["min"]))
        if item.get("max") is not None and value > item["max"]:
            raise ValueError("Value %s above maximum %s" % (value, item["max"]))
        if item.get("scale"):
            raw = int(round(value / item["scale"]))
        else:
            raw = int(value)
        if item.get("high_byte"):
            raw = (raw & 0xFF) << 8
        return raw
    if kind == KIND_TIME:
        if isinstance(value, str):
            parts = value.strip().split(":")
            if len(parts) != 2:
                raise ValueError("Invalid time %r (expected HH:MM)" % value)
            try:
                hour, minute = int(parts[0]), int(parts[1])
            except ValueError:
                raise ValueError("Invalid time %r (expected HH:MM)" % value)
        else:
            total = int(value)
            hour, minute = divmod(total, 60)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("Invalid time %r (hours 0-23, minutes 0-59)" % value)
        return (minute << 8) | hour
    raise ValueError("Unknown kind %s" % kind)


def merge_raw(item: dict, current_raw: int, raw_part: int) -> int:
    """Merge the written (possibly masked) value into the current register raw value."""
    shift, mask = mask_for(item)
    if mask == 0xFFFF:
        return raw_part
    return (current_raw & ~mask) | ((raw_part << shift) & mask)