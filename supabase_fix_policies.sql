-- 실행 필요: Supabase 대시보드 → SQL Editor 에 붙여넣고 Run
--
-- 증상: anon 키로 보낸 UPDATE 가 오류 없이 0행만 바꿈(무시됨).
--  1) 완료 페이지에서 participants.completed 가 true 로 안 바뀜
--  2) 참가자가 "이전 세트"로 돌아가 답을 고쳐도 서버에는 원래 답이 남음  ← 데이터 문제
--
-- 원인: responses/participants 에 anon 용 UPDATE 정책이 적용되어 있지 않음.
--       (RLS 가 대상 행을 걸러내면 PostgREST 는 오류 없이 204 를 반환함)

drop policy if exists "anon complete participants" on public.participants;
drop policy if exists "anon update participants"   on public.participants;
drop policy if exists "anon upsert responses"      on public.responses;
drop policy if exists "anon update responses"      on public.responses;

create policy "anon update participants" on public.participants
    for update to anon using (true) with check (true);

create policy "anon update responses" on public.responses
    for update to anon using (true) with check (true);

-- 참고: SELECT 정책은 일부러 만들지 않습니다. anon 키로는 읽기가 불가능해야 하고,
-- 분석은 service_role 키(supabase_secrets.env)로만 수행합니다.
