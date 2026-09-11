import sys, asyncio
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        for name in sys.argv[1:]:
            pg = await b.new_page(viewport={'width':1440,'height':960})
            await pg.goto(f'file:///home/claude/fleet-design/{name}.dc.html')
            await pg.wait_for_timeout(800)
            await pg.screenshot(path=f'shots/{name}.png', full_page=True)
            h = await pg.evaluate('document.body.scrollHeight')
            print(name, 'height', h)
        await b.close()
asyncio.run(main())
