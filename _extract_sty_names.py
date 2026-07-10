# -*- coding: utf-8 -*-
import gspread, re, json
from google.oauth2.credentials import Credentials
creds=Credentials.from_authorized_user_file("token.json")
gc=gspread.authorize(creds)
sh=gc.open_by_key("1aAYXjJPFgWCJAmZabc_f-f-wF3z492cIeDE-aVlx-HY")
ws=sh.get_worksheet_by_id(1392316906)
rows=ws.get("A1:G1000")
STY=re.compile(r"\b([A-Z]{2}[A-Z0-9]{7})\b")
names={}
mode="top"
for r in rows:
    r=(r+[""]*7)[:7]
    b,c,e=str(r[1]).strip(),str(r[2]).strip(),str(r[4]).strip()
    if b.startswith("■"): mode="bar"; continue
    if mode=="top":
        # 헤더행: C=신품번, E=품명((MAIN)/(SUB) 접두 제거)
        if c and e and ("(" in e or len(e)>6) and not e.replace(".","").isdigit():
            nm=re.sub(r"^\((MAIN|SUB)\)\s*","",e).strip()
            if STY.match(c) and c not in names: names[c]=nm
    else:
        # ■ 라인: "- STYLE 설명 — N: ..." 또는 "- 설명 (STYLE) → uid"
        m=STY.search(b)
        if m:
            st=m.group(1)
            # STYLE 뒤 설명 or 앞 설명
            after=b.split(st,1)[1]
            desc=re.split(r"[—:→]",after)[0].strip(" -()（）")
            if len(desc.strip("[]().-—  "))<2:  # 뒤에 설명 없음(예: "(STYLE) → uid") → 앞쪽(설명 (STYLE))
                desc=re.split(r"[(（]",b.split(st,1)[0])[0].strip(" -")
            if len(desc.strip("[]().-—  "))>=2 and st not in names: names[st]=desc[:30]
print(f"style→name {len(names)}개")
for k in list(names)[:8]: print(" ",k,names[k])
# 기존 매핑에 병합
m=json.load(open("hero_goods_26ss.json",encoding="utf-8"))
m["style_names"]=names
json.dump(m,open("hero_goods_26ss.json","w",encoding="utf-8"),ensure_ascii=False,indent=1)
print("hero_goods_26ss.json에 style_names 추가")
