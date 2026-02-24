import jieba
import re
from pycccedict.cccedict import CcCedict

def numbered_to_tonemarks(s: str) -> str:
    """Convert numbered pinyin like 'bei3 jing1' to tone marks like 'běi jīng'."""
    _tone_marks = {
        'a': 'āáǎà', 'e': 'ēéěè', 'i': 'īíǐì',
        'o': 'ōóǒò', 'u': 'ūúǔù', 'ü': 'ǖǘǚǜ',
    }
    def _convert_syllable(m):
        syllable = m.group(1).lower()
        tone = int(m.group(2))
        if tone == 5 or tone == 0:
            return syllable
        # find the vowel to place the mark on (standard rules)
        # rule: 'a' or 'e' always take the mark
        for v in ('a', 'e'):
            if v in syllable:
                return syllable.replace(v, _tone_marks[v][tone - 1])
        # 'ou' → mark goes on 'o'
        if 'ou' in syllable:
            return syllable.replace('o', _tone_marks['o'][tone - 1])
        # otherwise mark the last vowel
        for idx in range(len(syllable) - 1, -1, -1):
            ch = syllable[idx]
            if ch in _tone_marks:
                return syllable[:idx] + _tone_marks[ch][tone - 1] + syllable[idx + 1:]
        return syllable
    # handle ü represented as v
    s = s.replace('v', 'ü')
    return re.sub(r'([a-züA-ZÜ]+)([0-5])', _convert_syllable, s)

text = "我来到北京清华大学，想学习自然语言处理和机器学习。"

# Improve segmentation for phrase-level dictionary matches
for w in ["清华大学", "自然语言处理", "机器学习"]:
    jieba.add_word(w)

tokens = list(jieba.cut(text))
cc = CcCedict()

def is_chinese_token(s: str) -> bool:
    return any('\u4e00' <= ch <= '\u9fff' for ch in s)

for tok in tokens:
    if not is_chinese_token(tok):
        print(f"\nTOKEN: {tok}\n  (punctuation/symbol)")
        continue

    entry = cc.get_entry(tok)  # returns dict or None (depends on token)
    print(f"\nTOKEN: {tok}")

    if not entry:
        print("  No dictionary entry found")
        continue

    print(f"  Simplified:  {entry.get('simplified')}")
    print(f"  Pinyin:      {numbered_to_tonemarks(entry.get('pinyin', ''))}")

    defs = entry.get("definitions", [])
    if defs:
        print("  Definitions:")
        for i, d in enumerate(defs, 1):
            print(f"    {i}. {d}")
    else:
        print("  Definitions: (none)")