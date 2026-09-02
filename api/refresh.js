// Manual refresh: triggers the "Model run" GitHub Action (workflow_dispatch).
// GET  -> current state of the latest run
// POST -> start a forced run, unless one is already queued/in progress
const REPO = "your-github-user/your-repo";
const WORKFLOW = "run.yml";

module.exports = async (req, res) => {
  const token = process.env.GITHUB_DISPATCH_TOKEN;
  if (!token) return res.status(500).json({ status: "not_configured" });
  const headers = {
    Authorization: `Bearer ${token}`,
    Accept: "application/vnd.github+json",
    "User-Agent": "mudchute-dashboard",
  };
  const base = `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}`;

  const runsResp = await fetch(`${base}/runs?per_page=1`, { headers });
  if (!runsResp.ok) return res.status(502).json({ status: "github_error", code: runsResp.status });
  const latest = (await runsResp.json()).workflow_runs?.[0];
  const busy = latest && (latest.status === "in_progress" || latest.status === "queued");

  if (req.method !== "POST") {
    return res.json({
      status: busy ? "running" : "idle",
      last_run: latest ? { started: latest.run_started_at, conclusion: latest.conclusion } : null,
    });
  }
  if (busy) return res.json({ status: "already_running" });
  const dispatch = await fetch(`${base}/dispatches`, {
    method: "POST", headers,
    body: JSON.stringify({ ref: "main", inputs: { force: "true" } }),
  });
  if (dispatch.status === 204) return res.json({ status: "started" });
  return res.status(502).json({ status: "dispatch_failed", code: dispatch.status });
};
