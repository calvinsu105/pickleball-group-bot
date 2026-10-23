"""揪團訊息解析與名單計算（不依賴 LINE SDK，可單獨測試）。"""
import re

TRIGGER = "揪團"
LIST_COMMAND = "場次"

# 每個欄位可接受的標籤寫法
FIELD_LABELS = {
    "date": ["日期"],
    "time": ["時間"],
    "venue": ["場地", "地點", "球場"],
    "price": ["價錢", "價格", "費用", "場地費", "金額"],
    "capacity": ["人數上限", "人數", "名額", "上限"],
}
REQUIRED = ["date", "time", "venue", "price"]
FIELD_NAMES = {"date": "日期", "time": "時間", "venue": "場地", "price": "價錢", "capacity": "人數"}

TEMPLATE = (
    "揪團\n"
    "日期：10/5（日）\n"
    "時間：19:00-21:00\n"
    "場地：信義運動中心 3F\n"
    "價錢：150 元/人\n"
    "人數：8（可不填）"
)

_LABEL_TO_FIELD = {lab: f for f, labs in FIELD_LABELS.items() for lab in labs}
# 長的標籤優先比對，避免「場地費」被當成「場地」
_LABELS = "|".join(sorted(_LABEL_TO_FIELD, key=len, reverse=True))
_FIELD_RE = re.compile(
    rf"(?P<label>{_LABELS})\s*(?:[：:]\s*|\s+)(?P<value>.*?)"
    rf"(?=\s*(?:{_LABELS})\s*(?:[：:]|\s)|\n|$)"
)


def is_trigger(text):
    return text.strip().startswith(TRIGGER)


def parse_poll(text):
    """解析揪團訊息，回傳 (欄位 dict, 缺少的必填欄位 list)。"""
    body = text.strip()[len(TRIGGER):]
    fields = {}
    for m in _FIELD_RE.finditer(body):
        field = _LABEL_TO_FIELD[m.group("label")]
        value = m.group("value").strip(" ，,、")
        if value and field not in fields:
            fields[field] = value

    if "capacity" in fields:
        num = re.search(r"\d+", fields["capacity"])
        fields["capacity"] = int(num.group()) if num and int(num.group()) > 0 else None
    else:
        fields["capacity"] = None

    missing = [FIELD_NAMES[f] for f in REQUIRED if not fields.get(f)]
    return fields, missing


def split_roster(votes, capacity):
    """votes: 依投票先後排序的 list（每筆含 choice）。
    回傳 (正取, 候補, 不參加)。有人取消時，候補會自動依序遞補。"""
    going = [v for v in votes if v["choice"] == "yes"]
    not_going = [v for v in votes if v["choice"] == "no"]
    if capacity:
        return going[:capacity], going[capacity:], not_going
    return going, [], not_going
