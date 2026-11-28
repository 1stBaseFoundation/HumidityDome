// Scheduled trigger for HumidityDome.
//
// Cloudflare Workers cron wakes the Sprite with POST /tick and retries through a
// cold start: the Sprite proxy can return 502 for 10-30s while the service
// boots, and a single failed attempt must not drop the hour. Twelve attempts at
// 25s intervals stay well inside the 15-minute scheduled-invocation limit.
//
// Deploy from this directory:
//   npx wrangler secret put SPRITE_URL
//   npx wrangler secret put SPRITES_TOKEN
//   npx wrangler deploy

const ATTEMPTS = 12;
const DELAY_MS = 25_000;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export default {
  async scheduled(_event, env) {
    const base = String(env.SPRITE_URL).replace(/\/+$/, "");
    const url = `${base}/tick`;
    let last = "no attempt";

    for (let attempt = 1; attempt <= ATTEMPTS; attempt += 1) {
      try {
        const response = await fetch(url, {
          method: "POST",
          headers: {
            Authorization: `Bearer ${env.SPRITES_TOKEN}`,
            "Content-Type": "application/json",
          },
          body: "{}",
        });
        last = `HTTP ${response.status}`;
        if (response.ok) {
          // 200 also covers "tick too soon" and "run in progress": the tick is idempotent.
          console.log(`tick ok on attempt ${attempt}`);
          return;
        }
      } catch (error) {
        last = String(error);
      }
      console.log(`attempt ${attempt} failed (${last}), retrying`);
      await sleep(DELAY_MS);
    }

    throw new Error(`tick failed after ${ATTEMPTS} attempts: ${last}`);
  },
};
