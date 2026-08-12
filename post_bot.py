#!/usr/bin/env python3
"""드메르 스레드 자동 포스팅 봇 v5 — 4트랙 시간표 시스템.

매시간 GitHub Actions에서 실행:
1. 현재 KST 시각에 해당하는 DAY_PLAN 슬롯(트랙·엔진·소재) 선택
2. style_guide.md 바이블로 Gemini 2단계 생성 (작가 → 에디터)
3. Threads API로 체인 게시 (파트 수는 내용에 따라 유동)
4. state.json 갱신 (커밋은 워크플로가 수행)
"""
import json
import os
import re
import sys
import time
import urllib.request
import urllib.parse

THREADS_API = "https://graph.threads.net/v1.0"
GEMINI_MODELS = ["gemini-3.5-flash", "gemini-2.5-flash", "gemini-flash-lite-latest"]
MAX_PART_LEN = 500

# 하루 시간표 (KST 시각 → 트랙/엔진/소재). 24시간 게시.
# v11: "사주 보며 느낀 점" 사주적 통찰·명언 계정. 일반 명언 아님. 사주를 렌즈로 한 지혜.
# 벤치마크: 사주 철학 관점전복 글이 니치 최상위 (좋아요 5천+·리포스트 500+). 저장·공유가 조회수를 만듦.
# 단문(일침형·관점전복형)을 섞어 여백을 살림.
# 트랙 배분: 통찰 22 / 위로 2
DAY_PLAN = {
    0:  {"track": "통찰", "engine": "사주일침형", "category": "운명",
         "note": "자정. 사주로 삶을 꿰뚫는 한 줄 일침"},
    1:  {"track": "위로", "engine": "사주위로형", "category": "시기",
         "note": "새벽. 안 풀리는 시기를 사주로 다독임. 끝에만 '나를 찾아와' 한 번"},
    2:  {"track": "통찰", "engine": "관점전복형", "category": "운명",
         "note": "새벽 2시. 운명·팔자에 대한 통념을 뒤집는 짧은 통찰"},
    3:  {"track": "통찰", "engine": "사주느낀점형", "category": "인생",
         "note": "새벽 3시. '사주 보며 느낀 점' 연재. 담담한 깨달음 1~2개"},
    4:  {"track": "통찰", "engine": "공통점관찰형", "category": "관계",
         "note": "사람 인연·관계에서 본 사주 공통점"},
    5:  {"track": "통찰", "engine": "사주일침형", "category": "인생",
         "note": "동트기 전. 한 줄 일침"},
    6:  {"track": "통찰", "engine": "관점전복형", "category": "복",
         "note": "아침. 복·운에 대한 통념 뒤집기"},
    7:  {"track": "통찰", "engine": "공통점관찰형", "category": "돈",
         "note": "출근길. 돈 잘 버는 사람들 사주에서 본 공통점"},
    8:  {"track": "통찰", "engine": "사주느낀점형", "category": "인생",
         "note": "아침. '사주 N만 명 보고 느낀 점' 연재 (플래그십)"},
    9:  {"track": "통찰", "engine": "사주일침형", "category": "돈",
         "note": "오전. 돈·운에 대한 한 줄 일침"},
    10: {"track": "통찰", "engine": "공통점관찰형", "category": "관계",
         "note": "오전. 잘 사는 부부·인연 사주 공통점"},
    11: {"track": "통찰", "engine": "운흐름통찰형", "category": "시기",
         "note": "때·흐름에 대한 통찰. 대운의 의미를 지혜로"},
    12: {"track": "통찰", "engine": "사주느낀점형", "category": "운명",
         "note": "점심. 느낀 점 연재. 운명을 대하는 태도"},
    13: {"track": "통찰", "engine": "관점전복형", "category": "운명",
         "note": "오후. 팔자 통념 뒤집는 짧은 통찰"},
    14: {"track": "통찰", "engine": "공통점관찰형", "category": "복",
         "note": "오후. 복 많은 사람들 사주·태도 공통점"},
    15: {"track": "통찰", "engine": "사주일침형", "category": "관계",
         "note": "나른한 오후. 관계에 대한 한 줄 일침"},
    16: {"track": "통찰", "engine": "운흐름통찰형", "category": "시기",
         "note": "오후. 잘 안 될 때의 진짜 의미를 지혜로"},
    17: {"track": "통찰", "engine": "사주느낀점형", "category": "인생",
         "note": "퇴근 무렵. 느낀 점 연재"},
    18: {"track": "통찰", "engine": "관점전복형", "category": "복",
         "note": "퇴근길. 복·운 통념 뒤집기 (짧게)"},
    19: {"track": "통찰", "engine": "공통점관찰형", "category": "관계",
         "note": "저녁. 오래가는 인연 사주 공통점"},
    20: {"track": "위로", "engine": "사주위로형", "category": "시기",
         "note": "저녁. 힘든 시기를 사주로 위로. 끝에만 '나를 찾아오면 됨' 한 번"},
    21: {"track": "통찰", "engine": "사주일침형", "category": "운명",
         "note": "밤. 하루 닫는 한 줄 일침"},
    22: {"track": "통찰", "engine": "사주느낀점형", "category": "인생",
         "note": "밤. 곱씹게 되는 느낀 점 연재"},
    23: {"track": "통찰", "engine": "관점전복형", "category": "운명",
         "note": "자정 직전. 운명 통념 뒤집는 한 줄"},
}

ENGINE_PARTS = {
    "사주일침형": (1, 1), "관점전복형": (1, 1), "사주느낀점형": (1, 2),
    "공통점관찰형": (1, 2), "운흐름통찰형": (1, 2), "사주위로형": (1, 2),
}

TRACK_RULES = {
    "통찰": ("이 글은 사주 통찰 트랙임. 사주를 오래 본 사람만 쓸 수 있는 통찰·명언·깨달음을 건넬 것. "
             "일반 명언 금지 — 반드시 사주·운명·팔자·복·때(대운)라는 렌즈가 담겨야 함. "
             "어려운 명리 용어 나열·풀이 금지. 지식 자랑 금지. 읽는 사람이 저장하고 공유하고 싶은 한 문장이 목표. "
             "겁주기·공포 조장 금지. 상담·프로필·팔로우·저장 언급 절대 금지."),
    "위로": ("이 글은 사주위로 트랙임. 사주를 가벼운 렌즈로 써서 힘든 사람을 위로할 것 "
             "(예: '안 풀리는 시기는 게으른 게 아니라 흐름이 바뀌는 중임'). "
             "사주 용어 나열 금지. 공포·불안 조장 금지. 위로가 먼저, 사주는 거들 뿐. "
             "마지막 한 줄에만 담백하게: '혼자 버겁거든 나를 찾아와' 정도. 안 넣어도 됨. "
             "'프로필' '링크' '팔로우' '저장' 금지."),
}


def http_json(url, method="GET", body=None, timeout=60):
    req = urllib.request.Request(url, method=method)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def gemini_call(api_key, prompt, temperature):
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": temperature,
        },
    }
    backoffs = [5, 15, 30, 45, 60, 90]
    last = None
    for attempt in range(6):
        model = GEMINI_MODELS[min(attempt // 2, len(GEMINI_MODELS) - 1)]
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={api_key}")
        try:
            resp = http_json(url, "POST", body, timeout=120)
            return json.loads(resp["candidates"][0]["content"]["parts"][0]["text"])
        except Exception as e:
            last = e
            print(f"[gemini] attempt {attempt+1} ({model}) failed: {e}", flush=True)
            time.sleep(backoffs[attempt])
    raise RuntimeError(f"gemini failed after 6 attempts: {last}")


# 프로그램 차원 금지 패턴 — 걸리면 해당 생성 시도 자체를 실패 처리하고 재생성
FORBIDDEN = [
    (re.compile(r"당신"), "호칭 위반(당신)"),
    (re.compile(r"너네|너희|네 사주|(^|\s)네가\s"), "호칭 위반(너)"),
    (re.compile(r"\d+\s*%"), "퍼센트 통계 날조"),
    (re.compile(r"지난\s*[갑을병정무기경신임계][자축인묘진사오미신유술해]년"), "60년 전 간지 회상"),
    (re.compile(r"[\U0001F000-\U0001FAFF☀-➿]"), "이모지"),
    (re.compile(r"#\w"), "해시태그"),
    (re.compile(r"저장\s*해|저장각|저장\s*각|저장\s*추천"), "저장 유도 금지"),
    (re.compile(r"팔로우"), "팔로우 구걸 금지"),
    (re.compile(r"프로필|링크\s*(눌러|클릭|타고)|타고\s*(와|오)"), "프로필·링크 언급 금지"),
    (re.compile(r"(습니다|합니다|됩니다|입니다|십니다|봅니다|옵니다|납니다|줍니다|랍니다|"
                r"드립니다|드립니당|하세요|되세요|이에요|예요|해요|드려요|십시오|셔요|세욤)"),
     "존댓말 금지(음슴체·반말만)"),
    # 해요체 종결('~어요/~네요/~군요/셨어요' 등): 문장 끝의 '요'를 잡음 (중요/필요/요일 등 어중 요는 통과)
    (re.compile(r"[가-힣]요(?=[\s.!?…\"'」』)\]]|$)"), "존댓말 금지(해요체 종결)"),
]


def validate(data, lo, hi):
    title = str(data.get("title", "")).strip()
    parts = data.get("parts", [])
    assert isinstance(parts, list)
    parts = [str(p).strip() for p in parts if str(p).strip()]
    if not (lo <= len(parts) <= hi):
        raise ValueError(f"part count {len(parts)} not in [{lo},{hi}]")
    if any(len(p) > MAX_PART_LEN for p in parts):
        raise ValueError("part too long")
    for p in parts:
        for pat, why in FORBIDDEN:
            if pat.search(p):
                raise ValueError(f"forbidden pattern: {why}")
    # "TOP N" / "N가지" 예고 개수와 실제 번호 항목 수 일치 검사
    body = "\n".join(parts)
    m = re.search(r"(?:TOP|톱)\s*(\d)|(\d)\s*가지", body)
    if m:
        n = int(m.group(1) or m.group(2))
        items = {int(x) for x in re.findall(r"^\s*(\d)[.)]", body, re.M)}
        if items and (len(items) < n or max(items) != n):
            raise ValueError(f"list count mismatch: promised {n}, numbered {sorted(items)}")
    if not title:
        title = parts[0].split("\n")[0][:60]
    return title, parts


def generate(api_key, slot, style_guide, avoid):
    """금지 패턴(존댓말·팔로우 등)에 걸리면 실패 대신 최대 3회 재생성."""
    last = None
    for attempt in range(3):
        try:
            return _generate_once(api_key, slot, style_guide, avoid)
        except (ValueError, AssertionError) as e:
            last = e
            print(f"[generate] attempt {attempt+1} rejected: {e} -> 재생성", flush=True)
            time.sleep(5)
    raise RuntimeError(f"generate failed after 3 attempts: {last}")


def _generate_once(api_key, slot, style_guide, avoid):
    engine = slot["engine"]
    lo, hi = ENGINE_PARTS[engine]
    parts_rule = ("파트 1개로 완결하셈." if lo == hi == 1 else
                  f"파트 수는 내용이 필요로 하는 만큼 {lo}~{hi}개에서 직접 정할 것. "
                  "억지로 늘리지도 자르지도 말 것.")

    avoid_block = ""
    if avoid:
        avoid_block = ("\n## 중복 금지 (최근에 이미 쓴 글들. 주제·훅·표현·비유가 "
                       "겹치면 봇으로 보여서 실패작임)\n"
                       + "\n".join(f"- {a}" for a in avoid))

    writer_prompt = f"""당신은 아래 바이블을 체화한 18년차 사주 상담가입니다. 지금 팔로워가 기다리는
오늘의 글 1편을 씁니다.

{style_guide}

## 이번 글 주문서
- 트랙: {slot['track']} — {TRACK_RULES[slot['track']]}
- 글 엔진: {engine} (바이블의 엔진 정의를 정확히 따를 것)
- 소재 방향: {slot['category']} / {slot['note']}
- {parts_rule}
- 각 파트는 공백 포함 480자 이하. 실제 개행문자로 줄바꿈.
- 반말 음슴체. 독자 호칭은 "님"만 사용 ("야" "너" "네가" 금지). 쉼표 최소화. 이모지 금지.
{avoid_block}

## 출력 형식
JSON 객체만: {{"title": "관리용 짧은 제목", "parts": ["파트1", "파트2"]}}"""

    draft = gemini_call(api_key, writer_prompt, 1.0)
    title, parts = validate(draft, lo, hi)

    editor_prompt = f"""당신은 감성 글을 잘 쓰는 스레드 에디터입니다. 초안에서 AI 티를 전부 걷어내고
사람이 조용히 눌러쓴 것처럼 자연스럽게 다듬습니다. 이 계정은 명언·인생의 진리·공감·위로를
건네는 감성 계정입니다. 자극적인 훅이나 정보 나열이 아니라, 마음에 남는 한 문장이 목표입니다.

## 반드시 고칠 것
- **말투 고정: 반말·음슴체 또는 담담한 평서체(~다)만 허용.** 존댓말체(~습니다 ~해요 ~하세요 ~이에요 ~십시오)가
  있으면 전부 반말·평서체로 바꿀 것. 명언·진리 글은 "~다"로 담담하게 끝내도 됨.
- **독자 호칭**: "야" "너" "너네" "네가"는 금지. 필요하면 "님" 또는 호칭 없는 문장으로.
  명언·진리형은 호칭 없이 impersonal하게 쓰는 게 더 좋음.
- **금지 문구 제거**: "저장"(저장해둬/저장각), "팔로우" 구걸, "프로필/링크/타고 와"가 있으면 삭제.
  사주위로 트랙에서만 상담 유도 가능하고, 그때도 "나를 찾아와" "나를 찾아오면 됨"으로만.
- 설교·훈계 톤 제거: "~해야 한다" 명령조가 과하면 담담한 관찰로 바꿀 것. 독자를 가르치려 들지 말 것.
- 정보 나열·사주 용어 남발 제거 (사주위로 트랙 제외). 감성 글에 어려운 용어가 있으면 뺄 것.
- 맞춤법·띄어쓰기 완벽 교정 ("할수있다"→"할 수 있다" 류). 오탈자 하나가 글의 격을 무너뜨림.
- 줄바꿈: 한 줄에 문장 1~2개. 의미 덩어리 사이 빈 줄. 짧은 글일수록 여백을 살릴 것.
- 쉼표 남용 제거: 쉼표 자리는 문장을 끊어라. 한 문장에 쉼표 2개 이상이면 분리.
- 이모지·해시태그·물결표 삭제.
- "정리하자면" "결론적으로" "~인 셈" 류 삭제.
- 엔진({engine})의 형식과 길이는 유지. 단문형(사주일침형·관점전복형)은 절대 늘리지 말고 짧게 둘 것.
- 사주 렌즈 유지: 순수 일반 명언이 되면 안 됨. 사주·운명·복·때의 관점이 살아 있어야 함.
- 트랙 규칙 검증: {TRACK_RULES[slot['track']]}
- 각 파트 공백 포함 480자 이하. 파트 수 {lo}~{hi}개 유지.
{avoid_block}

## 초안
{json.dumps({"title": title, "parts": parts}, ensure_ascii=False, indent=1)}

## 출력 형식
JSON 객체만: {{"title": "...", "parts": ["..."]}}"""

    final = gemini_call(api_key, editor_prompt, 0.7)
    title, parts = validate(final, lo, hi)

    verifier_prompt = f"""당신은 감성 글 검수관입니다. 아래 스레드 게시글을 읽고 아래 항목만 검사해서
문제가 있으면 최소한으로 고쳐 다시 출력하고, 문제가 없으면 그대로 출력합니다.

## 이 글의 트랙
{slot['track']} — {TRACK_RULES[slot['track']]}

## 검사 항목
1. 사주 렌즈 필수: 이 계정은 '사주 보며 느낀 통찰'을 파는 곳임. 사주·운명·팔자·복·때(대운) 렌즈가
   전혀 없는 순수 일반 명언이면, 그 통찰을 사주의 관점으로 다시 얹을 것.
2. 명리 개연성(가벼운 수준): 사주를 아는 사람이 봐도 틀리지 않아야 함. 단정적 예언("이 사주는 무조건 이혼")·
   특정 일간/띠 낙인·겁주기는 금지. "안 풀리는 시기는 흐름이 바뀌는 중" 같은 지혜로운 비유는 좋음.
   어려운 명리 용어 나열이 있으면 빼고 통찰만 남길 것.
3. 내부 모순: 앞뒤 주장이 충돌하지 않는가.
4. 진부함: 뻔한 클리셰면 사주만이 줄 수 있는 한 끗으로 비틀 것. 억지 반전은 금지.
5. 설교·훈계: 가르치려 드는 명령조가 과하면 담담한 관찰·깨달음으로 완화.
6. 저장·공유 욕구: 읽고 나서 저장하거나 남에게 보내고 싶은 한 문장이 있는가. 없으면 마지막 줄을 벼릴 것.
7. 연재형(사주느낀점형)일 때: "N탄" 같은 편수 표기는 자연스럽게. 각 항목이 진짜 겪은 사람만 아는 디테일인가.
8. 맞춤법·띄어쓰기 전부 교정. 최근 글(아래)과 주제·문장이 겹치면 각도를 바꿀 것.
{avoid_block}

## 검사할 글
{json.dumps({"title": title, "parts": parts}, ensure_ascii=False, indent=1)}

## 출력 형식
JSON 객체만: {{"title": "...", "parts": ["..."]}}. 각 파트 480자 이하. 파트 수 {lo}~{hi}개 유지."""

    checked = gemini_call(api_key, verifier_prompt, 0.3)
    return validate(checked, lo, hi)


def threads_post(token, text, reply_to=None):
    params = {"media_type": "TEXT", "text": text, "access_token": token}
    if reply_to:
        params["reply_to_id"] = reply_to
    create = http_json(f"{THREADS_API}/me/threads?" + urllib.parse.urlencode(params),
                       method="POST")
    if "id" not in create:
        raise RuntimeError(f"create failed: {create}")
    time.sleep(3)
    pub = http_json(f"{THREADS_API}/me/threads_publish?" + urllib.parse.urlencode(
        {"creation_id": create["id"], "access_token": token}), method="POST")
    if "id" not in pub:
        raise RuntimeError(f"publish failed: {pub}")
    return pub["id"]


def main():
    token = os.environ["THREADS_TOKEN"]
    gemini_key = os.environ["GEMINI_API_KEY"]

    kst_hour = (time.gmtime().tm_hour + 9) % 24
    slot = DAY_PLAN.get(kst_hour)
    if slot is None:
        print(f"skip: KST {kst_hour}시는 게시 슬롯 없음 (새벽 죽은 시간대)")
        return 0

    state = json.load(open("state.json", encoding="utf-8"))
    style_guide = open("style_guide.md", encoding="utf-8").read()

    hist = state.get("history", [])
    if hist:
        last_ts = time.mktime(time.strptime(hist[-1]["posted_at_utc"], "%Y-%m-%dT%H:%M:%SZ"))
        if time.time() - last_ts < 45 * 60:
            print(f"skip: last post {int((time.time()-last_ts)//60)} min ago (<45)")
            return 0

    idx = state["next_index"]

    avoid = []
    for h in hist[-25:]:
        avoid.append(h["title"])
        if h.get("hook"):
            avoid.append(h["hook"])

    print(f"[post] #{idx}: KST{kst_hour}시 {slot['track']}/{slot['engine']} × {slot['category']}",
          flush=True)

    title, parts = generate(gemini_key, slot, style_guide, avoid)

    prev = None
    ids = []
    for i, part in enumerate(parts):
        pid = threads_post(token, part, reply_to=prev)
        ids.append(pid)
        prev = pid
        print(f"  part {i+1}/{len(parts)} -> {pid}", flush=True)
        time.sleep(3)

    state["next_index"] = idx + 1
    state.setdefault("history", []).append(
        {"index": idx, "title": title, "hook": parts[0].split("\n")[0][:80],
         "engine": slot["engine"], "track": slot["track"], "root_id": ids[0],
         "posted_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    json.dump(state, open("state.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"[done] #{idx} posted ({len(parts)} parts). next_index={idx+1}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
