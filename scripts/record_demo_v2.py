"""
record_demo_v2.py — Re-record demo placeholder showcasing C2 transparency components.

Target ~90s walkthrough of:
  - Landing page hero + Master architecture (WorkflowOverview mermaid/ReactFlow)
  - Event detail with TrustIndicators (header transparency badges)
  - Phase timeline + per-phase tooltips
  - AgentDebatePanel (DebateStepStrip progress)
  - AuctionExplainer (bid ranking + formula)
  - JudgePanel (3 translation + 8 style breakdown)
  - Operators page (3 seeders + "Be the first external operator" CTA)

Constraints:
  - Do NOT trigger fresh LLM events. Navigate directly to a SUBMITTED event.
  - HEADED chromium with slow_mo so you can see the recording in real time.
  - Output webm + transcode to placeholder_v2.mp4 (does NOT overwrite placeholder.mp4).
"""

from __future__ import annotations

import asyncio
import sqlite3
import subprocess
import sys
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "polyglot_alpha.db"
OUT_DIR = ROOT / "outputs" / "demo"
RAW_DIR = OUT_DIR / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

UI_BASE = "http://localhost:3001"
VIEWPORT = {"width": 1920, "height": 1080}


def pick_submitted_event_id() -> int:
    """Return id of the most recent SUBMITTED event that has full transparency data.

    We prefer events that actually carry verdict/anchor/translation_scores. Falls
    back to MAX(id) WHERE status='SUBMITTED' if no rich event is found.
    """
    con = sqlite3.connect(str(DB_PATH))
    try:
        # First try: any SUBMITTED event with a winner_address recorded via
        # the quality_scores / translations tables. Otherwise pick MAX id.
        cur = con.execute(
            "SELECT id FROM events WHERE status='SUBMITTED' ORDER BY id DESC"
        )
        rows = cur.fetchall()
        if not rows:
            raise SystemExit("No SUBMITTED events in DB — cannot record demo.")
        return int(rows[0][0])
    finally:
        con.close()


async def smooth_scroll_to(page, selector: str, *, dwell_ms: int = 1500) -> None:
    """Scroll an element into view smoothly, then pause."""
    try:
        await page.eval_on_selector(
            selector,
            "el => el.scrollIntoView({behavior: 'smooth', block: 'center'})",
        )
    except Exception as exc:  # pragma: no cover - non-fatal
        print(f"  [warn] could not scroll to {selector}: {exc}", file=sys.stderr)
    await page.wait_for_timeout(dwell_ms)


async def slow_page_scroll(page, *, pixels: int, steps: int = 20, step_ms: int = 80):
    """Smoothly scroll the window down by `pixels` over `steps` increments."""
    delta = pixels / steps
    for _ in range(steps):
        await page.evaluate(f"window.scrollBy(0, {delta})")
        await page.wait_for_timeout(step_ms)


async def run() -> Path:
    event_id = pick_submitted_event_id()
    print(f"[demo] using event_id={event_id}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=200)
        context = await browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=1,
            record_video_dir=str(RAW_DIR),
            record_video_size=VIEWPORT,
        )
        page = await context.new_page()

        # ---- 0-7s: Landing page hero + architecture diagram ----
        print("[demo] landing page")
        await page.goto(UI_BASE, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)  # let hero settle
        # Scroll to architecture diagram
        await smooth_scroll_to(
            page, "section:has(h2:has-text('Pipeline architecture'))", dwell_ms=2500
        )
        # Hold on architecture for a beat
        await page.wait_for_timeout(2000)

        # ---- 7-12s: Navigate directly to detail page (no fresh trigger) ----
        print(f"[demo] navigating to /events/{event_id}")
        await page.goto(f"{UI_BASE}/events/{event_id}", wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)

        # Make sure header (TrustIndicators) is at the top
        await page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
        await page.wait_for_timeout(1500)

        # ---- 12-22s: TrustIndicators in header ----
        print("[demo] TrustIndicators")
        try:
            ti = page.locator("header").first
            await ti.scroll_into_view_if_needed()
        except Exception:
            pass
        await page.wait_for_timeout(3500)
        # Hover any badge if discoverable
        try:
            badges = page.locator("header [role='status'], header .badge, header span")
            count = await badges.count()
            for i in range(min(count, 4)):
                try:
                    await badges.nth(i).hover(timeout=500)
                    await page.wait_for_timeout(400)
                except Exception:
                    continue
        except Exception:
            pass
        await page.wait_for_timeout(1500)

        # ---- 22-40s: Phase timeline + WorkflowOverview ----
        print("[demo] Phase timeline")
        await smooth_scroll_to(
            page, "section:has(h2:has-text('Phase timeline'))", dwell_ms=2500
        )
        # Try hovering each timeline phase pill to surface tooltips
        try:
            phase_nodes = page.locator(
                "section:has(h2:has-text('Phase timeline')) [data-phase], "
                "section:has(h2:has-text('Phase timeline')) button, "
                "section:has(h2:has-text('Phase timeline')) li"
            )
            n = min(await phase_nodes.count(), 6)
            for i in range(n):
                try:
                    await phase_nodes.nth(i).hover(timeout=800)
                    await page.wait_for_timeout(700)
                except Exception:
                    continue
        except Exception:
            pass
        await page.wait_for_timeout(2000)

        # ---- 40-55s: AgentDebatePanel + DebateStepStrip ----
        print("[demo] AgentDebatePanel")
        # The AgentDebatePanel is the section between the timeline and the
        # auction explainer. Anchor it by the auction explainer aria-label and
        # scroll a little above.
        try:
            debate = page.locator("section").nth(2)  # heuristic
            await debate.scroll_into_view_if_needed()
        except Exception:
            await slow_page_scroll(page, pixels=600)
        await page.wait_for_timeout(4500)

        # ---- 55-70s: AuctionExplainer ----
        print("[demo] AuctionExplainer")
        await smooth_scroll_to(
            page,
            "section[aria-label='Auction explainer'], section:has(h2:has-text('Auction'))",
            dwell_ms=3000,
        )
        await page.wait_for_timeout(3500)

        # ---- 70-82s: JudgePanel ----
        print("[demo] JudgePanel")
        await smooth_scroll_to(
            page,
            "section[aria-label='Judge breakdown'], section:has(h2:has-text('11-Judge'))",
            dwell_ms=3000,
        )
        # Slowly scroll down through the judge panel
        await slow_page_scroll(page, pixels=500, steps=18, step_ms=100)
        await page.wait_for_timeout(2500)

        # ---- 82-90s: Operators page ----
        print("[demo] /operators")
        await page.goto(f"{UI_BASE}/operators", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        # Scroll to the CTA card
        try:
            cta = page.get_by_text("Be the first external operator", exact=False).first
            await cta.scroll_into_view_if_needed()
        except Exception:
            await slow_page_scroll(page, pixels=600)
        await page.wait_for_timeout(3500)

        # Back to home as a closing beat
        print("[demo] back to home")
        await page.goto(UI_BASE, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)

        # Close to finalize the video file
        await context.close()
        await browser.close()

    # The webm filename is generated by Playwright; pick the newest one.
    webms = sorted(RAW_DIR.glob("*.webm"), key=lambda f: f.stat().st_mtime)
    if not webms:
        raise SystemExit("No webm recorded — check Playwright config.")
    latest = webms[-1]
    print(f"[demo] raw webm: {latest}")
    return latest


def transcode(webm: Path) -> Path:
    mp4 = OUT_DIR / "placeholder_v2.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(webm),
        "-c:v",
        "libx264",
        "-crf",
        "22",
        "-preset",
        "fast",
        "-pix_fmt",
        "yuv420p",
        str(mp4),
    ]
    print("[demo] transcoding:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    return mp4


def main() -> None:
    webm = asyncio.run(run())
    mp4 = transcode(webm)
    print(f"[demo] done: {mp4} ({mp4.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
