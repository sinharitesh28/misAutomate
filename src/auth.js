const { getOTP } = require('./gmail');

/**
 * Automates the login flow and OTP retrieval.
 * Returns the authenticated browser context to be used by subsequent tasks.
 * 
 * @param {import('./browser')} browserHandler - The established browser session.
 * @returns {Promise<{page: any, browserHandler: any}>} Resolves with the authenticated page.
 */
async function startSession(browserHandler) {
    const page = browserHandler.page;
    const { PORTAL_USER, PORTAL_PASS } = process.env;

    if (!PORTAL_USER || !PORTAL_PASS) {
        throw new Error('Missing PORTAL_USER or PORTAL_PASS in .env file.');
    }

    console.log('Navigating to MIS Portal...');
    await browserHandler.navigate('https://ums.paruluniversity.ac.in/Login.aspx');

    console.log('Filling in credentials...');
    // IMPORTANT: Check the exact selectors for the portal inputs
    const userSelector = 'input[type="text"]:not([readonly]), #txtEnrollmentNo, #txtUsername, [name*="user"]';
    const passSelector = 'input[type="password"]';

    await page.waitForSelector(userSelector, { visible: true });

    // Evaluate multiple likely inputs because exact ASP.NET IDs vary
    // We will find the first visible text input and password input
    const textInputs = await page.$$(userSelector);
    if (textInputs.length > 0) {
        await textInputs[0].type(PORTAL_USER);
    } else {
        throw new Error('Could not find username input field on the portal.');
    }

    const passInputs = await page.$$(passSelector);
    if (passInputs.length > 0) {
        await passInputs[0].type(PORTAL_PASS);
    } else {
        throw new Error('Could not find password input field on the portal.');
    }

    console.log('Submitting login form...');
    const loginButtonSelector = 'input[type="submit"], button[type="submit"], #btnLogin, #btnSubmit, .login-btn';

    await Promise.all([
        page.waitForNavigation({ waitUntil: 'networkidle2' }),
        page.click(loginButtonSelector)
    ]);

    // Check if OTP authentication is required
    console.log('Checking for OTP prompt...');
    const otpInputSelector = 'input[placeholder*="OTP"], input[id*="OTP"], input[name*="OTP"]';
    try {
        await page.waitForSelector(otpInputSelector, { timeout: 8000 });
        console.log('OTP verification prompt detected! Fetching OTP from email...');

        let otp;
        try {
            otp = await getOTP();
        } catch (otpErr) {
            console.error('Failed to retrieve OTP via IMAP: ', otpErr.message);
            throw otpErr;
        }

        console.log(`OTP Successfully retrieved: ${otp}. Submitting to portal...`);
        await page.type(otpInputSelector, otp);

        // Find generic Verify button near the OTP field
        const otpVerifyBtnXpath = "::-p-xpath(//input[contains(translate(@value, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'verify')] | //button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'verify')])";
        const verifyBtn = await page.$(otpVerifyBtnXpath);

        if (verifyBtn) {
            await verifyBtn.click();
            console.log('OTP verify button clicked.');
        } else {
            console.log('Could not find an obvious OTP Verify button, pressing Enter and hoping for the best...');
            await page.keyboard.press('Enter');
        }

        console.log('Waiting for login to complete...');
        await page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 15000 }).catch(() => { });
        console.log('OTP verification passed.');
    } catch (e) {
        console.error('Error during OTP flow:', e.message);
        console.log('No OTP prompt found or it was bypassed. Proceeding to task routing...');
    }

    // Return the authenticated page
    return { page, browserHandler };
}

module.exports = {
    startSession
};
