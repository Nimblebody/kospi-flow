// Cloudflare Worker(worker/scheduler.js)의 크론 배정·중복 방지·입력을 가짜 GitHub 로 검증한다
// 실행: node tests/test_worker.mjs
import { readFileSync } from "fs";

const src = readFileSync(new URL("../worker/scheduler.js", import.meta.url), "utf8");
const body = src.replace(/export default \{/, "const __worker = {") +
  "\nglobalThis.__t = { jobsFor, JOBS, CRON_JOBS, trigger, worker: __worker };\n";
await import("data:text/javascript;base64," + Buffer.from(body).toString("base64"));
const { jobsFor, JOBS, CRON_JOBS, trigger, worker } = globalThis.__t;

let fail = 0;
const ck = (label, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) fail++;
  console.log(`  ${ok ? "ok  " : "FAIL"} ${label}${ok ? "" : `: ${JSON.stringify(got)} (기대 ${JSON.stringify(want)})`}`);
};

const today = new Date(Date.now() + 9 * 3600 * 1000).toISOString().slice(0, 10);
const yesterday = new Date(Date.now() + 9 * 3600 * 1000 - 86400000).toISOString().slice(0, 10);

// 가짜 GitHub. files 는 raw.githubusercontent 가 돌려줄 파일, running 은 도는 중인 실행.
function fakeGitHub({ files = {}, running = [] } = {}) {
  const dispatched = [];
  globalThis.fetch = async (url, init) => {
    const u = String(url);
    if (u.includes("raw.githubusercontent")) {
      const path = Object.keys(files).find((p) => u.includes(p));
      return path ? { ok: true, json: async () => files[path] } : { ok: false, status: 404 };
    }
    if (u.includes("/runs?")) {
      const wf = u.match(/workflows\/([^/]+)\/runs/)[1];
      const runs = running.filter((r) => r === wf)
        .map(() => ({ status: "in_progress", created_at: new Date().toISOString() }));
      return { ok: true, json: async () => ({ workflow_runs: runs }) };
    }
    if (u.includes("/dispatches")) {
      const wf = u.match(/workflows\/([^/]+)\/dispatches/)[1];
      dispatched.push({ wf, ...JSON.parse(init.body).inputs });
      return { status: 204 };
    }
    return { ok: false, status: 404, text: async () => "" };
  };
  return dispatched;
}
const env = { GITHUB_TOKEN: "t" };

console.log("=== 크론 배정 ===");
ck("16:30 → 리포트", jobsFor("30 7 * * *").map((j) => j.label), ["수급 리포트"]);
ck("20:30 → 확정 갱신 + 조선 저녁", jobsFor("30 11 * * *").map((j) => j.label),
   ["수급 리포트 확정 갱신", "조선 저녁"]);
ck("01:30 → 뉴스", jobsFor("30 16 * * *").map((j) => j.workflow), ["news.yml"]);
ck("08:00 → 조선 아침", jobsFor("0 23 * * *").map((j) => j.label), ["조선 아침"]);
ck("옛 20:00 크론은 아무것도 안 부른다", jobsFor("0 11 * * *"), []);
ck("크론 5개 (무료 한도)", Object.keys(CRON_JOBS).length, 5);

console.log("\n=== 워크플로별 입력 (없는 입력을 보내면 422) ===");
ck("리포트", JOBS.report.inputs({ stage: "final", date: "" }), { stage: "final", date: "" });
ck("뉴스", JOBS.news.inputs({ stage: "final", date: "" }), { date: "" });
ck("조선 아침", JOBS.ship_morning.inputs({ stage: "final", date: "" }), { slot: "morning" });
ck("조선 저녁", JOBS.ship_evening.inputs({ stage: "final", date: "" }), { slot: "evening" });

console.log("\n=== 이미 끝났는지 ===");
let sent = fakeGitHub({ files: { "ship.json": { morning: { date: today } } } });
let r = await trigger(env, { job: JOBS.ship_morning });
ck("조선 아침: 오늘 것 있으면 건너뜀", r.skipped, true);
r = await trigger(env, { job: JOBS.ship_evening });
ck("조선 저녁: 아침만 있으면 실행", r.skipped, false);
ck("  → ship.yml 에 slot=evening", sent, [{ wf: "ship.yml", slot: "evening" }]);

sent = fakeGitHub({ files: { "news-latest.json": { date: yesterday } } });
r = await trigger(env, { job: JOBS.news });
ck("뉴스: 기준일은 어제", r.skipped, true);

console.log("\n=== 20:30 크론 한 번에 두 작업 ===");
sent = fakeGitHub({ files: { "latest.json": { date: today }, "ship.json": {} } });
const waits = [];
await worker.scheduled({ cron: "30 11 * * *" }, env, { waitUntil: (p) => waits.push(p) });
await Promise.all(waits);
ck("둘 다 dispatch", sent.map((d) => d.wf).sort(), ["daily.yml", "ship.yml"]);

console.log("\n=== 겹쳐 돌지 않기 ===");
sent = fakeGitHub({ files: { "ship.json": {} }, running: ["ship.yml"] });
r = await trigger(env, { job: JOBS.ship_evening });
ck("ship.yml 이 도는 중이면 건너뜀", r.skipped, true);
r = await trigger(env, { job: JOBS.refresh });
ck("다른 워크플로(daily.yml)는 막지 않음", r.skipped, false);

console.log("\n=== 모르는 크론 ===");
sent = fakeGitHub();
const warned = [];
const origWarn = console.warn;
console.warn = (m) => warned.push(m);
await worker.scheduled({ cron: "0 11 * * *" }, env, { waitUntil: () => {} });
console.warn = origWarn;
ck("아무것도 dispatch 안 함", sent, []);
ck("경고를 남김", warned.length, 1);

console.log(fail ? `\n실패 ${fail}건` : "\n전부 통과");
process.exit(fail ? 1 : 0);
