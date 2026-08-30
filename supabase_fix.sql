-- 실행 필요: Supabase 대시보드 → SQL Editor 에 붙여넣고 Run
-- (앞서 드린 supabase_fix_policies.sql 은 효과가 없어 폐기했습니다. 이 파일로 대체합니다.)
--
-- 문제: anon 키로 보낸 UPDATE 가 오류 없이 0행만 바꿔서, 참가자가 "이전 세트"로
--       돌아가 답을 고쳐도 서버에는 원래 답이 남습니다.
-- 원인: PostgreSQL RLS 규칙상 WHERE 절이 있는 UPDATE 는 SELECT 정책도 필요합니다.
--       그런데 anon 에게 읽기를 열어주면 누구나 전체 응답을 조회할 수 있어 부적절합니다.
-- 해결: UPDATE 를 아예 쓰지 않고 항상 INSERT 만 하도록 바꿉니다(append-only).
--       수정된 답은 새 행으로 쌓이고, analyze.py 가 가장 마지막 행만 사용합니다.
--       그러려면 아래 unique 제약을 없애야 합니다.

alter table public.responses
    drop constraint if exists responses_participant_id_set_id_file_key_key;

-- 쓸모없어진 UPDATE 정책 정리 (읽기 정책은 계속 만들지 않습니다)
drop policy if exists "anon complete participants" on public.participants;
drop policy if exists "anon update participants"   on public.participants;
drop policy if exists "anon upsert responses"      on public.responses;
drop policy if exists "anon update responses"      on public.responses;
