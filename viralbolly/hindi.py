"""Hindi voiceover lines, built from fixed templates plus a name lexicon.

No machine translation on purpose: a small local model garbled names and invented
claims in tests, and a wrong claim in a gossip short is worse than a plain line.
Each line names who the story is about (in Devanagari, so the Hindi voice reads
it properly) and says what kind of item it is, using only what the title shows.
"""
from __future__ import annotations

import re

from .models import Post
from .voice import spoken

NUM = {1: "एक", 2: "दो", 3: "तीन", 4: "चार", 5: "पाँच", 6: "छह", 7: "सात", 8: "आठ", 9: "नौ", 10: "दस"}

# Full names first (longest match wins), then single first names that are unambiguous.
NAMES = {
    "aishwarya rai bachchan": "ऐश्वर्या राय बच्चन", "aishwarya rai": "ऐश्वर्या राय", "abhishek bachchan": "अभिषेक बच्चन",
    "amitabh bachchan": "अमिताभ बच्चन", "shah rukh khan": "शाहरुख़ ख़ान", "shahrukh khan": "शाहरुख़ ख़ान",
    "salman khan": "सलमान ख़ान", "aamir khan": "आमिर ख़ान", "saif ali khan": "सैफ़ अली ख़ान", "sara ali khan": "सारा अली ख़ान",
    "kareena kapoor": "करीना कपूर", "karisma kapoor": "करिश्मा कपूर", "ranbir kapoor": "रणबीर कपूर", "alia bhatt": "आलिया भट्ट",
    "ranveer singh": "रणवीर सिंह", "deepika padukone": "दीपिका पादुकोण", "katrina kaif": "कैटरीना कैफ़", "vicky kaushal": "विक्की कौशल",
    "priyanka chopra": "प्रियंका चोपड़ा", "parineeti chopra": "परिणीति चोपड़ा", "anushka sharma": "अनुष्का शर्मा",
    "virat kohli": "विराट कोहली", "shahid kapoor": "शाहिद कपूर", "mira rajput": "मीरा राजपूत", "janhvi kapoor": "जाह्नवी कपूर",
    "tiger shroff": "टाइगर श्रॉफ़", "kiara advani": "कियारा आडवाणी", "sidharth malhotra": "सिद्धार्थ मल्होत्रा",
    "kriti sanon": "कृति सेनन", "varun dhawan": "वरुण धवन", "ananya panday": "अनन्या पांडे", "hrithik roshan": "ऋतिक रोशन",
    "kangana ranaut": "कंगना रनौत", "taapsee pannu": "तापसी पन्नू", "bhumi pednekar": "भूमि पेडनेकर", "kartik aaryan": "कार्तिक आर्यन",
    "ajay devgn": "अजय देवगन", "kajol": "काजोल", "madhuri dixit": "माधुरी दीक्षित", "urvashi rautela": "उर्वशी रौतेला",
    "nora fatehi": "नोरा फ़तेही", "jacqueline fernandez": "जैक्लीन फ़र्नांडिस", "sonakshi sinha": "सोनाक्षी सिन्हा",
    "zaheer iqbal": "ज़हीर इक़बाल", "rakul preet singh": "रकुल प्रीत सिंह", "rakul preet": "रकुल प्रीत", "mouni roy": "मौनी रॉय",
    "tamannaah bhatia": "तमन्ना भाटिया", "vijay varma": "विजय वर्मा", "samantha": "समांथा", "rashmika mandanna": "रश्मिका मंदाना",
    "allu arjun": "अल्लू अर्जुन", "prabhas": "प्रभास", "ram charan": "राम चरण", "bharti singh": "भारती सिंह", "gola": "गोला",
    "haarsh limbachiyaa": "हर्ष लिंबाचिया", "munawar faruqui": "मुनव्वर फ़ारूक़ी", "elvish yadav": "एल्विश यादव",
    "kapil sharma": "कपिल शर्मा", "tejasswi prakash": "तेजस्वी प्रकाश", "shehnaaz gill": "शहनाज़ गिल", "neha kakkar": "नेहा कक्कड़",
    "sunil shetty": "सुनील शेट्टी", "athiya shetty": "आथिया शेट्टी", "kl rahul": "के एल राहुल", "neha dhupia": "नेहा धूपिया",
    "aditi rao hydari": "अदिति राव हैदरी", "siddharth": "सिद्धार्थ", "govinda": "गोविंदा", "vikrant massey": "विक्रांत मैसी",
    "triptii dimri": "तृप्ति डिमरी", "riddhi dogra": "रिद्धि डोगरा", "siwet tomar": "श्वेत तोमर", "anushka mitra": "अनुष्का मित्रा",
    "karishma tanna": "करिश्मा तन्ना", "ayushmann khurrana": "आयुष्मान खुराना", "arun govil": "अरुण गोविल", "kiran rao": "किरण राव",
    "vashu bhagnani": "वाशु भगनानी", "jackky bhagnani": "जैकी भगनानी", "akshay khanna": "अक्षय खन्ना", "akshay kumar": "अक्षय कुमार",
    "jubin nautiyal": "जुबिन नौटियाल", "saba azad": "सबा आज़ाद", "shruti sinha": "श्रुति सिन्हा", "kunal kemmu": "कुणाल खेमू",
    "yug devgan": "युग देवगन", "sharvari": "शरवरी", "himesh reshammiya": "हिमेश रेशमिया", "shreya ghoshal": "श्रेया घोषाल",
    "sooraj barjatya": "सूरज बड़जात्या", "ranbir": "रणबीर", "alia": "आलिया", "kareena": "करीना", "mira": "मीरा",
    "sara": "सारा", "saba": "सबा", "katrina": "कैटरीना", "salman": "सलमान", "shah rukh": "शाहरुख़", "aishwarya": "ऐश्वर्या",
    "deepika": "दीपिका", "ranveer": "रणवीर", "akshay": "अक्षय", "kriti": "कृति", "kiara": "कियारा", "janhvi": "जाह्नवी",
}
_KEYS = sorted(NAMES, key=len, reverse=True)
_PAT = re.compile(r"\b(" + "|".join(re.escape(k) for k in _KEYS) + r")\b", re.I)


def names_in(title: str, limit: int = 2) -> list[str]:
    """Devanagari names of people in the title, in order, without repeats."""
    text = spoken(title, 60)
    found: list[str] = []
    taken: list[tuple[int, int]] = []
    for m in _PAT.finditer(text):
        if any(a <= m.start() < b for a, b in taken):
            continue
        taken.append((m.start(), m.end()))
        n = NAMES[m[0].lower()]
        if n not in found and not any(n in f or f in n for f in found):
            found.append(n)
        if len(found) == limit:
            break
    return found


def _join(names: list[str]) -> str:
    return " और ".join(names)


def hook_line(count: int, seconds: float) -> str:
    return "आज की सबसे बड़ी बॉलीवुड खबरें, बस " + ("एक मिनट" if seconds <= 80 else "दो मिनट") + " में!"


def item_line(post: Post, num: int, first: bool, max_words: int = 0) -> str:
    lead = "और अब नंबर एक!" if num == 1 else (f"शुरू करते हैं नंबर {NUM.get(num, num)} से!" if first
                                               else f"नंबर {NUM.get(num, num)}!")
    who = _join(names_in(post.title))
    v = num % 3  # vary the wording so five lines in a row don't sound the same
    if post.kind == "video":
        if post.platform == "reddit":
            body = (f"रेडिट पर वायरल, {who} का ये वीडियो देखिए!" if who else "रेडिट पर ये वीडियो खूब वायरल है!")
        elif who:
            body = (f"{who} का ये वीडियो देखिए!", f"{who} का ये वीडियो आजकल खूब वायरल है!",
                    f"देखिए, {who} का ये नया वीडियो!")[v]
        else:
            body = ("ये वीडियो देखिए!", "ये वीडियो आजकल खूब चर्चा में है!", "देखिए, ये वीडियो खूब वायरल हो रहा है!")[v]
    elif post.kind == "news":
        body = f"{who} को लेकर ताज़ा खबर आई है!" if who else "एक ताज़ा खबर आई है!"
    else:
        body = f"रेडिट पर {who} को लेकर ये पोस्ट चर्चा में है। पढ़िए!" if who else "रेडिट पर ये पोस्ट चर्चा में है। पढ़िए!"
    return f"{lead} {body}"


WELCOME_LINE = "आपके डेली डोज़ ऑफ़ बॉलीवुड में आपका स्वागत है!"


HOOKS = [
    ["आज की टॉप {n} बॉलीवुड खबरें।", "नंबर एक सबसे बड़ा धमाका है, आखिर तक देखिए!"],
    ["बॉलीवुड में आज क्या चल रहा है?", "टॉप {n} खबरें, सीधे यहाँ!"],
    ["रुकिए! ये {n} खबरें मिस मत कीजिए।", "नंबर एक आखिर में है, देखते रहिए!"],
    ["गॉसिप टाइम!", "आज की टॉप {n} बॉलीवुड खबरें, शुरू करते हैं।"],
    ["आज बॉलीवुड में क्या हुआ?", "{n} बड़ी खबरें, बस दो मिनट में!"],
    ["अगर आप बॉलीवुड के दीवाने हैं, तो ये आपके लिए है!", "टॉप {n} खबरें, आखिर तक देखिए।"],
    ["{n} खबरें, एक वीडियो!", "देखिए आज बॉलीवुड में क्या चल रहा है।"],
]
HOOKS_RUMOUR = [
    ["बॉलीवुड की गरमा-गरम अफवाहें!", "{n} खबरें, कितनी सच हैं, आप तय कीजिए।"],
    ["रेडिट और न्यूज़ पर आज क्या चर्चा है?", "टॉप {n} रूमर्स, सीधे यहाँ!"],
    ["अफवाह या सच?", "आज की टॉप {n} बॉलीवुड चर्चाएँ, देखिए।"],
    ["गॉसिप अलर्ट!", "{n} ताज़ा रूमर्स, आखिर तक देखिए।"],
    ["फैंस क्या बात कर रहे हैं?", "आज की {n} सबसे चर्चित बॉलीवुड खबरें।"],
]
KICKERS_RUMOUR = ["🗣️ रूमर मिल", "👀 अफवाह या सच?", "📰 आज की चर्चा", "🔥 गॉसिप अलर्ट"]
KICKERS = ["🔥 आज का सबसे बड़ा धमाका", "⚡ आज की ताज़ा खबरें", "🎬 बॉलीवुड अपडेट", "👀 ये मिस मत कीजिए"]


def flow_hook(count: int, variant: int = 0, kind: str = "clips") -> list[str]:
    """Hook for --welcome: about the whole list, so it fits over whichever clip plays first.
    `variant` picks one of HOOKS (the pipeline varies it from day to day)."""
    pool = HOOKS_RUMOUR if kind == "rumours" else HOOKS
    return [t.format(n=NUM.get(count, count)) for t in pool[variant % len(pool)]]


OUTRO_LINE = "ऐसी ही रोज़ की बॉलीवुड खबरों के लिए फ़ॉलो करें!"
