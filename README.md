# threads-auto

@lovedemer_ 스레드 자동 포스팅 봇.

- 매시간 정각(UTC)에 GitHub Actions가 실행됩니다.
- `topics.json`의 다음 주제를 꺼내 → Gemini가 글을 작성 → Threads API로 게시.
- 진행 상황은 `state.json`에 기록됩니다. 30개 완료 후에는 아무것도 하지 않습니다.

## 중지하려면
Actions 탭 → hourly-threads-post → 우측 ⋯ → Disable workflow

## 시크릿
- `THREADS_TOKEN`: Threads 60일 장기 토큰 (만료 시 재발급 필요)
- `GEMINI_API_KEY`: Google AI Studio API 키
