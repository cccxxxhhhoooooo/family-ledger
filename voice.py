"""把一句中文语音转成账单字段。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import streamlit as st  # pyright: ignore[reportMissingImports]

# 口语里的类别词。越长越先匹配，避免「车」吃掉「打车」
_CATEGORY_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("早餐", "餐饮"),
    ("午饭", "餐饮"),
    ("午餐", "餐饮"),
    ("晚饭", "餐饮"),
    ("晚餐", "餐饮"),
    ("早饭", "餐饮"),
    ("外卖", "餐饮"),
    ("奶茶", "餐饮"),
    ("咖啡", "餐饮"),
    ("买菜", "餐饮"),
    ("水果", "餐饮"),
    ("吃饭", "餐饮"),
    ("餐饮", "餐饮"),
    ("地铁", "交通"),
    ("公交", "交通"),
    ("打车", "交通"),
    ("出租", "交通"),
    ("加油", "交通"),
    ("停车", "交通"),
    ("高铁", "交通"),
    ("火车", "交通"),
    ("交通", "交通"),
    ("买衣服", "购物"),
    ("衣服", "购物"),
    ("淘宝", "购物"),
    ("京东", "购物"),
    ("日用品", "购物"),
    ("购物", "购物"),
    ("房租", "住房"),
    ("水电", "住房"),
    ("物业", "住房"),
    ("燃气", "住房"),
    ("住房", "住房"),
    ("电影", "娱乐"),
    ("游戏", "娱乐"),
    ("旅游", "娱乐"),
    ("娱乐", "娱乐"),
    ("看病", "医疗"),
    ("医院", "医疗"),
    ("买药", "医疗"),
    ("医疗", "医疗"),
    ("学费", "教育"),
    ("培训", "教育"),
    ("教育", "教育"),
    ("其他", "其他"),
)

_DIGITS: dict[str, int] = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "壹": 1,
    "二": 2,
    "两": 2,
    "贰": 2,
    "三": 3,
    "叁": 3,
    "四": 4,
    "肆": 4,
    "五": 5,
    "伍": 5,
    "六": 6,
    "陆": 6,
    "七": 7,
    "柒": 7,
    "八": 8,
    "捌": 8,
    "九": 9,
    "玖": 9,
}
_UNITS: dict[str, int] = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
_CN_NUM = "".join(_DIGITS) + "".join(_UNITS)
_SPOKEN_NUMBER = rf"(?:\d+(?:\.\d+)?|[{re.escape(_CN_NUM)}]+(?:点[{re.escape(''.join(_DIGITS))}]+)?)"
_AMOUNT_WITH_UNIT = re.compile(rf"(?P<num>{_SPOKEN_NUMBER})\s*(?:块钱|元钱|块|元|圆)")
_AMOUNT_AFTER_VERB = re.compile(rf"(?:花了|付了|支付|花)\s*(?P<num>{_SPOKEN_NUMBER})")
_FILLERS: tuple[str, ...] = ("帮我记一下", "帮我记一笔", "帮我记", "记一笔", "花了", "付了", "支付")

_speech_component: Any | None = None


@dataclass(frozen=True)
class SpokenBill:
    """从一句话里解析出的账单。金额听不清时 amount 为 None。"""

    bill_date: date
    category: str
    amount: float | None
    note: str
    owner: str | None
    raw_text: str


def parse_bill_speech(
    text: str,
    *,
    categories: list[str],
    owners: list[str],
    today: date | None = None,
) -> SpokenBill:
    """从口语里提取日期、使用者、类别、金额和备注。"""
    current_day = today or date.today()
    raw_text = text.strip()
    # 去掉标点，避免「三十五块，午饭」把备注切碎
    working = re.sub(r"[\s,，。！？、；;：:]+", "", raw_text)

    owner, working = _extract_owner(working, owners)
    bill_date, working = _extract_date(working, current_day)
    category, working, note_seed = _extract_category(working, categories)
    amount, working = _extract_amount(working)
    note = _clean_note(working)
    # 「午饭」这类词既用来判断类别，也保留成备注
    if note_seed and note_seed not in note:
        note = note_seed if not note else f"{note_seed}{note}"
    return SpokenBill(
        bill_date=bill_date,
        category=category,
        amount=amount,
        note=note,
        owner=owner,
        raw_text=raw_text,
    )


def listen_for_speech() -> dict[str, str] | None:
    """显示手机上的语音按钮。本轮如果有新识别结果，返回编号和原文。"""
    result = _get_speech_component()(on_transcript_change=_ignore_voice_change)
    raw = getattr(result, "transcript", None)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    spoken = str(payload.get("text", "")).strip()
    identity = str(payload.get("id", "")).strip()
    if not spoken or not identity:
        return None
    return {"id": identity, "text": spoken}


def _ignore_voice_change() -> None:
    """识别结果在触发值里读取，回调本身不做处理。"""
    return None


def _get_speech_component() -> Any:
    """注册一次语音按钮。脚本跑在页面里，才能调用手机麦克风。"""
    global _speech_component
    if _speech_component is None:
        _speech_component = st.components.v2.component(
            "family_ledger_speech",
            html="""
            <button id="speech-btn" type="button">点我说话</button>
            <p id="speech-status">例如：今天餐饮三十五块，午饭</p>
            """,
            css="""
            button {
                width: 100%;
                min-height: 52px;
                border: none;
                border-radius: 14px;
                background: #101828;
                color: #ffffff;
                font-size: 1.05rem;
                font-weight: 650;
            }
            button:disabled {
                background: #98a2b3;
            }
            p {
                margin: 0.45rem 0 0;
                color: #667085;
                font-size: 0.92rem;
                line-height: 1.4;
            }
            """,
            js="""
            export default function(component) {
                const { setTriggerValue, parentElement } = component;
                const button = parentElement.querySelector("#speech-btn");
                const status = parentElement.querySelector("#speech-status");
                const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
                if (!button || !status) {
                    return;
                }
                if (!SpeechRecognition) {
                    button.disabled = true;
                    status.textContent = "这个浏览器不能语音输入。安卓请用 Chrome 打开，并允许麦克风。";
                    return;
                }

                const recognition = new SpeechRecognition();
                recognition.lang = "zh-CN";
                recognition.interimResults = false;
                recognition.maxAlternatives = 1;
                let listening = false;

                button.onclick = () => {
                    if (listening) {
                        recognition.stop();
                        return;
                    }
                    listening = true;
                    button.textContent = "正在听，说完可再点一次";
                    status.textContent = "请说：谁、哪天、类别、多少钱。";
                    try {
                        recognition.start();
                    } catch (error) {
                        listening = false;
                        button.textContent = "点我说话";
                        status.textContent = "麦克风没启动，请再点一次。";
                    }
                };

                recognition.onresult = (event) => {
                    const text = (event.results[0][0].transcript || "").trim();
                    listening = false;
                    button.textContent = "点我说话";
                    status.textContent = text ? "听到：" + text : "没有听清，请再说一次。";
                    if (text) {
                        setTriggerValue("transcript", JSON.stringify({
                            id: String(Date.now()),
                            text: text
                        }));
                    }
                };

                recognition.onerror = (event) => {
                    listening = false;
                    button.textContent = "点我说话";
                    // aborted 多半是页面刷新打断，不算没听清
                    if (event.error === "aborted") {
                        return;
                    }
                    const hints = {
                        "not-allowed": "请允许麦克风后再点一次。",
                        "service-not-allowed": "这个浏览器不能用网页语音，还没开始听。请用下面的输入框，点键盘上的麦克风。",
                        "network": "语音服务连不上，还没开始听。请用下面的输入框，点键盘上的麦克风。",
                        "audio-capture": "找不到麦克风。请用下面的输入框，点键盘上的麦克风。",
                        "no-speech": "没有听到声音，请再说一次。"
                    };
                    status.textContent = hints[event.error]
                        || "网页语音没启动，还没开始听。请用下面的输入框，点键盘上的麦克风。";
                };

                recognition.onend = () => {
                    listening = false;
                    button.textContent = "点我说话";
                };

                return () => {
                    recognition.onresult = null;
                    recognition.onerror = null;
                    recognition.onend = null;
                    try {
                        recognition.abort();
                    } catch (error) {
                        // 已经结束时再中断会抛错，可以忽略
                    }
                };
            }
            """,
        )
    return _speech_component


def _extract_owner(text: str, owners: list[str]) -> tuple[str | None, str]:
    """取出句子里的使用者。没说到时留给界面上的当前使用者。"""
    for owner in sorted(owners, key=len, reverse=True):
        if owner and owner in text:
            return owner, text.replace(owner, "", 1)
    return None, text


def _extract_date(text: str, today: date) -> tuple[date, str]:
    """识别今天、昨天、前天，以及「10月8日」「十月八号」。"""
    if "前天" in text:
        return today - timedelta(days=2), text.replace("前天", "", 1)
    if "昨天" in text:
        return today - timedelta(days=1), text.replace("昨天", "", 1)
    if "今天" in text:
        return today, text.replace("今天", "", 1)

    full_date = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})[日号]?", text)
    if full_date:
        parsed = _safe_date(int(full_date.group(1)), int(full_date.group(2)), int(full_date.group(3)))
        if parsed is not None:
            return parsed, text.replace(full_date.group(0), "", 1)

    month_day = re.search(r"(\d{1,2})月(\d{1,2})[日号]", text)
    if month_day:
        parsed = _safe_date(today.year, int(month_day.group(1)), int(month_day.group(2)))
        if parsed is not None:
            return parsed, text.replace(month_day.group(0), "", 1)

    spoken_month_day = re.search(rf"([{re.escape(_CN_NUM)}]+)月([{re.escape(_CN_NUM)}]+)[日号]", text)
    if spoken_month_day:
        month = _parse_chinese_int(spoken_month_day.group(1))
        day = _parse_chinese_int(spoken_month_day.group(2))
        if month is not None and day is not None:
            parsed = _safe_date(today.year, month, day)
            if parsed is not None:
                return parsed, text.replace(spoken_month_day.group(0), "", 1)
    return today, text


def _extract_category(text: str, categories: list[str]) -> tuple[str, str, str]:
    """按最长关键词归类。口语词会作为备注种子返回。"""
    allowed = set(categories)
    matches = [
        (keyword, category)
        for keyword, category in _CATEGORY_KEYWORDS
        if keyword in text and category in allowed
    ]
    if not matches:
        fallback = "其他" if "其他" in allowed else (categories[0] if categories else "其他")
        return fallback, text, ""

    keyword, category = max(matches, key=lambda item: len(item[0]))
    updated = text.replace(keyword, "", 1)
    if keyword != category:
        updated = updated.replace(category, "", 1)
    note_seed = "" if keyword == category else keyword
    return category, updated, note_seed


def _extract_amount(text: str) -> tuple[float | None, str]:
    """优先识别带「块/元」的金额，其次识别「花了三十五」。"""
    match = _AMOUNT_WITH_UNIT.search(text)
    if match is None:
        match = _AMOUNT_AFTER_VERB.search(text)
    if match is None:
        # 类别和日期去掉后，整句只剩一个数字时也当作金额
        naked = re.fullmatch(rf"({_SPOKEN_NUMBER})", text)
        if naked is None:
            return None, text
        amount = _parse_spoken_number(naked.group(1))
        if amount is None:
            return None, text
        return round(amount, 2), ""

    amount = _parse_spoken_number(match.group("num"))
    if amount is None:
        return None, text
    return round(amount, 2), text.replace(match.group(0), "", 1)


def _parse_spoken_number(raw: str) -> float | None:
    """把 35、35.5、三十五、三十五点五 转成数字。"""
    if re.fullmatch(r"\d+(?:\.\d+)?", raw):
        return float(raw)
    if "点" in raw:
        whole, fraction = raw.split("点", 1)
        whole_value = 0 if whole == "" else _parse_chinese_int(whole)
        if whole_value is None:
            return None
        digits: list[str] = []
        for character in fraction:
            if character not in _DIGITS:
                return None
            digits.append(str(_DIGITS[character]))
        if not digits:
            return None
        return float(f"{whole_value}.{''.join(digits)}")
    parsed = _parse_chinese_int(raw)
    if parsed is None:
        return None
    return float(parsed)


def _parse_chinese_int(raw: str) -> int | None:
    """解析一万以内的中文整数，例如十五、三十五、一百二十。"""
    if not raw:
        return None
    total = 0
    current = 0
    seen = False
    for character in raw:
        if character in _DIGITS:
            current = _DIGITS[character]
            seen = True
            continue
        if character in _UNITS:
            unit = _UNITS[character]
            total += (current or 1) * unit
            current = 0
            seen = True
            continue
        return None
    if not seen:
        return None
    return total + current


def _clean_note(text: str) -> str:
    """去掉口头禅，剩下的词作为备注。"""
    for filler in _FILLERS:
        text = text.replace(filler, "")
    return text.strip()


def _safe_date(year: int, month: int, day: int) -> date | None:
    """非法日期返回 None，避免一句口误中断记账。"""
    try:
        return date(year, month, day)
    except ValueError:
        return None
