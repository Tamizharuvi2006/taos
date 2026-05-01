from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright


HTML = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>TAOS Trace Assets</title>
    <style>
      :root {
        --bg: #070a0f;
        --panel: #0d1118;
        --card: #131a25;
        --border: rgba(255, 255, 255, 0.12);
        --muted: #92a0b8;
        --text: #f3f7ff;
        --accent: #37d18c;
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        padding: 40px;
        font-family: "Segoe UI", Roboto, Arial, sans-serif;
        background: radial-gradient(1200px 800px at 10% 0%, #122033 0%, var(--bg) 55%);
        color: var(--text);
      }
      .stack { display: grid; gap: 24px; max-width: 980px; margin: 0 auto; }
      .panel {
        border: 1px solid var(--border);
        border-radius: 24px;
        background: linear-gradient(180deg, rgba(255,255,255,0.05), rgba(255,255,255,0.015));
        backdrop-filter: blur(8px);
        overflow: hidden;
      }
      .head {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 20px 24px;
        border-bottom: 1px solid var(--border);
      }
      .pill {
        font-size: 12px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        padding: 7px 10px;
        border-radius: 999px;
        border: 1px solid rgba(55, 209, 140, 0.35);
        background: rgba(55, 209, 140, 0.12);
        color: #b4f5d3;
      }
      .title { font-size: 24px; margin: 0; }
      .sub { margin: 6px 0 0; color: var(--muted); font-size: 14px; }
      .grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 12px;
        padding: 20px 24px;
      }
      .metric {
        border: 1px solid var(--border);
        border-radius: 14px;
        padding: 12px;
        background: rgba(255,255,255,0.02);
      }
      .metric .k {
        color: var(--muted);
        font-size: 11px;
        letter-spacing: 0.1em;
        text-transform: uppercase;
      }
      .metric .v { margin-top: 6px; font-size: 15px; font-weight: 600; }
      .section {
        border-top: 1px solid var(--border);
        padding: 16px 24px 20px;
      }
      .section h3 {
        margin: 0 0 12px;
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: var(--muted);
      }
      .steps { display: grid; gap: 8px; }
      .step {
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 10px 12px;
        background: var(--card);
        font-size: 14px;
      }
      .trust {
        border: 1px solid var(--border);
        border-radius: 20px;
        background: linear-gradient(180deg, rgba(255,255,255,0.045), rgba(255,255,255,0.012));
        padding: 20px;
      }
      .trust h2 { margin: 0 0 14px; font-size: 20px; }
      .trust-grid {
        display: grid;
        grid-template-columns: repeat(5, minmax(0, 1fr));
        gap: 10px;
      }
      .badge {
        border: 1px solid var(--border);
        border-radius: 12px;
        background: rgba(255,255,255,0.03);
        padding: 10px;
      }
      .badge .k {
        color: var(--muted);
        font-size: 10px;
        letter-spacing: 0.1em;
        text-transform: uppercase;
      }
      .badge .v { margin-top: 6px; font-size: 14px; font-weight: 600; }
    </style>
  </head>
  <body>
    <div class="stack">
      <section class="panel" id="execution-trace-panel">
        <div class="head">
          <div>
            <h1 class="title">Execution Trace</h1>
            <p class="sub">TAOS runtime inspector with FSM, DAG batches, and freshness signals.</p>
          </div>
          <span class="pill">Freshness Passed</span>
        </div>
        <div class="grid">
          <div class="metric"><div class="k">Intent</div><div class="v">Research</div></div>
          <div class="metric"><div class="k">Mode</div><div class="v">Deep</div></div>
          <div class="metric"><div class="k">Path</div><div class="v">Structured Research (DAG)</div></div>
          <div class="metric"><div class="k">Confidence</div><div class="v">0.88</div></div>
        </div>
        <div class="section">
          <h3>FSM Flow</h3>
          <div class="steps">
            <div class="step">INIT → PLANNING → EXECUTING → REFLECTING → TERMINATING</div>
          </div>
        </div>
        <div class="section">
          <h3>DAG Frontier Batches</h3>
          <div class="steps">
            <div class="step">Batch 1: search_primary + search_secondary (parallel)</div>
            <div class="step">Batch 2: extract_primary + extract_secondary (parallel)</div>
            <div class="step">Batch 3: summarize</div>
          </div>
        </div>
      </section>

      <section class="trust" id="trust-block">
        <h2>Trust Block</h2>
        <div class="trust-grid">
          <div class="badge"><div class="k">Freshness</div><div class="v">High</div></div>
          <div class="badge"><div class="k">Evidence</div><div class="v">Strong</div></div>
          <div class="badge"><div class="k">Path</div><div class="v">Structured Research</div></div>
          <div class="badge"><div class="k">Fallback Used</div><div class="v">No</div></div>
          <div class="badge"><div class="k">Confidence</div><div class="v">High</div></div>
        </div>
      </section>
    </div>
  </body>
</html>
"""


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    out_dir = root / "docs" / "images"
    out_dir.mkdir(parents=True, exist_ok=True)

    panel_png = out_dir / "execution-trace-panel.png"
    trust_png = out_dir / "trust-block.png"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 1600})
        page.set_content(HTML, wait_until="domcontentloaded")
        page.locator("#execution-trace-panel").screenshot(path=str(panel_png))
        page.locator("#trust-block").screenshot(path=str(trust_png))
        browser.close()

    print(f"saved: {panel_png}")
    print(f"saved: {trust_png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

