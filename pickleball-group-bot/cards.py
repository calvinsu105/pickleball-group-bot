"""產生 LINE Flex Message 卡片（純 dict，方便測試）。"""
from polls import headcount, parse_amount, split_cost, split_roster

GREEN = "#1B7F5B"
GREY = "#888888"


def _row(label, value):
    return {
        "type": "box", "layout": "baseline", "spacing": "md",
        "contents": [
            {"type": "text", "text": label, "color": GREY, "size": "sm", "flex": 2},
            {"type": "text", "text": str(value), "size": "sm", "flex": 6, "wrap": True},
        ],
    }


def _section(title, names, empty_text, color="#111111"):
    body = "\n".join(f"{i}. {n}" for i, n in enumerate(names, 1)) if names else empty_text
    return [
        {"type": "text", "text": title, "weight": "bold", "size": "sm", "color": color, "margin": "lg"},
        {"type": "text", "text": body, "size": "sm", "wrap": True, "margin": "sm",
         "color": "#333333" if names else GREY},
    ]


def _button(label, data, display, style, color=None):
    btn = {
        "type": "button", "style": style, "height": "sm",
        "action": {"type": "postback", "label": label, "data": data, "displayText": display},
    }
    if color or style == "primary":
        btn["color"] = color or GREEN
    return btn


def _name(v, with_guests=True):
    n = v["display_name"] or "成員"
    g = v.get("guests") or 0
    return f"{n} +{g}" if with_guests and g else n


def _guest_row(pid, date):
    return {"type": "box", "layout": "horizontal", "spacing": "sm", "contents": [
        _button("➕ 攜伴", f"action=guest&poll={pid}&delta=1", f"➕ 多帶 1 人（{date}）", "secondary"),
        _button("➖ 攜伴", f"action=guest&poll={pid}&delta=-1", f"➖ 少帶 1 人（{date}）", "secondary"),
    ]}


def poll_card(poll, votes, note=None):
    """揪團卡片：活動資訊 + 目前名單 + 投票按鈕。建立時與每次投票後都用同一張卡。"""
    cap = poll["capacity"]
    going, waitlist, not_going = split_roster(votes, cap)

    count = f"{headcount(going)}/{cap}" if cap else f"{headcount(going)}"
    info = [
        _row("日期", poll["date"]),
        _row("時間", poll["time"]),
        _row("場地", poll["venue"]),
        _row("場地費", poll["price"]),
    ]
    if cap:
        info.append(_row("人數", f"上限 {cap} 人"))
    if poll.get("creator_name"):
        info.append(_row("團主", poll["creator_name"]))

    roster = _section(f"✋ 參加（{count}）", [_name(v) for v in going], "還沒有人報名，快來搶頭香！", GREEN)
    if cap:
        roster += _section(f"⏳ 候補（{headcount(waitlist)}）", [_name(v) for v in waitlist], "無")
    roster += _section(f"🙅 不參加（{len(not_going)}）", [_name(v, False) for v in not_going], "無", GREY)

    pid = poll["id"]
    footer = [
        {"type": "box", "layout": "horizontal", "spacing": "sm", "contents": [
            _button("✋ 參加", f"action=vote&poll={pid}&choice=yes", "✋ 我要參加", "primary"),
            _button("🙅 不參加", f"action=vote&poll={pid}&choice=no", "🙅 我不參加", "secondary"),
        ]},
        _guest_row(pid, poll["date"]),
        _button("📍 到場簽到", f"action=checkin_start&poll={pid}", f"📍 簽到（{poll['date']}）", "link"),
    ]

    header_texts = [{"type": "text", "text": "🏓 匹克球揪團", "color": "#FFFFFF", "weight": "bold", "size": "lg"}]
    if note:
        header_texts.append({"type": "text", "text": note, "color": "#DFF5EA", "size": "xs", "wrap": True})

    bubble = {
        "type": "bubble",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": GREEN,
                   "paddingAll": "16px", "contents": header_texts},
        "body": {"type": "box", "layout": "vertical", "spacing": "sm",
                 "contents": info + [{"type": "separator", "margin": "lg"}] + roster},
        "footer": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": footer},
    }
    alt = f"🏓 匹克球揪團 {poll['date']} {poll['time']} @{poll['venue']}（參加 {count}）"
    return bubble, alt[:400]


def _short(poll):
    return f"{poll['date']} {poll['time']} {poll['venue']}"


def list_carousel(polls):
    """「場次」指令：每個揪團一張小卡，可左右滑動（最多 12 張）。
    polls 需含 votes，由 db.list_polls 提供。人數都包含攜伴。"""
    bubbles = []
    for p in polls[:12]:
        cap = p["capacity"]
        going, waitlist, _ = split_roster(p["votes"], cap)
        if cap:
            wait = headcount(waitlist)
            status = f"參加 {headcount(going)}/{cap}" + (f"，候補 {wait}" if wait else "")
        else:
            status = f"參加 {headcount(going)} 人"
        rows = [
            _row("時間", p["time"]),
            _row("場地", p["venue"]),
            _row("場地費", p["price"]),
            _row("報名", status),
        ]
        if p.get("creator_name"):
            rows.append(_row("團主", p["creator_name"]))
        pid = p["id"]
        bubbles.append({
            "type": "bubble", "size": "kilo",
            "header": {"type": "box", "layout": "vertical", "backgroundColor": GREEN, "paddingAll": "12px",
                       "contents": [{"type": "text", "text": f"🏓 {p['date']}", "color": "#FFFFFF",
                                     "weight": "bold", "wrap": True}]},
            "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": rows},
            "footer": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [
                {"type": "box", "layout": "horizontal", "spacing": "sm", "contents": [
                    _button("✋ 參加", f"action=vote&poll={pid}&choice=yes", f"✋ 我要參加 {p['date']}", "primary"),
                    _button("🙅 不參加", f"action=vote&poll={pid}&choice=no", f"🙅 我不參加 {p['date']}", "secondary"),
                ]},
                _guest_row(pid, p["date"]),
                {"type": "box", "layout": "horizontal", "spacing": "sm", "contents": [
                    _button("📋 名單", f"action=show&poll={pid}", f"📋 查看 {p['date']} 名單", "link"),
                    _button("📍 簽到", f"action=checkin_start&poll={pid}", f"📍 簽到（{p['date']}）", "link"),
                    _button("🗑 刪除", f"action=ask_delete&poll={pid}", f"🗑 刪除 {p['date']} 場次", "link",
                            color="#C0392B"),
                ]},
            ]},
        })
    carousel = {"type": "carousel", "contents": bubbles}
    return carousel, f"📅 揪團場次（共 {len(polls)} 場）"


def delete_confirm_card(poll, yes_count):
    """刪除前的確認卡片。"""
    warn = f"已有 {yes_count} 人報名，" if yes_count else ""
    bubble = {
        "type": "bubble", "size": "kilo",
        "body": {"type": "box", "layout": "vertical", "spacing": "md", "contents": [
            {"type": "text", "text": "確定要刪除這場揪團？", "weight": "bold", "size": "md"},
            {"type": "text", "text": _short(poll), "size": "sm", "wrap": True},
            {"type": "text", "text": f"{warn}刪除後報名紀錄會一起清除，無法復原。",
             "size": "xs", "color": GREY, "wrap": True},
        ]},
        "footer": {"type": "box", "layout": "horizontal", "spacing": "sm", "contents": [
            _button("確定刪除", f"action=delete&poll={poll['id']}", "確定刪除", "primary", color="#C0392B"),
            _button("取消", f"action=cancel&poll={poll['id']}", "取消", "secondary"),
        ]},
    }
    return bubble, f"確定要刪除 {_short(poll)} 的揪團？"[:400]


def guest_choice_card(poll, registered_guests, current=None):
    """有攜伴的人簽到時，選擇實際到了幾位同伴。"""
    pid = poll["id"]
    buttons = [
        _button("只有本人" if g == 0 else f"本人＋{g} 位同伴", f"action=checkin&poll={pid}&g={g}",
                "📍 簽到（本人）" if g == 0 else f"📍 簽到（本人＋{g}）",
                "primary" if g == registered_guests else "secondary")
        for g in range(registered_guests + 1)
    ]
    sub = f"報名時攜伴 {registered_guests} 人，實際到了幾位？"
    if current is not None:
        sub += f"\n（目前簽到：本人＋{current} 位）" if current else "\n（目前簽到：只有本人）"
    bubble = {
        "type": "bubble", "size": "kilo",
        "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [
            {"type": "text", "text": "📍 到場簽到", "weight": "bold", "size": "md"},
            {"type": "text", "text": _short(poll), "size": "sm", "wrap": True},
            {"type": "text", "text": sub, "size": "xs", "color": GREY, "wrap": True},
        ]},
        "footer": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": buttons},
    }
    return bubble, f"📍 簽到：{_short(poll)}"[:400]


def checkin_card(poll, votes, checkins, note=None):
    """簽到表：實到名單、還沒簽到的人、實到人數與每人應付金額。"""
    pid = poll["id"]
    present = headcount(checkins)
    checked_ids = {c["user_id"] for c in checkins}
    going, _, _ = split_roster(votes, poll["capacity"])
    missing = [_name(v) for v in going if v["user_id"] not in checked_ids]

    total = parse_amount(poll["price"])
    cost = split_cost(total, present)
    if total is None:
        money = [_row("場地費", f"{poll['price']}（無法辨識金額）")]
    elif cost is None:
        money = [_row("場地費", f"總額 {total:,} 元"), _row("每人", "等大家簽到後計算")]
    else:
        per, collected, extra = cost
        detail = f"合計 {collected:,} 元" + (f"，多收 {extra} 元" if extra else "")
        money = [_row("場地費", f"總額 {total:,} 元"),
                 _row("每人", f"{per:,} 元（{present} 人平分）"),
                 {"type": "text", "text": detail, "size": "xs", "color": GREY, "align": "end"}]

    body = [
        {"type": "text", "text": f"✅ 實到 {present} 人", "weight": "bold", "size": "xl", "color": GREEN},
        _row("時間", f"{poll['date']} {poll['time']}"),
        _row("場地", poll["venue"]),
        {"type": "separator", "margin": "lg"},
        *money,
        {"type": "separator", "margin": "lg"},
        *_section(f"📍 已簽到（{len(checkins)} 組）", [_name(c) for c in checkins], "還沒有人簽到", GREEN),
        *_section(f"⌛ 報名但未簽到（{len(missing)}）", missing, "無", GREY),
    ]
    header = [{"type": "text", "text": "📍 簽到表", "color": "#FFFFFF", "weight": "bold", "size": "lg"}]
    if note:
        header.append({"type": "text", "text": note, "color": "#DFF5EA", "size": "xs", "wrap": True})
    bubble = {
        "type": "bubble",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": GREEN,
                   "paddingAll": "16px", "contents": header},
        "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": body},
        "footer": {"type": "box", "layout": "horizontal", "spacing": "sm", "contents": [
            _button("📍 簽到", f"action=checkin_start&poll={pid}", f"📍 簽到（{poll['date']}）", "primary"),
            _button("↩️ 取消簽到", f"action=checkin_cancel&poll={pid}", "↩️ 取消簽到", "secondary"),
        ]},
    }
    alt = f"📍 簽到表 {poll['date']}：實到 {present} 人"
    if cost:
        alt += f"，每人 {cost[0]} 元"
    return bubble, alt[:400]
