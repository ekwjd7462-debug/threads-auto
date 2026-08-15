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

# 하루 시간표 (KST 시각 -> 슬롯). 하루 3회만 발행: 8시·12시·21시.
# v12: 별하(@saju_byeolha) 조회수 15만~82만 글 5편의 구조를 학습. 모든 글은 "별하3파트형" 하나로 통일.
#      1파트 훅(선언+임상권위+인과뒤집기) / 2파트 본론(항목 3~4개: 관찰+명리이유+대비) / 3파트 마무리(요약+오늘행동+맞춤예고).
#      슬롯은 소재 카테고리만 회전. 시간대별로 잘 먹히는 소재군을 배치.
DAY_PLAN = {
    8:  {"track": "별하", "engine": "별하3파트형",
         "note": "아침. 하루 시작하며 바로 확인할 수 있는 소재",
         "categories": ["집·공간(현관·침실·주방·책상·거울 중 하나)", "말·행동(말버릇·인사법·아침습관 중 하나)",
                        "잘 풀리는 사람 공통 습관", "돈(지갑·통장·돈 쓰는 습관 중 하나)"]},
    12: {"track": "별하", "engine": "별하3파트형",
         "note": "점심. 폰 보다가 자기 것 바로 확인하는 소재",
         "categories": ["폰·디지털(프사·배경화면·사진첩·알림 중 하나)", "관계(기 빨리는 사람·연락 끊긴 인연·오래가는 부부 중 하나)",
                        "돈 붙는 사람 공통점", "안 풀릴 때 티 나는 것"]},
    21: {"track": "별하", "engine": "별하3파트형",
         "note": "밤. 집에서 자기 전 둘러보며 확인하는 소재",
         "categories": ["집·공간(냉장고·화장실·옷장·신발장·조명 중 하나)", "때·흐름(운 바뀌기 직전 징조·대운 바뀔 때 신호 중 하나)",
                        "복 나가는 습관·물건", "잘 되는 집·잘 되는 사람 공통점"]},
}

ENGINE_PARTS = {
    "별하3파트형": (3, 3),
}

TRACK_RULES = {
    "별하": ("이 글은 별하 구조 트랙임. 반드시 3파트. "
             "1파트=훅: 첫 줄 단정형 선언(~있음/~보임/~티가 남/~해야 함), 둘째 줄 임상 권위(18년·수백 곳·손님들 보면), "
             "셋째 줄 인과 뒤집기 또는 놀람 예고, 필요시 개수 예고. 본론 절대 안 품. 200자 안팎. "
             "2파트=본론: 항목 3~4개. 각 항목은 구체 관찰 -> 명리·기운 원리 한 줄 -> 안 되는 사람과 대비. 임상 디테일 한 방 필수. "
             "3파트=마무리: 핵심 한 줄 요약 -> 오늘 할 수 있는 행동 하나 -> 맞춤 영역 예고 한 줄('내 사주 맞춤은 만세력 보고 하는 영역이고' 류). "
             "그 뒤 링크·프로필·팔로우·저장·DM 언급 절대 없음. "
             "소재는 독자가 지금 당장 자기 걸 확인할 수 있는 것만. 겁주기 금지, 미신은 걷어내고 태도로 번역할 것."),
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

    avoid_block = ""
    if avoid:
        avoid_block = ("\n## 중복 금지 (최근에 이미 쓴 글들. 소재·훅·표현·비유가 "
                       "겹치면 봇으로 보여서 실패작임. 반드시 다른 소재로)\n"
                       + "\n".join(f"- {a}" for a in avoid))

    writer_prompt = f"""당신은 아래 바이블을 체화한 18년차 사주 상담가입니다. 지금 팔로워가 기다리는
오늘의 글 1편을 씁니다. 조회수 15만~82만 나온 글의 구조를 그대로 따릅니다.

{style_guide}

## 이번 글 주문서
- 트랙: {slot['track']} — {TRACK_RULES[slot['track']]}
- 오늘 소재 방향: {slot['category']} / {slot['note']}
  이 방향 안에서 독자가 지금 당장 자기 걸 확인할 수 있는 좁은 소재 하나를 정해 파고들 것.
- 파트 3개 고정. 1파트=훅(200자 안팎, 본론 절대 안 품), 2파트=본론(항목 3~4개), 3파트=마무리.
- 각 파트는 공백 포함 480자 이하. 실제 개행문자로 줄바꿈. 의미 덩어리 사이 빈 줄.
- 음슴체(~임/~음/~거임/~더라/~함). 지시는 "~하셈/~보셈". "야/너/네가" 금지. 존댓말체 금지. 이모지 금지.
- 3파트 마지막에 링크·프로필·팔로우·저장·DM 언급 절대 없음.
{avoid_block}

## 출력 형식
JSON 객체만: {{"title": "관리용 짧은 제목", "parts": ["1파트 훅", "2파트 본론", "3파트 마무리"]}}"""

    draft = gemini_call(api_key, writer_prompt, 1.0)
    title, parts = validate(draft, lo, hi)

    editor_prompt = f"""당신은 스레드 사주 계정 에디터입니다. 초안에서 AI 티를 걷어내고, 조회수 15만~82만 글의
구조와 톤에 정확히 맞춥니다. 사람이 오래 상담하고 나서 툭 던지듯 쓴 글처럼.

## 구조 강제 (파트 3개 고정)
- 1파트(훅): 첫 줄은 단정형 선언 한 줄(~있음/~보임/~티가 남/~해야 함). 둘째 줄에 임상 권위(18년·수백 곳·손님들 보면·만세력 보고).
  셋째 줄에 인과 뒤집기 또는 놀람 예고. 개수 예고 가능("정확히 세 개 있더라"). 본론이 새어 나왔으면 잘라서 2파트로 보낼 것. 200자 안팎.
- 2파트(본론): 항목 3~4개. 항목마다 [구체 관찰 → 명리·기운 원리 한 줄 → 안 되는 사람과 대비]. 임상 디테일("백이면 백이었음" 류) 최소 1회.
  원리는 쉬운 비유로. 어려운 용어 나열이면 비유로 바꿀 것. 각 항목 사이 빈 줄.
- 3파트(마무리): 핵심 한 줄 요약 → 오늘 할 행동 하나 → 맞춤 예고 한 줄("내 사주 맞춤은 만세력 보고 하는 영역이고" 류). 그 뒤 아무것도 없음.

## 말투 강제
- 음슴체(~임/~음/~거임/~더라/~함/~않음)로 통일. 존댓말체(~습니다 ~해요 ~하세요 ~이에요)가 있으면 전부 바꿀 것.
- 지시는 "~하셈" "~보셈". "야/너/너네/네가" 금지. 필요하면 "님".
- "저장" "팔로우" "프로필" "링크" "DM" "타고 와" 삭제.
- 문장 짧게. 쉼표 2개 이상 문장은 끊을 것. 한 줄에 한 생각.
- 이모지·해시태그·물결표 삭제. "정리하자면" "결론적으로" "~인 셈" 삭제.
- 맞춤법·띄어쓰기 완벽 교정.
- 겁주기·저주 표현이 있으면 "물건에 저주가 걸렸다는 얘기가 아님. 그걸 방치하는 태도가 문제임" 식으로 태도로 번역.
- 트랙 규칙: {TRACK_RULES[slot['track']]}
- 각 파트 480자 이하. 파트 수 3개 유지.
{avoid_block}

## 초안
{json.dumps({"title": title, "parts": parts}, ensure_ascii=False, indent=1)}

## 출력 형식
JSON 객체만: {{"title": "...", "parts": ["...", "...", "..."]}}"""

    final = gemini_call(api_key, editor_prompt, 0.7)
    title, parts = validate(final, lo, hi)

    verifier_prompt = f"""당신은 사주 스레드 글 검수관입니다. 아래 글을 읽고 아래 항목만 검사해서
문제가 있으면 최소한으로 고쳐 다시 출력하고, 문제가 없으면 그대로 출력합니다.

## 이 글의 트랙
{slot['track']} — {TRACK_RULES[slot['track']]}

## 검사 항목
1. 3파트 구조: 1파트에 [선언 한 줄 + 임상 권위 + 인과 뒤집기/놀람 예고]가 있는가. 본론이 1파트에 새지 않았는가.
   2파트 항목이 3~4개이고 각 항목에 관찰+명리 이유+대비가 있는가. 3파트에 [요약 + 오늘 행동 + 맞춤 예고]가 있는가.
2. 명리 개연성: 사주 아는 사람이 봐도 틀리지 않아야 함. 단정 예언("무조건 이혼")·특정 일간/띠 낙인·겁주기 금지.
   미신을 그대로 믿으라는 식이면 태도·습관으로 번역할 것. 60년 주기 오류(지난 병오년=60년 전) 금지.
3. 임상 권위 문장이 최소 1회 있는가(18년/수백 곳/손님들 보면/백이면 백). 없으면 자연스럽게 넣을 것.
4. 소재가 독자가 지금 당장 자기 걸 확인할 수 있는 것인가. 추상적 명언이면 구체 소재로 끌어내릴 것.
5. 내부 모순: 앞뒤 주장 충돌 없는가.
6. 존댓말체·야/너·저장·팔로우·프로필·링크·DM 잔재가 있으면 삭제. 이모지·해시태그 삭제.
7. 맞춤법·띄어쓰기 전부 교정. 최근 글(아래)과 소재·문장이 겹치면 각도를 바꿀 것.
{avoid_block}

## 검사할 글
{json.dumps({"title": title, "parts": parts}, ensure_ascii=False, indent=1)}

## 출력 형식
JSON 객체만: {{"title": "...", "parts": ["...", "...", "..."]}}. 각 파트 480자 이하. 파트 수 3개 유지."""

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
        print(f"skip: KST {kst_hour}시는 발행 슬롯 아님 (하루 3회: 8·12·21시)")
        return 0

    # 요일마다 소재 카테고리 로테이션(반복 방지). 엔진은 별하3파트형 고정.
    slot = dict(slot)
    cats = slot.pop("categories")
    slot["category"] = cats[time.gmtime().tm_yday % len(cats)]

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
