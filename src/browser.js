const puppeteer = require('puppeteer');

class BrowserHandler {
    constructor() {
        this.browser = null;
        this.page = null;
    }

    /**
     * Initializes the Puppeteer browser session.
     * @param {boolean} headless - Whether to run the browser in headless mode.
     */
    async init(headless = false) {
        this.browser = await puppeteer.launch({
            headless,
            defaultViewport: null,
            args: ['--start-maximized']
        });
        const pages = await this.browser.pages();
        this.page = pages.length > 0 ? pages[0] : await this.browser.newPage();
    }

    /**
     * Navigates to a specific URL and waits for network idle.
     * @param {string} url - The destination URL.
     */
    async navigate(url) {
        await this.page.goto(url, { waitUntil: 'networkidle2' });
    }

    /**
     * Safely closes the browser session.
     */
    async close() {
        if (this.browser) {
            await this.browser.close();
            this.browser = null;
        }
    }
}

module.exports = BrowserHandler;
