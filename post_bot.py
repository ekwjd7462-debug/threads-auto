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
import sys
import time
import urllib.request
import urllib.parse

THREADS_API = "https://graph.threads.net/v1.0"
GEMINI_MODELS = ["gemini-3.5-flash", "gemini-2.5-flash", "gemini-flash-lite-latest"]
MAX_PART_LEN = 500

# 하루 시간표 (KST 시각 → 트랙/엔진/소재). KST 01~07시는 슬롯 없음 = 게시 안 함.
# 트랙 배분: 정보 9 / 모객 3 / 전환 2 / 성장 3
DAY_PLAN = {
    8:  {"track": "정보", "engine": "특징해부형", "category": "연애",
         "note": "출근길에 가볍게 읽고 공감할 오행·살 행동 패턴"},
    9:  {"track": "모객", "engine": "떡밥관찰형", "category": "직업",
         "note": "일·직장 관련 통념 뒤집는 관찰. 댓글로 답 쏟아지게"},
    10: {"track": "정보", "engine": "지목단정형", "category": "재물",
         "note": "돈 새는 사주 구조를 좁은 타겟으로 지목"},
    11: {"track": "성장", "engine": "연재예고형", "category": "일간",
         "note": "일간 10개 연재 시리즈. 오늘의 일간 하나 해부 + 내일 예고 + 팔로우 걸쇠"},
    12: {"track": "정보", "engine": "TOP리스트형", "category": "결혼",
         "note": "점심 피크. 결혼·배우자 소재 리스트"},
    13: {"track": "전환", "engine": "시기포착형", "category": "대운",
         "note": "뭘 해도 안 풀리는 시기의 정체. 마지막 한 줄만 프로필 CTA"},
    14: {"track": "정보", "engine": "조합랭킹형", "category": "연애",
         "note": "일간×신살 조합 랭킹 포맷"},
    15: {"track": "모객", "engine": "생년참여형", "category": "운세",
         "note": "댓글에 생년 던지게 판 깔기"},
    16: {"track": "정보", "engine": "텐션불릿형", "category": "연애",
         "note": "정착 상대 특징 불릿"},
    17: {"track": "정보", "engine": "세운시사형", "category": "운세",
         "note": "2026 병오년 하반기 시의성 운세. 일간별 또는 띠별"},
    18: {"track": "모객", "engine": "떡밥관찰형", "category": "재물",
         "note": "돈 관련 통념 반박 관찰"},
    19: {"track": "정보", "engine": "일상판별리스트형", "category": "인간관계",
         "note": "퇴근 피크. 궁합·악연·귀인 판별 체크리스트"},
    20: {"track": "전환", "engine": "상담가이드형", "category": "상담",
         "note": "사주 볼 때 꼭 물어봐야 할 것들. 소비자 가이드 + 마지막 한 줄 CTA"},
    21: {"track": "정보", "engine": "특징해부형", "category": "재물",
         "note": "골든타임. 재물 그릇·재성 해부"},
    22: {"track": "성장", "engine": "통찰에세이형", "category": "인생",
         "note": "밤 감성 사유 글. 끝에 저장 유도 한 줄"},
    23: {"track": "모객", "engine": "생년참여형", "category": "궁합",
         "note": "밤 연애 감성. 커플 일간 궁합 떡밥"},
    0:  {"track": "성장", "engine": "연재예고형", "category": "신살",
         "note": "신살 시리즈 연재. 오늘의 살 하나 + 내일 예고 + 팔로우 걸쇠"},
}

ENGINE_PARTS = {
    "특징해부형": (1, 1), "지목단정형": (2, 3), "TOP리스트형": (2, 3),
    "텐션불릿형": (1, 1), "조합랭킹형": (1, 2), "일상판별리스트형": (1, 2),
    "통찰에세이형": (1, 2), "세운시사형": (1, 2), "떡밥관찰형": (1, 1),
    "생년참여형": (1, 1), "시기포착형": (1, 2), "상담가이드형": (1, 2),
    "연재예고형": (1, 1),
}

TRACK_RULES = {
    "정보": "이 글은 정보 트랙임. 독자가 자기 사주를 대입하고 저장하게 만들 것. 상담·프로필 언급 절대 금지.",
    "모객": "이 글은 모객 트랙임. 답을 다 주지 말고 댓글이 쏟아지게 설계할 것. 상담·프로필 언급 절대 금지.",
    "전환": ("이 글은 전환 트랙임. 독자가 '내 사주 한번 제대로 확인해야겠다'는 생각이 들도록 "
             "혼자서는 답 못 내는 지점을 정확히 짚을 것. 과장·공포팔이 금지. "
             "마지막 한 줄에만 조용히: 프로필 링크로 오라는 CTA 허용. 그 이상 팔면 실패작."),
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


def validate(data, lo, hi):
    title = str(data.get("title", "")).strip()
    parts = data.get("parts", [])
    assert isinstance(parts, list)
    parts = [str(p).strip() for p in parts if str(p).strip()]
    if not (lo <= len(parts) <= hi):
        raise ValueError(f"part count {len(parts)} not in [{lo},{hi}]")
    if any(len(p) > MAX_PART_LEN for p in parts):
        raise ValueError("part too long")
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
- 반말 음슴체. 쉼표 최소화. 이모지 금지.
{avoid_block}

## 출력 형식
JSON 객체만: {{"title": "관리용 짧은 제목", "parts": ["파트1", "파트2"]}}"""

    draft = gemini_call(api_key, writer_prompt, 1.0)
    title, parts = validate(draft, lo, hi)

    editor_prompt = f"""당신은 백만 팔로워 계정을 여럿 키운 스레드 글쓰기 코치입니다. 18년차 사주 상담가의
초안에서 AI 티를 전부 걷어내고 사람이 갈겨쓴 것처럼 자연스럽게 다듬습니다.

## 반드시 고칠 것
- 소설 티 제거 최우선: 1인칭 서사 장면이나 특정 손님 1명의 사연이 있으면 들어내고
  집단 관찰("이 사주 손님들 절반이 ~더라")로 바꿀 것.
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
    return validate(final, lo, hi)


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
