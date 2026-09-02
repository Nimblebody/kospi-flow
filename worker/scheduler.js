// GitHub 예약이 못 미더워 대신 정시에 워크플로를 때리는 Cloudflare Worker
//
// 두 가지 입구가 있다.
//   1) 크론      — 매 영업일 16:30 KST 에 스스로 발사
//   2) HTTP GET  — 앱(PWA)의 '지금 갱신' 버튼이 부른다
//
// 둘 다 같은 함수를 타고, 그 안에서 '최근에 이미 돌았는지' 를 먼저 확인한다.
// 저장소(KV)를 쓰지 않고 GitHub 실행 기록을 그대로 보기 때문에, 크론과 앱이
// 같은 시각에 겹쳐도 두 번 돌지 않는다.

const REPO = "Nimblebody/kospi-flow";
const WORKFLOW = "daily.yml";
const REF = "main";

// 연타 방지용 최소 간격. '이미 끝났나' 는 아래 alreadyDoneToday 가 따로 보므로
// 여기서 길게 잡을 이유가 없다.
//
// 처음에 30분으로 뒀다가 실제로 발등을 찍었다. 15:34 실행이 확정 수급이 아직
// 없어 리포트 없이 끝났는데, 곧바로 다시 부르니 '최근에 실행됨' 으로 막혔다.
// 헛돈 실행 때문에 진짜 필요한 재시도가 막히면 안 된다.
const MIN_GAP_MIN = 10;

// 앱이 부를 수 있는 출처. 공개 페이지라 비밀이랄 게 없지만,
// 아무 데서나 부르는 건 막는다.
const ALLOW_ORIGIN = "https://nimblebody.github.io";

function cors(origin) {
  return {
    "Access-Control-Allow-Origin": origin === ALLOW_ORIGIN ? origin : ALLOW_ORIGIN,
    "Access-Control-Allow-Methods": "GET,OPTIONS",
    "Access-Control-Allow-Headers": "content-type",
    "Cache-Control": "no-store",
  };
}

// GitHub API 는 User-Agent 가 없으면 403 을 준다. Worker 의 fetch 는 기본값이 없다.
function gh(path, token, init = {}) {
  return fetch(`https://api.github.com${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "kospi-flow-scheduler",
      ...(init.headers || {}),
    },
  });
}

async function recentlyRan(token) {
  const res = await gh(
    `/repos/${REPO}/actions/workflows/${WORKFLOW}/runs?per_page=5`,
    token,
  );
  // 확인에 실패하면 막지 않는다. 못 도는 것보다 한 번 더 도는 편이 낫다.
  if (!res.ok) return false;

  const { workflow_runs = [] } = await res.json();
  const now = Date.now();
  return workflow_runs.some((run) => {
    // 지금 돌고 있으면 겹쳐 돌리지 않는다. 이건 시간과 무관하게 항상 막는다.
    if (run.status === "queued" || run.status === "in_progress") return true;
    return now - Date.parse(run.created_at) < MIN_GAP_MIN * 60_000;
  });
}

// 저장소에 커밋된 최신 리포트. 재시도 크론이 헛돌지 않게 이걸 먼저 본다.
// raw 는 CDN 캐시가 몇 분 끼지만, 재시도 간격이 한 시간이라 문제되지 않는다.
const LATEST_JSON =
  `https://raw.githubusercontent.com/${REPO}/${REF}/web/data/latest.json`;

function todayKST() {
  // KST = UTC+9. 리포트의 date 필드와 같은 YYYY-MM-DD 모양으로 맞춘다.
  return new Date(Date.now() + 9 * 3600 * 1000).toISOString().slice(0, 10);
}

async function alreadyDoneToday() {
  try {
    const res = await fetch(`${LATEST_JSON}?t=${Date.now()}`, {
      headers: { "User-Agent": "kospi-flow-scheduler" },
      cf: { cacheTtl: 0 },
    });
    if (!res.ok) return false;
    const { date } = await res.json();
    return date === todayKST();
  } catch {
    // 확인 못 하면 막지 않는다. 못 도는 것보다 한 번 더 도는 편이 낫다.
    return false;
  }
}

async function trigger(env, { stage = "final", date = "", force = false } = {}) {
  const token = env.GITHUB_TOKEN;
  if (!token) return { ok: false, status: 500, message: "GITHUB_TOKEN 이 없습니다" };

  if (!force) {
    if (await alreadyDoneToday()) {
      return { ok: true, skipped: true, message: `오늘(${todayKST()}) 리포트가 이미 있습니다` };
    }
    if (await recentlyRan(token)) {
      return { ok: true, skipped: true, message: `최근 ${MIN_GAP_MIN}분 안에 이미 실행됐습니다` };
    }
  }

  const res = await gh(
    `/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
    token,
    { method: "POST", body: JSON.stringify({ ref: REF, inputs: { stage, date } }) },
  );

  // 성공은 204 No Content 다. 본문이 없다.
  if (res.status === 204) return { ok: true, skipped: false, message: "실행을 요청했습니다" };

  const body = await res.text();
  return {
    ok: false,
    status: res.status,
    message: `GitHub 응답 ${res.status}: ${body.slice(0, 200)}`,
  };
}

export default {
  // 크론. Cloudflare 도 UTC 기준이다.
  //   30 7  * * *  = 16:30 KST  본 실행
  //   0  9  * * *  = 18:00 KST  1차 재시도
  //   0  11 * * *  = 20:00 KST  2차 재시도
  //
  // 요일 조건(1-5)을 일부러 넣지 않는다. 표기가 한 칸 밀리면 금요일을 통째로
  // 놓치는데, 그건 주말에 헛도는 것보다 훨씬 나쁘다. 휴장 판단은 KIS 의
  // chk-holiday 가 한다(주말·대체공휴일·임시공휴일 전부 알고 있다).
  // 휴장일이면 파이프라인이 수집 전에 40초 만에 끝난다.
  //
  // 재시도는 오늘 리포트가 없을 때만 실제로 돈다. 있으면 위에서 걸러진다.
  async scheduled(event, env, ctx) {
    ctx.waitUntil(
      trigger(env).then((r) =>
        console.log(`cron ${event.cron}:`, JSON.stringify(r)),
      ),
    );
  },

  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    const head = cors(origin);

    if (request.method === "OPTIONS") return new Response(null, { headers: head });
    if (request.method !== "GET") {
      return new Response("GET 만 받습니다", { status: 405, headers: head });
    }

    const url = new URL(request.url);

    // 상태 확인용. 토큰 없이도 살아있는지 볼 수 있다.
    if (url.pathname === "/health") {
      return Response.json({ ok: true, repo: REPO, workflow: WORKFLOW }, { headers: head });
    }

    // 열쇠를 설정해 뒀으면 맞아야 한다. 앱에 박아 두는 값이라 비밀은 아니지만,
    // 지나가는 스캐너가 눌러 보는 건 걸러낸다.
    if (env.TRIGGER_KEY && url.searchParams.get("key") !== env.TRIGGER_KEY) {
      return Response.json({ ok: false, message: "key 가 맞지 않습니다" }, { status: 403, headers: head });
    }

    const result = await trigger(env, {
      stage: url.searchParams.get("stage") || "final",
      date: url.searchParams.get("date") || "",
    });
    return Response.json(result, { status: result.ok ? 200 : (result.status || 500), headers: head });
  },
};
