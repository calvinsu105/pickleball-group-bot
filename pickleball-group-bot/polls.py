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


MAX_GUESTS = 5  # 每人最多攜伴人數


def party_size(vote):
    """本人 + 攜伴人數。"""
    return 1 + (vote.get("guests") or 0)


def split_roster(votes, capacity):
    """votes: 依投票先後排序的 list（每筆含 choice、guests）。
    回傳 (正取, 候補, 不參加)。

    攜伴的人與同伴算同一組，整組一起正取或一起候補，不會拆開。
    嚴格依報名順序：前面的組放不下時，後面的人也一律候補（不插隊），
    所以有人取消或減少攜伴時，只會讓候補依序遞補，已正取的人不會被擠掉。"""
    yes = [v for v in votes if v["choice"] == "yes"]
    not_going = [v for v in votes if v["choice"] == "no"]
    if not capacity:
        return yes, [], not_going
    going, used = [], 0
    for i, v in enumerate(yes):
        if used + party_size(v) > capacity:
            return going, yes[i:], not_going
        going.append(v)
        used += party_size(v)
    return going, [], not_going


def headcount(votes):
    """一群投票的總人數（含攜伴）。"""
    return sum(party_size(v) for v in votes)


def next_guest_state(current, delta):
    """按「➕／➖ 攜伴」後的新狀態。
    current: 目前的投票（沒投過為 None）。
    回傳 (choice, guests, keep_order, 訊息)；不需要變更時 choice 為 None。"""
    if delta > 0:
        if current is None or current["choice"] != "yes":
            return "yes", 1, False, "報名並攜伴 1 人"
        guests = current.get("guests") or 0
        if guests >= MAX_GUESTS:
            return None, guests, True, f"每人最多攜伴 {MAX_GUESTS} 人"
        # 增加攜伴視為新的報名，整組重新排到最後，不會擠掉已經正取的人
        return "yes", guests + 1, False, f"攜伴 {guests + 1} 人"
    if current is None or current["choice"] != "yes" or not current.get("guests"):
        return None, 0, True, "目前沒有攜伴"
    guests = current["guests"] - 1
    return "yes", guests, True, f"攜伴 {guests} 人" if guests else "取消攜伴"
