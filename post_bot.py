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
GEMINI_MODEL = "gemini-2.5-flash"
MAX_PART_LEN = 500


def http_json(url, method="GET", body=None, timeout=60):
    req = urllib.request.Request(url, method=method)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def gemini_generate(api_key, topic, style_guide, avoid_titles=None):
    if avoid_titles:
        spec = f"""## 이번 글 스펙 (새 주제 창작 모드)
- 구조 유형: {topic['structure']}
- 카테고리: {topic['category']}
- 파트 수: {topic['num_parts']}개 (반드시 정확히 이 개수)
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
- 카테고리: {topic['category']}
- 파트 수: {topic['num_parts']}개 (반드시 정확히 이 개수)
- 논지/훅 힌트: {topic['angle']}

## 출력 형식
JSON 배열만 출력. 각 원소는 파트 1개의 본문 문자열. 각 파트는 공백 포함 480자 이하.
예: ["파트1 본문", "파트2 본문"]"""

    prompt = f"""당신은 사주 명리 콘텐츠 전문 작가입니다. 아래 문체 가이드를 완벽히 체화한 뒤,
주어진 스펙과 구조로 Threads(스레드) 게시물을 작성하세요.

{style_guide}

{spec}"""

    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={api_key}")
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.9,
        },
    }
    for attempt in range(3):
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
            parts = [ensure_linebreaks(p) for p in parts]
            return title, parts
        except Exception as e:
            print(f"[gemini] attempt {attempt+1} failed: {e}", flush=True)
            time.sleep(5)
    raise RuntimeError("gemini generation failed after 3 attempts")


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

    state = json.load(open("state.json", encoding="utf-8"))
    topics = json.load(open("topics.json", encoding="utf-8"))
    style_guide = open("style_guide.md", encoding="utf-8").read()

    idx = state["next_index"]
    template_idx = (idx - 1) % len(topics) + 1
    topic = next(t for t in topics if t["index"] == template_idx)

    avoid_titles = None
    if idx > len(topics):
        # 창작 모드: 같은 구조/카테고리로 새 주제 생성, 최근 40개 제목과 중복 금지
        avoid_titles = [h["title"] for h in state.get("history", [])[-40:]]

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
