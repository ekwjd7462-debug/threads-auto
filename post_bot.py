#!/usr/bin/env python3
"""드메르 스레드 자동 포스팅 봇.

매시간 GitHub Actions에서 실행:
1. state.json에서 다음 글 번호 확인
2. topics.json의 구조 스펙 + style_guide.md로 Gemini에게 글 작성 요청
3. 생성된 글을 Threads API로 스레드 체인 게시
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
GEMINI_MODELS = ["gemini-3.5-flash", "gemini-2.5-flash", "gemini-flash-lite-latest"]  # 503 시 순차 폴백
MAX_PART_LEN = 500

# KST 01~07시(UTC 16~22시)는 조회수가 거의 안 나오는 죽은 시간대 → 게시 안 함
DEAD_UTC_HOURS = set(range(16, 23))

# 창작 모드(31번째 글부터) 구조 로테이션 — 실측 조회수 기반 가중치.
# 비교형(평균 1,300+)·질문떡밥형(타 계정 벤치마킹 최상위)·텐션불릿형·TOP리스트형 위주.
# 단문완결형(평균 ~150)·상담인용형(평균 ~200)은 제외.
CREATIVE_SLOTS = [
    {"structure": "비교형", "category": "연애", "num_parts": 4},
    {"structure": "질문떡밥형", "category": "운세", "num_parts": 1},
    {"structure": "텐션불릿형", "category": "연애", "num_parts": 1},
    {"structure": "TOP리스트형", "category": "재물", "num_parts": 3},
    {"structure": "자리별해석형", "category": "인간관계", "num_parts": 2},
    {"structure": "질문떡밥형", "category": "재물", "num_parts": 1},
    {"structure": "비교형", "category": "직업", "num_parts": 4},
    {"structure": "일상디테일리스트형", "category": "연애", "num_parts": 1},
    {"structure": "TOP리스트형", "category": "연애", "num_parts": 3},
    {"structure": "질문떡밥형", "category": "인간관계", "num_parts": 1},
    {"structure": "비교형", "category": "재물", "num_parts": 4},
    {"structure": "자리별해석형", "category": "연애", "num_parts": 2},
    {"structure": "텐션불릿형", "category": "직업", "num_parts": 1},
    {"structure": "일상디테일리스트형", "category": "인간관계", "num_parts": 1},
    {"structure": "절기·띠저격형", "category": "운세", "num_parts": 2},
    {"structure": "일간지목형", "category": "연애", "num_parts": 3},
]

STRUCTURE_HINTS = {
    "질문떡밥형": (
        "전체 250자 미만의 아주 짧은 글. 통념 한 줄 던지고 → 상담 현장에서 본 반례/이상한 패턴 관찰 → "
        "'왜지', '이거 뭘까', '나만 그런가' 같은 미완결 질문으로 뚝 끊고 끝냄. "
        "결론·해설·처방 절대 금지. 독자가 댓글로 자기 얘기와 답을 쏟아내게 만드는 떡밥 글임. "
        "리스트 금지, 권위 문구 금지, 강의 금지."
    ),
    "일상디테일리스트형": (
        "사주 개념을 일상 장면 체크리스트로 번역한 글. 항목 5~6개, 각 항목은 반드시 구체적 생활 장면 "
        "(카톡 답장 속도, 밥 먹는 취향, 월급날 통장, 데이트 통장 눈치게임 같은 것). "
        "추상 개념어 항목은 실패작. 마지막에 '몇 개 해당됨?' 같은 참여 유도."
    ),
}


def http_json(url, method="GET", body=None, timeout=60):
    req = urllib.request.Request(url, method=method)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def gemini_generate(api_key, topic, style_guide, avoid_titles=None):
    hint = STRUCTURE_HINTS.get(topic["structure"], "")
    hint_block = f"\n- 구조 추가 지시: {hint}" if hint else ""
    if avoid_titles:
        spec = f"""## 이번 글 스펙 (새 주제 창작 모드)
- 구조 유형: {topic['structure']}
- 카테고리: {topic['category']}
- 파트 수: {topic['num_parts']}개 (반드시 정확히 이 개수){hint_block}
- 이 구조와 카테고리에 맞는 **새로운 주제**를 직접 만들어서 쓰세요.
- 아래 최근 게시글 제목들과 주제가 겹치면 절대 안 됩니다:
{chr(10).join('  - ' + t for t in avoid_titles)}

## 출력 형식
JSON 객체만 출력: {{"title": "새 주제 제목", "parts": ["파트1 본문", "파트2 본문"]}}
각 파트는 공백 포함 480자 이하."""
    else:
        spec = f"""## 이번 글 스펙
- 제목/주제: {topic['title']}
- 구조 유형: {topic['structure']}
- 파트 수: {topic['num_parts']}개 (반드시 정확히 이 개수)

## 완성 초안 (이 글의 품질 기준임)
{json.dumps(topic['reference_draft'], ensure_ascii=False, indent=1)}

## 작업 지시
위 초안의 훅, 구성, 리스트 항목, 참여 유도 문장, 줄바꿈 위치를 그대로 유지하면서
문장 표현만 20~30% 수준으로 자연스럽게 바꿔 재작성하세요. 재배열·요약·추상화 금지.
초안보다 밋밋해지면 실패작입니다.

## 출력 형식
JSON 배열만 출력. 각 원소는 파트 1개의 본문 문자열(개행문자 포함). 각 파트는 공백 포함 490자 이하.
예: ["파트1 본문", "파트2 본문"]"""

    prompt = f"""당신은 사주 명리 콘텐츠 전문 작가입니다. 아래 문체 가이드를 완벽히 체화한 뒤,
주어진 스펙과 구조로 Threads(스레드) 게시물을 작성하세요.

{style_guide}

{spec}"""

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.85,
        },
    }
    backoffs = [5, 15, 30, 45, 60, 90]
    for attempt in range(6):
        model = GEMINI_MODELS[min(attempt // 2, len(GEMINI_MODELS) - 1)]
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={api_key}")
        try:
            resp = http_json(url, "POST", body, timeout=120)
            text = resp["candidates"][0]["content"]["parts"][0]["text"]
            data = json.loads(text)
            title = topic["title"]
            if isinstance(data, dict):
                title = str(data.get("title", "")).strip() or title
                parts = data.get("parts", [])
            else:
                parts = data
            assert isinstance(parts, list)
            parts = [str(p).strip() for p in parts if str(p).strip()]
            if len(parts) != topic["num_parts"]:
                raise ValueError(f"part count {len(parts)} != {topic['num_parts']}")
            if any(len(p) > MAX_PART_LEN for p in parts):
                raise ValueError("part too long")
            if topic["structure"] != "질문떡밥형":
                parts = [ensure_linebreaks(p) for p in parts]
            return title, parts
        except Exception as e:
            print(f"[gemini] attempt {attempt+1} ({model}) failed: {e}", flush=True)
            time.sleep(backoffs[attempt])
    raise RuntimeError("gemini generation failed after 6 attempts")


def ensure_linebreaks(part):
    """줄바꿈이 부족한 파트에 문장 단위 줄바꿈을 자동 삽입."""
    if len(part) < 160 or part.count("\n") >= 2:
        return part
    lines = part.split("\n")
    out_lines = []
    for line in lines:
        sents = re.split(r"(?<=[.!?])\s+", line.strip())
        sents = [s for s in sents if s]
        # 문장 2개씩 묶어 한 덩어리로, 덩어리 사이는 빈 줄
        groups = ["\n".join(sents[i:i+2]) for i in range(0, len(sents), 2)]
        out_lines.append("\n\n".join(groups))
    fixed = "\n".join(out_lines)
    return fixed if len(fixed) <= MAX_PART_LEN else part


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

    # 죽은 시간대(KST 새벽 01~07시) 게시 금지 — 도달이 낮아 계정 평균 조회수만 깎아먹음
    if time.gmtime().tm_hour in DEAD_UTC_HOURS:
        print(f"skip: dead hour (UTC {time.gmtime().tm_hour} = KST 새벽)")
        return 0

    state = json.load(open("state.json", encoding="utf-8"))
    topics = json.load(open("topics.json", encoding="utf-8"))
    style_guide = open("style_guide.md", encoding="utf-8").read()

    # 중복 방지: 마지막 게시 후 45분 이내면 건너뜀 (GitHub cron + 외부 트리거 이중화 대비)
    hist = state.get("history", [])
    if hist:
        last_ts = time.mktime(time.strptime(hist[-1]["posted_at_utc"], "%Y-%m-%dT%H:%M:%SZ"))
        if time.time() - last_ts < 45 * 60:
            print(f"skip: last post {int((time.time()-last_ts)//60)} min ago (<45)")
            return 0

    idx = state["next_index"]

    avoid_titles = None
    if idx > len(topics):
        # 창작 모드: 실측 조회수 기반 가중 로테이션으로 구조 선택, 최근 40개 제목과 중복 금지
        slot = CREATIVE_SLOTS[(idx - len(topics) - 1) % len(CREATIVE_SLOTS)]
        topic = dict(slot, index=idx, title=f"{slot['category']} 신규 주제")
        avoid_titles = [h["title"] for h in state.get("history", [])[-40:]]
    else:
        topic = next(t for t in topics if t["index"] == idx)

    print(f"[post] #{idx}: {topic['title']} ({topic['structure']}, "
          f"{topic['num_parts']} parts, creative={bool(avoid_titles)})", flush=True)

    title, parts = gemini_generate(gemini_key, topic, style_guide, avoid_titles)

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
        {"index": idx, "title": title, "root_id": ids[0],
         "posted_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    json.dump(state, open("state.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"[done] #{idx} posted. next_index={idx+1}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
