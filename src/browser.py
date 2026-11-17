"""
Playwright Browser Handler wrapper (Equivalent to browser.js)
"""
from playwright.async_api import async_playwright, Browser, Page, Playwright

class BrowserHandler:
    def __init__(self):
        self.playwright: Playwright = None
        self.browser: Browser = None
        self.page: Page = None
        self.context = None

    async def init(self, headless: bool = False):
        """Initializes the Playwright browser session."""
        self.playwright = await async_playwright().start()
        # Using channel="chrome" or just standard chromium. We'll use default chromium.
        self.browser = await self.playwright.chromium.launch(
            headless=headless,
            args=['--start-maximized']
        )
        # Create a context with a maximized viewport
        self.context = await self.browser.new_context(no_viewport=True)
        self.page = await self.context.new_page()

    async def navigate(self, url: str):
        """Navigates to a specific URL and waits for network idle."""
        # 'networkidle' in playwright is equivalent to 'networkidle2' in puppeteer
        await self.page.goto(url, wait_until='domcontentloaded')

    async def close(self):
        """Safely closes the browser session."""
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
