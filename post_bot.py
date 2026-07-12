#!/usr/bin/env python3
"""드메르 스레드 자동 포스팅 봇 v3.

매시간 GitHub Actions에서 실행:
1. state.json에서 다음 글 번호·최근 이력 확인
2. 글 엔진 로테이션에 따라 Gemini 2단계 생성 (작가 → 에디터)
3. 생성된 글을 Threads API로 체인 게시 (파트 수는 내용에 따라 1~4개 유동)
4. state.json 갱신 (커밋은 워크플로가 수행)
"""
import json
import os
import sys
import time
import re
import urllib.request
import urllib.parse

THREADS_API = "https://graph.threads.net/v1.0"
GEMINI_MODELS = ["gemini-3.5-flash", "gemini-2.5-flash", "gemini-flash-lite-latest"]
MAX_PART_LEN = 500

# KST 01~07시(UTC 16~22시)는 조회수가 안 나오는 죽은 시간대 → 게시 안 함
DEAD_UTC_HOURS = set(range(16, 23))

# 글 엔진 로테이션 — 스레드 10만+ 바이럴 실측 분석 기반 가중치.
# 상담실화형(대사 중심 목격담)과 통념반박떡밥형(댓글 제조기)이 주력.
ENGINES = [
    ("상담실화형", "연애"), ("통념반박떡밥형", "재물"), ("공감리스트형", "연애"),
    ("상담실화형", "결혼"), ("실전정보리스트형", "재물"), ("통찰에세이형", "인생"),
    ("통념반박떡밥형", "결혼"), ("상담실화형", "직업"), ("공감리스트형", "인간관계"),
    ("실전정보리스트형", "연애"), ("통념반박떡밥형", "직업"), ("상담실화형", "재물"),
    ("초단문선언형", "인생"), ("통찰에세이형", "연애"), ("상담실화형", "인간관계"),
    ("통념반박떡밥형", "연애"),
]

ENGINE_PARTS = {  # 엔진별 허용 파트 수 (내용에 맞게 이 범위에서 자유 선택)
    "상담실화형": (2, 4), "통념반박떡밥형": (1, 1), "통찰에세이형": (1, 2),
    "공감리스트형": (1, 2), "실전정보리스트형": (2, 3), "초단문선언형": (1, 1),
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


def generate(api_key, engine, category, style_guide, avoid):
    lo, hi = ENGINE_PARTS[engine]
    parts_rule = (f"파트 1개로 완결하세요." if lo == hi == 1 else
                  f"파트 수는 내용이 필요로 하는 만큼 {lo}~{hi}개에서 직접 정하세요. "
                  f"억지로 늘리지도, 좋은 내용을 자르지도 마세요.")

    avoid_block = ""
    if avoid:
        avoid_block = ("\n## 중복 금지 (최근에 이미 쓴 글들 — 주제·훅·표현·비유·디테일이 "
                       "겹치면 봇으로 보여서 실패작임)\n"
                       + "\n".join(f"- {a}" for a in avoid))

    writer_prompt = f"""당신은 아래 바이블을 체화한 18년차 사주 상담가이자, 스레드 게시글 대행사에서
가장 비싼 작가입니다. 지금 조회수 10만을 목표로 글 1편을 씁니다.

{style_guide}

## 이번 글 주문서
- 글 엔진: {engine} (바이블의 해당 엔진 정의를 정확히 따를 것)
- 소재 카테고리: {category} (사주와 엮되, 사주 용어보다 사람 이야기가 앞서야 함)
- {parts_rule}
- 각 파트는 공백 포함 480자 이하, 실제 개행문자로 줄바꿈.
{avoid_block}

## 출력 형식
JSON 객체만: {{"title": "관리용 짧은 제목", "parts": ["파트1", "파트2"]}}"""

    draft = gemini_call(api_key, writer_prompt, 1.0)
    title, parts = validate(draft, lo, hi)

    editor_prompt = f"""당신은 스레드 게시글 대행사의 수석 에디터입니다. 18년차 사주 상담가의 초안이
사람 냄새가 나는지 검수하고, AI 티가 나는 부분을 전부 고쳐서 다시 씁니다.

## 반드시 고칠 것
- 같은 어미가 3줄 이상 연속되면 리듬을 깨라. 한 단어 문장, 반 줄 문장도 써라.
- 설명하는 문장은 장면이나 대사(큰따옴표)로 바꿔라.
- "정리하자면", "결론적으로", "~인 셈이다", "~라고 할 수 있다", "~하는 경향이 있다" 류는 삭제.
- 첫 줄이 정보 예고면 사건·고백·도발로 다시 써라. 첫 줄에서 스크롤이 멈춰야 한다.
- 마지막이 요약이면 여운 한 줄이나 질문으로 바꿔라.
- 훅 뒤 빈 줄, 한 줄에 문장 1~2개, 덩어리 사이 빈 줄 — 줄바꿈이 무너져 있으면 살려라.
- 사주 내용, 글 엔진({engine}), 전체 이야기는 유지하되 표현은 마음껏 바꿔라.
- 각 파트 공백 포함 480자 이하 유지. 파트 수는 {lo}~{hi}개 범위에서 유지 또는 조정.
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

    if time.gmtime().tm_hour in DEAD_UTC_HOURS:
        print(f"skip: dead hour (UTC {time.gmtime().tm_hour} = KST 새벽)")
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
    engine, category = ENGINES[(idx - 1) % len(ENGINES)]

    # 최근 글들의 제목 + 훅(첫 줄)을 중복 금지 목록으로
    avoid = []
    for h in hist[-25:]:
        avoid.append(h["title"])
        if h.get("hook"):
            avoid.append(h["hook"])

    print(f"[post] #{idx}: {engine} × {category}", flush=True)

    title, parts = generate(gemini_key, engine, category, style_guide, avoid)

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
         "engine": engine, "root_id": ids[0],
         "posted_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    json.dump(state, open("state.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"[done] #{idx} posted ({len(parts)} parts). next_index={idx+1}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
