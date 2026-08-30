// ── Supabase 설정 ─────────────────────────────────────────────
// 새 Supabase 프로젝트 생성 후 아래 두 값을 채우세요.
// (Project Settings → API → Project URL / anon public key)
// 비어 있으면 응답은 localStorage에만 저장되고 완료 페이지에서 JSON 다운로드로 백업됩니다.
const SUPABASE_URL = "";
const SUPABASE_ANON_KEY = "";

// ── 설문 질문 (7점 척도) ──────────────────────────────────────
const QUESTIONS = [
    {
        key: "q1",
        title: "Q1. 의도 관련성",
        text: "이 하이라이트에 포함된 장면들이 주어진 의도와 얼마나 관련이 있었나요?",
        low: "1: 의도와 무관한 장면이 많았다",
        mid: "4: 보통이다",
        high: "7: 모든 장면이 의도와 관련 있었다",
    },
    {
        key: "q2",
        title: "Q2. 편집 자연스러움",
        text: "하이라이트 전체가 하나의 자연스러운 편집처럼 느껴졌나요?",
        note: "(자연스럽지 않은 경우: 장면이 뚝뚝 끊기거나 잘린 느낌이 든다, 각 장면의 맥락을 파악하기 힘들다)",
        low: "1: 전혀 자연스럽지 않다",
        mid: "4: 보통이다",
        high: "7: 매우 자연스럽다",
    },
    {
        key: "q3",
        title: "Q3. 전반적 만족도",
        text: "이 하이라이트의 전반적인 시청 경험에 얼마나 만족하시나요?",
        low: "1: 전혀 만족스럽지 않다",
        mid: "4: 보통이다",
        high: "7: 매우 만족스럽다",
    },
];

const supabaseClient = (SUPABASE_URL && SUPABASE_ANON_KEY && window.supabase)
    ? window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY)
    : null;
