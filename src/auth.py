"""
Authentication Flow (Equivalent to auth.js)
"""
import os
import asyncio
from .browser import BrowserHandler
from .gmail import get_otp
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

async def start_session(browser_handler: BrowserHandler):
    page = browser_handler.page
    portal_user = os.getenv("PORTAL_USER")
    portal_pass = os.getenv("PORTAL_PASS")

    if not portal_user or not portal_pass:
        raise ValueError("Missing PORTAL_USER or PORTAL_PASS in .env file.")

    print('Navigating to MIS Portal...')
    await browser_handler.navigate('https://ums.paruluniversity.ac.in/Login.aspx')

    print('Filling in credentials...')
    user_selector = 'input[type="text"]:not([readonly]), #txtEnrollmentNo, #txtUsername, [name*="user"]'
    pass_selector = 'input[type="password"]'

    await page.wait_for_selector(user_selector, state="visible")

    text_inputs = await page.locator(user_selector).all()
    if text_inputs:
        for t in text_inputs:
            if await t.is_visible():
                await t.fill(portal_user)
                break
    else:
        raise Exception('Could not find username input field on the portal.')

    pass_inputs = await page.locator(pass_selector).all()
    if pass_inputs:
        for p in pass_inputs:
            if await p.is_visible():
                await p.fill(portal_pass)
                break
    else:
        raise Exception('Could not find password input field on the portal.')

    print('Submitting login form...')

    login_btn = None
    specific_selectors = ['#btnLogin', '#btnSubmit', 'input[type="submit"][value*="Login" i]', 'button:has-text("Login")']
    for sel in specific_selectors:
        loc = page.locator(sel)
        if await loc.count() > 0 and await loc.first.is_visible():
            login_btn = loc.first
            break

    if not login_btn:
        fallback_selector = 'input[type="submit"], button[type="submit"], .login-btn'
        all_btns = await page.locator(fallback_selector).all()
        for btn in all_btns:
            if await btn.is_visible():
                login_btn = btn
                break

    if not login_btn:
        raise Exception("Could not find a visible login button to click.")

    # Click login without waiting for navigation, ASP.NET handles this via JS postback
    await login_btn.click()

    print('Checking for OTP prompt...')
    otp_input_selector = '#txtOTP'

    try:
        await page.wait_for_selector(otp_input_selector, state='visible', timeout=15000)
        print('OTP verification prompt detected! Fetching OTP from email...')

        otp = await get_otp()
        print(f"Retrieved OTP: {otp}")

        # Fill OTP directly using the exact ID
        otp_locator = page.locator(otp_input_selector)
        await otp_locator.fill(otp)
        print("OTP entered into the form.")

        # AGGRESSIVE SUBMIT: Press Enter inside the input box, AND force a JS click
        print('Triggering Verify submission...')
        await otp_locator.press("Enter")
        await page.evaluate("const btn = document.getElementById('btnVerifyOPT'); if(btn) btn.click();")

        # Wait for portal to process OTP — verify by DOM state, not URL alone
        print('Waiting for portal to process OTP...')
        await asyncio.sleep(5)

        # Poll up to 20s for the OTP input to disappear (page pivoted to dashboard)
        otp_gone = False
        try:
            await page.wait_for_function(
                "() => { const el = document.getElementById('txtOTP'); return !el || el.offsetParent === null || el.offsetWidth === 0; }",
                timeout=20000,
            )
            otp_gone = True
        except PlaywrightTimeoutError:
            otp_gone = False

        diag = await page.evaluate("""() => {
            const el = document.getElementById('txtOTP');
            const errs = [];
            ['lblError','lblMessage','lblMsg','errorMsg','Message','lblFailure'].forEach(id => {
                const e = document.getElementById(id);
                if (e && e.offsetParent !== null && e.innerText.trim()) {
                    errs.push(id + ': ' + e.innerText.trim());
                }
            });
            return {
                otpExists: !!el,
                otpVisible: !!(el && el.offsetParent !== null),
                otpValue: el ? el.value : '',
                errors: errs,
                url: location.href,
            };
        }""")

        if otp_gone or not diag['otpVisible']:
            print(f"✅ OTP verified successfully! URL: {diag['url']}")
        else:
            print(f"\n⚠️ OTP verification did NOT complete after 25s.")
            print(f"   URL: {diag['url']}")
            print(f"   OTP field visible={diag['otpVisible']}, value={diag['otpValue']!r}")
            print(f"   Page errors: {diag['errors']}")
            try:
                await page.screenshot(path='otp_verify_failed.png', full_page=True)
                print("   Saved otp_verify_failed.png for debugging.")
            except Exception:
                pass

    except PlaywrightTimeoutError:
        print('\n--- DEBUG INFO ---')
        print('No OTP prompt detected within 15 seconds.')
        print('Proceeding without OTP (this might cause the script to fail if OTP was required)...\n')

    return page
