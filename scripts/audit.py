#!/usr/bin/env python3
"""
frontend-design-audit Playwright capture script.
Usage: python3 audit.py <url>
Outputs: .design-audit/design-audit-data.json, .design-audit/design-audit-*.png
"""
import asyncio, json, os, sys
from playwright.async_api import async_playwright

URL = sys.argv[1]
OUT_DIR = os.path.join(os.getcwd(), ".design-audit")
os.makedirs(OUT_DIR, exist_ok=True)
OUT = os.path.join(OUT_DIR, "design-audit")

AXE_CDN = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.9.1/axe.min.js"
BREAKPOINTS = [
    (375, 812, "mobile"),
    (768, 1024, "tablet"),
    (1024, 768, "tablet-land"),
    (1280, 800, "desktop"),
    (1440, 900, "desktop-xl"),
]
VISION_DEFICIENCIES = ["protanopia", "deuteranopia", "achromatopsia"]
MAX_INTERACTIONS = 12
TAB_STEPS = 30


async def screenshot(page, label):
    path = f"{OUT}-{label}.png"
    await page.screenshot(path=path, full_page=True)
    return path


async def a11y(page):
    """Collect ARIA state of all interactive/role elements via JS."""
    return await page.evaluate("""() => {
        const sels = [
            '[role]','[aria-label]','[aria-selected]','[aria-expanded]',
            '[aria-current]','[aria-checked]','[aria-live]','[aria-controls]',
            'button','a[href]','input','select','[tabindex]',
        ];
        const seen = new WeakSet();
        const results = [];
        sels.forEach(s => {
            document.querySelectorAll(s).forEach(el => {
                if (seen.has(el)) return;
                seen.add(el);
                const attrs = {};
                ['role','aria-label','aria-selected','aria-expanded',
                 'aria-current','aria-checked','aria-hidden','aria-live',
                 'aria-controls','aria-describedby','aria-invalid',
                 'type','id','tabindex'].forEach(a => {
                    const v = el.getAttribute(a);
                    if (v !== null) attrs[a] = v;
                });
                results.push({
                    tag: el.tagName.toLowerCase(),
                    text: el.textContent?.trim().slice(0, 60) || '',
                    ...attrs,
                });
            });
        });
        return results;
    }""")



async def run():
    results = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox"])

        # ── Pass 1: Baseline + axe-core ──────────────────────────────
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        console_errors = []
        page.on(
            "console",
            lambda m: console_errors.append(m.text) if m.type == "error" else None,
        )
        await page.goto(URL, wait_until="networkidle", timeout=30000)

        results["baseline_screenshot"] = await screenshot(page, "00-baseline-desktop")
        results["baseline_a11y"] = await a11y(page)

        await page.add_script_tag(url=AXE_CDN)
        results["axe_violations"] = await page.evaluate(
            "() => axe.run().then(r => r.violations)"
        )

        # ── Pass 2: Interactive element walkthrough ───────────────────
        interactive = await page.evaluate(
            f"""() => {{
            const sels = [
                'input[type="checkbox"]', 'input[type="radio"]',
                '[role="tab"]', '[data-bs-toggle="collapse"]',
                'button:not([disabled])', 'select',
            ];
            return sels.flatMap(s =>
                [...document.querySelectorAll(s)].map((el, i) => ({{
                    sel: s,
                    nth: i,
                    label: (el.textContent?.trim().slice(0, 40)
                            || el.getAttribute('aria-label')
                            || el.id
                            || (s + '-' + i)).replace(/[^a-z0-9-]/gi, '-'),
                }}))
            ).slice(0, {MAX_INTERACTIONS});
        }}"""
        )

        interactions = []
        for item in interactive:
            try:
                before_path = await screenshot(page, f"before-{item['label']}")
                before_a11y = await a11y(page)
                locator = page.locator(item["sel"]).nth(item["nth"])
                await locator.click(timeout=3000)
                await page.wait_for_timeout(400)
                after_path = await screenshot(page, f"after-{item['label']}")
                after_a11y = await a11y(page)
                interactions.append(
                    {
                        "label": item["label"],
                        "before": before_path,
                        "after": after_path,
                        "a11y_before": before_a11y,
                        "a11y_after": after_a11y,
                    }
                )
            except Exception as e:
                interactions.append({"label": item["label"], "error": str(e)})
        results["interactions"] = interactions

        # ── Pass 3: Keyboard Tab traversal ───────────────────────────
        await page.goto(URL, wait_until="networkidle", timeout=30000)
        focus_order = []
        for i in range(TAB_STEPS):
            await page.keyboard.press("Tab")
            info = await page.evaluate(
                """() => {
                const el = document.activeElement;
                if (!el || el === document.body) return null;
                const cs = getComputedStyle(el);
                const bb = el.getBoundingClientRect();
                return {
                    tag: el.tagName,
                    role: el.getAttribute('role'),
                    label: el.getAttribute('aria-label') || el.textContent?.trim().slice(0, 40),
                    outline: cs.outline,
                    outlineColor: cs.outlineColor,
                    width: Math.round(bb.width),
                    height: Math.round(bb.height),
                };
            }"""
            )
            if info:
                info["screenshot"] = await screenshot(page, f"focus-{i:02d}")
                focus_order.append(info)
        results["focus_order"] = focus_order

        # ── Pass 4: Computed styles + touch targets ───────────────────
        styles_and_targets = await page.evaluate(
            """() => {
            const styleSels = ['h1','h2','h3','p','a','button','[role="tab"]','label','th','td'];
            const styles = {};
            styleSels.forEach(s => {
                const el = document.querySelector(s);
                if (!el) return;
                const cs = getComputedStyle(el);
                const bg = cs.backgroundColor !== 'rgba(0, 0, 0, 0)'
                           ? cs.backgroundColor
                           : getComputedStyle(document.body).backgroundColor;
                styles[s] = {
                    color: cs.color, background: bg,
                    fontSize: cs.fontSize, fontWeight: cs.fontWeight,
                    lineHeight: cs.lineHeight,
                };
            });

            const interactiveSels =
                'a,button,input,select,textarea,[role="button"],[role="tab"],[role="checkbox"]';
            const targets = [...document.querySelectorAll(interactiveSels)].map(el => {
                const bb = el.getBoundingClientRect();
                return {
                    label: el.textContent?.trim().slice(0, 40)
                           || el.getAttribute('aria-label') || el.tagName,
                    width: Math.round(bb.width),
                    height: Math.round(bb.height),
                    below_minimum: bb.width < 44 || bb.height < 44,
                };
            }).filter(t => t.below_minimum);

            return { styles, touch_targets: targets };
        }"""
        )
        results["computed_styles"] = styles_and_targets["styles"]
        results["touch_targets"] = styles_and_targets["touch_targets"]

        # ── Pass 5: Responsive breakpoints (reuse one page, resize) ─────
        breakpoints = {}
        bp_page = await browser.new_page(viewport={"width": 1280, "height": 800})
        for w, h, label in BREAKPOINTS:
            await bp_page.set_viewport_size({"width": w, "height": h})
            await bp_page.goto(URL, wait_until="networkidle", timeout=30000)
            breakpoints[label] = await screenshot(bp_page, f"bp-{label}")
        await bp_page.close()
        results["breakpoint_screenshots"] = breakpoints

        # ── Pass 6: Hover and focus state capture ────────────────────
        hover_page = await browser.new_page(viewport={"width": 1280, "height": 800})
        await hover_page.goto(URL, wait_until="networkidle", timeout=30000)
        hover_shots = {}
        focus_shots = {}
        buttons = await hover_page.locator("button, a.btn").all()
        for i, btn in enumerate(buttons[:8]):
            try:
                label = (await btn.text_content() or f"btn-{i}").strip()[:20].replace(
                    " ", "-"
                )
                await btn.hover()
                await hover_page.wait_for_timeout(150)
                hover_shots[label] = await screenshot(hover_page, f"hover-{label}")
                await btn.focus()
                await hover_page.wait_for_timeout(150)
                focus_shots[label] = await screenshot(hover_page, f"focused-{label}")
            except Exception:
                pass
        results["hover_screenshots"] = hover_shots
        results["focus_screenshots"] = focus_shots
        await hover_page.close()

        # ── Pass 7: Color blindness simulations (reuse one page) ─────
        colorblind = {}
        cb_page = await browser.new_page(viewport={"width": 1280, "height": 800})
        cdp = await cb_page.context.new_cdp_session(cb_page)
        for deficiency in VISION_DEFICIENCIES:
            await cdp.send("Emulation.setEmulatedVisionDeficiency", {"type": "none"})
            await cb_page.goto(URL, wait_until="networkidle", timeout=30000)
            await cdp.send(
                "Emulation.setEmulatedVisionDeficiency", {"type": deficiency}
            )
            await cb_page.wait_for_timeout(100)
            colorblind[deficiency] = await screenshot(
                cb_page, f"colorblind-{deficiency}"
            )
        await cb_page.close()
        results["colorblind_screenshots"] = colorblind
        results["console_errors"] = console_errors

        await browser.close()

    with open(f"{OUT}-data.json", "w") as f:
        json.dump(results, f, indent=2)

    print(
        json.dumps(
            {
                "axe_violations": len(results.get("axe_violations", [])),
                "interactions": len(results.get("interactions", [])),
                "focus_steps": len(results.get("focus_order", [])),
                "touch_target_violations": len(results.get("touch_targets", [])),
                "breakpoints": list(results.get("breakpoint_screenshots", {}).keys()),
                "colorblind_modes": list(
                    results.get("colorblind_screenshots", {}).keys()
                ),
                "console_errors": results.get("console_errors", []),
            },
            indent=2,
        )
    )


asyncio.run(run())
