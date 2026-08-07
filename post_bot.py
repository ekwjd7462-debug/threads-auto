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
# 한 달 실측 조회수 기반 v6: TOP리스트형(중앙값 1.6만)·조합랭킹형(9.7천)·세운시사형(9.0천) 3강 중심.
# 트랙 배분: 정보 18 / 모객 2 / 전환 2 / 성장 2
DAY_PLAN = {
    1:  {"track": "정보", "engine": "TOP리스트형", "category": "연애",
         "note": "새벽 감성. 연애 패턴·인연 리스트"},
    2:  {"track": "정보", "engine": "세운시사형", "category": "운세",
         "note": "병오년 하반기 일간별·띠별 흐름 (실측 대박 소재)"},
    3:  {"track": "정보", "engine": "통찰에세이형", "category": "인생",
         "note": "새벽 3시에 깨어 있는 사람에게 닿는 사유 글"},
    4:  {"track": "정보", "engine": "조합랭킹형", "category": "재물",
         "note": "돈 쓸어 담는·돈 냄새 맡는 조합 랭킹 (이 슬롯 중앙값 1.1만 실측)"},
    5:  {"track": "정보", "engine": "TOP리스트형", "category": "결혼",
         "note": "배우자 복·결혼 후 인생 역전 리스트 (최고 실적 소재)"},
    6:  {"track": "정보", "engine": "텐션불릿형", "category": "연애",
         "note": "아침 첫 스크롤에 걸리는 정착 상대 불릿"},
    7:  {"track": "정보", "engine": "조합랭킹형", "category": "연애",
         "note": "가만히 있어도 이성 줄 서는·주도권 쥐는 조합 랭킹"},
    8:  {"track": "정보", "engine": "특징해부형", "category": "연애",
         "note": "오행 과다자 연애 해부 (토다자 3.2만·수다자 1.9만 실측 소재)"},
    9:  {"track": "정보", "engine": "TOP리스트형", "category": "재물",
         "note": "돈복·재물 그릇 리스트"},
    10: {"track": "정보", "engine": "조합랭킹형", "category": "직업",
         "note": "일복·승진·사업 되는 조합 랭킹"},
    11: {"track": "성장", "engine": "연재예고형", "category": "일간",
         "note": "일간 10개 연재 시리즈 (을목 편 3.4만 실측) + 내일 예고 + 팔로우 걸쇠"},
    12: {"track": "정보", "engine": "TOP리스트형", "category": "결혼",
         "note": "점심 피크 최강 슬롯 (중앙값 1.6만·최고 9.9만). 배우자·결혼 소재 고정"},
    13: {"track": "전환", "engine": "시기포착형", "category": "대운",
         "note": "뭘 해도 안 풀리는 시기의 정체. 마지막 한 줄만 프로필 CTA"},
    14: {"track": "정보", "engine": "조합랭킹형", "category": "연애",
         "note": "연애 주도권·헤어나올 수 없는 조합 랭킹 (3.6만 실측)"},
    15: {"track": "모객", "engine": "생년참여형", "category": "운세",
         "note": "댓글에 생년 던지게 판 깔기"},
    16: {"track": "정보", "engine": "TOP리스트형", "category": "연애",
         "note": "연애는 망해도 결혼은 잘하는 류의 반전 리스트 (2.7만 실측)"},
    17: {"track": "정보", "engine": "세운시사형", "category": "운세",
         "note": "병오년 하반기 일간별 운세·생존 전략 (중앙값 9천·2.3만 실측)"},
    18: {"track": "정보", "engine": "조합랭킹형", "category": "재물",
         "note": "퇴근길. 재물·부업·투자 그릇 조합 랭킹"},
    19: {"track": "정보", "engine": "일상판별리스트형", "category": "인간관계",
         "note": "궁합·악연·귀인 판별 체크리스트"},
    20: {"track": "전환", "engine": "상담가이드형", "category": "상담",
         "note": "사주 볼 때 꼭 물어봐야 할 것들 (1.8만 실측 있음) + 마지막 한 줄 CTA"},
    21: {"track": "정보", "engine": "TOP리스트형", "category": "재물",
         "note": "골든타임. 돈 버는 팔자·재물운 리스트"},
    22: {"track": "성장", "engine": "연재예고형", "category": "신살",
         "note": "신살 시리즈 연재. 오늘의 살 하나 + 내일 예고 + 팔로우 걸쇠"},
    23: {"track": "모객", "engine": "생년참여형", "category": "궁합",
         "note": "밤 연애 감성. 커플 일간 궁합 떡밥"},
    0:  {"track": "정보", "engine": "조합랭킹형", "category": "인간관계",
         "note": "인복·귀인 부르는 조합 랭킹"},
}

ENGINE_PARTS = {
    "특징해부형": (1, 1), "지목단정형": (2, 3), "TOP리스트형": (2, 3),
    "텐션불릿형": (1, 1), "조합랭킹형": (1, 2), "일상판별리스트형": (1, 2),
    "통찰에세이형": (1, 2), "세운시사형": (1, 2), "떡밥관찰형": (1, 1),
    "생년참여형": (1, 1), "시기포착형": (1, 2), "상담가이드형": (1, 2),
    "연재예고형": (1, 1),
}

TRACK_RULES = {
    "정보": ("이 글은 정보 트랙임. 독자가 자기 사주를 대입하고 저장하게 만들 것. "
             "본문에 반드시 '경계선' 하나를 심을 것: 공통 해석을 다 준 뒤 딱 한 지점에서 "
             "원국 배치(월지·신강신약·조후·통관·합충)에 따라 결론이 갈린다는 걸 진짜 명리 논리로 보여줄 것. "
             "독자가 혼자 판정 못 하는 그 간극이 상담 수요가 됨. 상담·프로필 직접 언급은 절대 금지."),
    "모객": "이 글은 모객 트랙임. 답을 다 주지 말고 댓글이 쏟아지게 설계할 것. 상담·프로필 언급 절대 금지.",
    "전환": ("이 글은 전환 트랙임. 독자가 '내 사주 한번 제대로 확인해야겠다'는 생각이 들도록 "
             "혼자서는 답 못 내는 지점을 정확히 짚고, 원국을 펴면 구체적으로 뭐가 나오는지 "
             "결과물 3가지를 미리 보여줄 것 (예: 지금 대운이 몇 년 남았는지. 다음 대운 기운. 그때 피할 것). "
             "과장·공포팔이 금지. 마지막 한 줄에만: 프로필 링크로 오라는 CTA 허용. 그 이상 팔면 실패작."),
    "성장": ("이 글은 성장 트랙임. 연재 시리즈의 한 편처럼 쓰고 끝에 다음 편 예고와 "
             "팔로우·저장 유도 한 줄을 넣을 것. 상담 언급 금지."),
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

    editor_prompt = f"""당신은 백만 팔로워 계정을 여럿 키운 스레드 글쓰기 코치입니다. 18년차 사주 상담가의
초안에서 AI 티를 전부 걷어내고 사람이 갈겨쓴 것처럼 자연스럽게 다듬습니다.

## 반드시 고칠 것
- **사실 개연성 검증 최우선**: 경력은 18년(2008년 이후)임. 그보다 먼 과거 경험 주장이 있으면 삭제.
  특히 "지난 병오년" "지난 갑진년" 같은 간지 해 회상은 60년 전 얘기가 되므로 반드시 원리 설명으로 교체.
  날조된 % 통계는 "열에 여덟" 같은 어림 표현으로. 미래 단정 예언은 기운의 방향 표현으로 완화.
- **낙인 검증**: 특정 일간·일주·띠·살을 나쁜 사주로 단죄하는 내용이 있으면
  그 기운의 강점과 활용법으로 닫도록 고칠 것. 잘못된 상식을 때리는 건 유지. 사람을 때리는 건 금지.
- 소설 티 제거: 1인칭 서사 장면이나 특정 손님 1명의 사연이 있으면 들어내고
  집단 관찰("이 사주 손님들 절반이 ~더라")로 바꿀 것.
- 독자 호칭 검사: "야" "너" "너네" "네가" "네 사주" 같은 표현이 있으면 전부 "님" 호칭으로 바꾸거나
  호칭 없는 문장으로 고칠 것. 반말 톤은 유지.
- 맞춤법·띄어쓰기 완벽 교정: "할수있다"→"할 수 있다", "안됨"→"안 됨"(부정)/"안심됨"류 구분,
  "밖에 없다" 띄어쓰기, 조사 붙임 확인. 오탈자 하나가 전문성을 무너뜨림.
- 줄바꿈 검사: 훅 뒤 빈 줄. 한 줄에 문장 1~2개. 의미 덩어리 사이 빈 줄. 3문장 이상 붙어 있으면 분리.
- 쉼표 남용 제거: 쉼표 자리는 문장을 끊어라. 한 문장에 쉼표 2개 이상이면 무조건 분리.
- 이모지·해시태그·물결표가 있으면 삭제.
- 같은 어미 3줄 연속이면 리듬을 깰 것. 한 단어 문장 허용.
- "정리하자면" "결론적으로" "~인 셈" "~수 있음" "~하는 경향" 류 삭제. 단정으로 교체.
- 첫 줄이 밋밋하면 좁은 타겟 지목이나 단정·도발로 다시 쓸 것.
- 트랙 규칙 검증: {TRACK_RULES[slot['track']]}
- 사주 내용과 엔진({engine}) 구조는 유지. 표현은 마음껏 바꿀 것.
- 각 파트 공백 포함 480자 이하. 파트 수 {lo}~{hi}개 유지.
{avoid_block}

## 초안
{json.dumps({"title": title, "parts": parts}, ensure_ascii=False, indent=1)}

## 출력 형식
JSON 객체만: {{"title": "...", "parts": ["..."]}}"""

    final = gemini_call(api_key, editor_prompt, 0.7)
    title, parts = validate(final, lo, hi)

    verifier_prompt = f"""당신은 30년 경력의 사주명리 전문가이자 검수관입니다. 후배 상담가의 스레드 게시글을
읽고 명리학적으로 깔 수 있는 지점이 하나라도 있으면 고칩니다. 당신이 통과시킨 글은
다른 30년차가 봐도 반박할 수 없어야 합니다. 문제가 있으면 최소한으로 고쳐서 다시 출력하고
문제가 없으면 그대로 출력합니다.

## 이 글의 트랙
{slot['track']} — {TRACK_RULES[slot['track']]}

## 검사 항목
0. 통변의 근거 사슬: 모든 주장이 "명리 근거 → 해석 → 현실 장면"으로 이어지는가.
   근거 없는 주장은 근거를 붙이거나 삭제. 무조건 단정("이 살 있으면 무조건 이혼")은
   조건부 단정("이 배치에 이것까지 겹치면 무조건. 단 ~가 통관하면 달라짐")으로 교정.
   신강신약·조후·통관·투출·합충 같은 변수를 무시한 일률 해석이 있으면 조건을 달아줄 것.
1. 내부 모순: 앞 문장과 뒤 문장의 주장이 충돌하는가 (예: 앞에서 "무재가 유리" 뒤에서 "재성 있어야 부자")
2. 명리 이론 오류: 오행 상생상극(목생화 화생토 토생금 금생수 수생목 / 목극토 토극수 수극화 화극금 금극목),
   십신 판정(일간을 생하면 인성. 일간이 생하면 식상. 일간을 극하면 관성. 일간이 극하면 재성. 같으면 비겁),
   도화살=자오묘유 역마살=인신사해 화개살=진술축미, 병오년=천간 불 지지 불
2-1. 계절 세력 오류: 월지 흐름은 인묘진(봄 목) 사오미(여름 화 정점) 신유술(가을 금) 해자축(겨울 수).
   "하반기로 갈수록 화가 극에 달한다"처럼 세운과 계절을 뒤섞은 주장은 반드시 교정
2-2. 음간·양간 뭉뚱그림: 경금(원석: 불로 제련)과 신금(완성된 보석: 불에 상함)처럼 물상이 다른
   두 일간을 묶어 같은 풀이를 하면 각각 분리하거나 하나만 남길 것
2-3. 공포 훅: "인생 리셋될 준비나 해라" 같은 겁주기, 가정·부부가 망한다는 불안 유발이 있으면
   "준비하면 기회" 프레임으로 교체
3. 개수 일치: TOP N이나 N가지를 예고했으면 항목이 정확히 N개인가
4. 시간 개연성: 18년 경력(2008년 이후)과 모순되는 과거 경험 주장이 없는가. 지난 병오년은 1966년임
5. 독자 모욕: "멍청한 거다" 같은 독자 비하가 있으면 상식을 때리는 표현으로 교체
6. 최근 글과의 충돌: 아래 최근 글 목록과 정면 모순되는 주장이 있으면 각도를 조정
7. 맞춤법·띄어쓰기: 띄어쓰기 오류와 오탈자를 전부 교정 ("할수있다"→"할 수 있다" 류)
8. 전환 설계: 정보 트랙 글이면 원국 배치에 따라 결론이 갈리는 '경계선'이 본문에 있는지 확인.
   없으면 가장 자연스러운 지점에 진짜 명리 변수(월지·조후·통관 등)로 하나 심을 것
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
