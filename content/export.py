"""
Export statique des slides en PNG 1080x1920 via Playwright (headless Chromium).

Aperçu rapide d'un script, sans passer par le rendu animé : le script JSON est
converti en slides HTML, puis chaque slide est capturée.

Usage :
    python -m content.export --slides data/processed/<stem>.json
"""

import argparse
import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SLIDE_W = 1080
SLIDE_H = 1920


async def _screenshot_slides(html_files: list[Path], output_dir: Path) -> list[Path]:
    """Prend un screenshot PNG de chaque fichier HTML via Playwright."""
    from playwright.async_api import async_playwright

    png_files: list[Path] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": SLIDE_W, "height": SLIDE_H})

        for html_path in html_files:
            png_path = output_dir / (html_path.stem + ".png")
            await page.goto(f"file:///{html_path.resolve()}")
            await page.screenshot(path=str(png_path), full_page=False)
            png_files.append(png_path)
            logger.info("PNG exporté : %s", png_path.name)

        await browser.close()

    return png_files


def main() -> None:
    """Point d'entrée CLI."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s : %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Export slides -> PNG 1080x1920 via Playwright")
    parser.add_argument("--slides", required=True, help="Chemin vers le script JSON")
    args = parser.parse_args()

    path = Path(args.slides)
    if not path.exists():
        logger.error("Fichier introuvable : %s", path)
        return

    from content.slides import generate_html_slides
    html_files = generate_html_slides(path)
    output_dir = path.parent / path.stem / "png"
    output_dir.mkdir(parents=True, exist_ok=True)
    png_files = asyncio.run(_screenshot_slides(html_files, output_dir))

    print(f"{len(png_files)} images PNG exportées")
    for p in png_files:
        print(f"  {p}")


if __name__ == "__main__":
    main()
