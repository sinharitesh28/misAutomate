"""
Mentoring Task (V2 Two-Tab Wizard + Excel .xlsx Review/Dropdown Workflow)

Modes:
  - python main.py mentoring --prepare   : Fetch all mentees + attendance + live questions -> generate mentoring_plan.xlsx
  - python main.py mentoring --submit    : Read mentoring_plan.xlsx -> fill Tab 1 & Tab 2 -> save -> download UPPERCASE PDFs
  - python main.py mentoring             : Run --prepare, pause for user Excel review/edit, then run --submit
"""
import os
import json
import re
import random
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from playwright.async_api import Page, BrowserContext, TimeoutError as PlaywrightTimeoutError

LIST_URL = "https://ums.paruluniversity.ac.in/AdminPanel/Mentoring/MEN_StudentMentor/MEN_StudentMentorListMentor.aspx"
EXCEL_PLAN_FILE = "mentoring_plan.xlsx"


def parse_mis_datetime(date_str: str) -> datetime:
    match = re.search(r'(\d{2})-(\d{2})-(\d{4})\s+(\d{1,2}):(\d{2})\s*(AM|PM)', str(date_str).strip(), re.IGNORECASE)
    if not match:
        raise ValueError(f"Invalid MIS date format: {date_str!r}. Expected 'DD-MM-YYYY HH:MM AM/PM'.")
    dd, mm, yyyy, hh, mn, ampm = match.groups()
    hour = int(hh)
    if ampm.upper() == 'PM' and hour < 12:
        hour += 12
    elif ampm.upper() == 'AM' and hour == 12:
        hour = 0
    return datetime(int(yyyy), int(mm), int(dd), hour, int(mn))


def format_mis_datetime(dt: datetime) -> str:
    return dt.strftime("%d-%m-%Y %I:%M %p")


def add_minutes_to_date_str(date_str: str, minutes_to_add: int) -> str:
    try:
        dt = parse_mis_datetime(date_str)
        return format_mis_datetime(dt + timedelta(minutes=minutes_to_add))
    except Exception:
        return str(date_str)


def compute_future_next_meeting_date(meeting_date_str: str, days_to_add: int) -> str:
    """
    Ensures Next Mentoring Date is ALWAYS strictly in the future (> current system datetime),
    even if meeting_date_str is a backdated mentoring session.
    """
    try:
        meeting_dt = parse_mis_datetime(meeting_date_str)
    except Exception:
        meeting_dt = datetime.now()

    now = datetime.now()
    base_dt = meeting_dt if meeting_dt > now else now.replace(hour=meeting_dt.hour, minute=meeting_dt.minute, second=0, microsecond=0)
    target_dt = base_dt + timedelta(days=max(1, days_to_add))
    if target_dt <= now:
        target_dt = now + timedelta(days=max(1, days_to_add))
    return format_mis_datetime(target_dt)


async def wait_for_aspnet_ajax(page: Page, timeout_ms: int = 15000):
    """Waits for ASP.NET PageRequestManager async postbacks (UpdatePanel) to finish."""
    try:
        await page.wait_for_function(
            """() => {
                if (typeof Sys !== 'undefined' && Sys.WebForms && Sys.WebForms.PageRequestManager) {
                    const prm = Sys.WebForms.PageRequestManager.getInstance();
                    if (prm && prm.get_isInAsyncPostBack()) return false;
                }
                return true;
            }""",
            timeout=timeout_ms,
        )
    except Exception:
        pass


async def get_page_server_messages(page: Page) -> dict:
    """Extracts any server-side error or success messages rendered on the ASP.NET page."""
    return await page.evaluate(
        r"""() => {
            const errors = [];
            const successes = [];
            const errSelectors = [
                '.alert-danger', '.alert-warning', '.validation-summary-errors',
                '[id*="lblError"]', '[id*="divError"]', '[id*="lblWarning"]',
                '.toast-error', '.toast-warning'
            ];
            const okSelectors = [
                '.alert-success', '[id*="lblSuccess"]', '[id*="divSuccess"]', '.toast-success'
            ];
            for (const sel of errSelectors) {
                document.querySelectorAll(sel).forEach(el => {
                    if (el.offsetParent !== null && el.innerText.trim()) {
                        errors.push(el.innerText.trim().replace(/\s+/g, ' '));
                    }
                });
            }
            for (const sel of okSelectors) {
                document.querySelectorAll(sel).forEach(el => {
                    if (el.offsetParent !== null && el.innerText.trim()) {
                        successes.push(el.innerText.trim().replace(/\s+/g, ' '));
                    }
                });
            }
            return {
                errors: Array.from(new Set(errors)),
                successes: Array.from(new Set(successes)),
            };
        }"""
    )


async def set_value_by_id(page: Page, element_id: str, value: str, element_type: str = "auto") -> dict:
    try:
        await page.wait_for_selector(f"#{element_id}", state="attached", timeout=8000)
    except Exception:
        pass

    js_code = r"""([elId, val, elementType]) => {
        const node = document.getElementById(elId);
        if (!node) return { success: false, error: 'Node not found' };
        node.scrollIntoView({ behavior: 'instant', block: 'center' });

        const tag = node.tagName.toUpperCase();
        const resolvedType = elementType === 'auto' ? (tag === 'SELECT' ? 'select' : 'input') : elementType;
        const cleanStr = (s) => (s || '').replace(/[\s\-_.]+/g, '').toLowerCase();
        const cleanVal = cleanStr(val);

        if (resolvedType === 'select') {
            let optionFound = false;

            for (let opt of node.options) {
                if (opt.value === val || cleanStr(opt.text) === cleanVal || cleanStr(opt.value) === cleanVal) {
                    node.value = opt.value;
                    optionFound = true;
                    break;
                }
            }

            if (!optionFound) {
                for (let opt of node.options) {
                    const low = opt.text.toLowerCase();
                    if (!low.includes('select') && opt.value !== '-99' && cleanStr(opt.text).includes(cleanVal)) {
                        node.value = opt.value;
                        optionFound = true;
                        break;
                    }
                }
            }

            if (!optionFound && cleanVal === 'na') {
                for (let opt of node.options) {
                    if (opt.value !== '-99' && !opt.text.toLowerCase().includes('select')) {
                        node.value = opt.value;
                        optionFound = true;
                        break;
                    }
                }
            }

            if (optionFound) {
                if (typeof jQuery !== 'undefined') {
                    $(node).trigger('change');
                    $(node).trigger('change.select2');
                }
                node.dispatchEvent(new Event('change', { bubbles: true }));
                return { success: true, actual: node.value };
            }
            const available = Array.from(node.options).map(o => `${o.value}:${o.text.trim()}`);
            return { success: false, error: `Option not found: ${val} (available: ${available.join(', ')})` };
        } else {
            node.value = val;
            node.dispatchEvent(new Event('input', { bubbles: true }));
            node.dispatchEvent(new Event('change', { bubbles: true }));
            node.dispatchEvent(new Event('blur', { bubbles: true }));
            return { success: true, actual: node.value };
        }
    }"""

    result = {"success": False, "error": "Unknown"}
    for _ in range(3):
        result = await page.evaluate(js_code, [element_id, value, element_type])
        if result.get("success"):
            break
        await asyncio.sleep(0.8)

    if not result.get("success"):
        print(f"  Warning: [{element_id}] {result.get('error')}")
    else:
        await asyncio.sleep(0.2)
    return result


async def select_radio_by_id(page: Page, radio_id: str) -> bool:
    ok = await page.evaluate(
        r"""(id) => {
            const el = document.getElementById(id);
            if (!el) return false;
            el.scrollIntoView({ behavior: 'instant', block: 'center' });
            el.checked = true;
            if (typeof jQuery !== 'undefined') {
                $(el).prop('checked', true).trigger('change');
            }
            el.dispatchEvent(new Event('click', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            return el.checked === true;
        }""",
        radio_id,
    )
    if not ok:
        print(f"  Warning: Could not check radio button #{radio_id}")
    return ok


async def get_failed_validators(page: Page, validation_group: str = None) -> list[dict]:
    """Runs ASP.NET client-side validation for a group and returns any failing validators."""
    return await page.evaluate(
        r"""(vg) => {
            if (typeof Page_ClientValidate === 'function') {
                try { Page_ClientValidate(vg || ''); } catch (e) {}
            }
            if (typeof Page_Validators === 'undefined' || !Array.isArray(Page_Validators)) return [];
            const failed = [];
            for (const v of Page_Validators) {
                if (v.enabled === false) continue;
                if (vg && v.validationGroup && v.validationGroup !== vg) continue;
                if (v.isvalid === false) {
                    const ctrl = v.controltovalidate ? document.getElementById(v.controltovalidate) : null;
                    failed.push({
                        id: v.id || '',
                        control: v.controltovalidate || '',
                        message: (v.errormessage || v.innerText || '').trim(),
                        value: ctrl ? (ctrl.value || '') : '<missing>',
                    });
                }
            }
            return failed;
        }""",
        validation_group,
    )


async def auto_fill_remaining_required_fields(page: Page, validation_group: str, default_text: str = "NA"):
    """Finds any failing required fields in validation_group and fills them with 'NA' or first valid option."""
    failed = await get_failed_validators(page, validation_group)
    if not failed:
        return

    for fv in failed:
        ctrl_id = fv.get("control")
        if not ctrl_id:
            continue
        info = await page.evaluate(
            r"""(id) => {
                const el = document.getElementById(id);
                if (!el) return null;
                return { tag: el.tagName.toUpperCase(), type: (el.type || '').toLowerCase() };
            }""",
            ctrl_id,
        )
        if not info:
            continue

        if info["tag"] == "SELECT":
            print(f"  Auto-filling mandatory select [{ctrl_id}] ({fv['message']}) -> 'NA' / first valid option")
            await set_value_by_id(page, ctrl_id, default_text, "select")
        elif info["tag"] in ("INPUT", "TEXTAREA") and info["type"] not in ("hidden", "file", "submit", "button"):
            print(f"  Auto-filling mandatory field [{ctrl_id}] ({fv['message']}) -> {default_text!r}")
            await set_value_by_id(page, ctrl_id, default_text, "input")


async def collect_all_mentees(page: Page) -> list[dict]:
    """Navigates to MEN_StudentMentorListMentor.aspx and collects all mentees across all pages."""
    print("Navigating to Mentee List...")
    await page.goto(LIST_URL, wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(3)
    await wait_for_aspnet_ajax(page)

    print("Collecting all mentees across pages...")
    all_mentees = []
    seen_enrollments = set()
    has_more_pages = True
    page_num = 1

    while has_more_pages:
        mentees_on_page = await page.evaluate(
            r"""(p) => {
                const tables = document.querySelectorAll('table');
                const items = [];
                tables.forEach(table => {
                    const rows = table.querySelectorAll('tr');
                    rows.forEach((row) => {
                        const cells = row.querySelectorAll('td');
                        if (cells.length >= 10) {
                            const menteeCell = cells[3];
                            const actionCell = cells[10];
                            if (!menteeCell || !actionCell) return;

                            const links = menteeCell.querySelectorAll('a');
                            let enrollmentNo = '';
                            let studentName = '';
                            let studentId = '';

                            if (links.length > 0) {
                                enrollmentNo = links[0].textContent.trim();
                                const href = links[0].getAttribute('href') || '';
                                const match = href.match(/StudentID=([^&]+)/i);
                                if (match) {
                                    studentId = match[1];
                                } else {
                                    const clickMatch = (links[0].getAttribute('onclick') || '').match(/['"]([^'"]+)['"]/);
                                    studentId = clickMatch ? clickMatch[1] : enrollmentNo;
                                }
                                studentName = menteeCell.textContent.replace(enrollmentNo, '').trim();
                            } else {
                                const text = menteeCell.textContent.trim();
                                const parts = text.split(/\s+/);
                                enrollmentNo = parts[0];
                                studentId = enrollmentNo;
                                studentName = parts.slice(1).join(' ');
                            }

                            studentName = studentName.replace(/[0-9]{2,}[A-Z]+[0-9]+/g, '').trim();
                            if (!enrollmentNo || enrollmentNo.toLowerCase().includes("enroll")) return;

                            const countCell = cells[9];
                            const countLink = countCell ? (countCell.querySelector('a[id*="hlNoOfMentoring"]') || countCell.querySelector('a')) : null;
                            const mentoringListHref = countLink ? (countLink.getAttribute('href') || '') : '';

                            const actionLinks = actionCell.querySelectorAll('a');
                            const targetActionLink = actionLinks.length >= 2 ? actionLinks[1] : (actionLinks[0] || null);
                            let actionHref = targetActionLink ? (targetActionLink.getAttribute('href') || '') : '';

                            items.push({ studentId, enrollmentNo, studentName, pageNum: p, actionHref, mentoringListHref });
                        }
                    });
                });
                return items;
            }""",
            page_num,
        )

        for m in mentees_on_page:
            if m["enrollmentNo"] not in seen_enrollments:
                seen_enrollments.add(m["enrollmentNo"])
                all_mentees.append(m)

        print(f"Page {page_num}: Found {len(mentees_on_page)} mentees. Unique total so far: {len(all_mentees)}")

        has_more_pages = await page.evaluate(
            """(currentP) => {
                const pagerLinks = Array.from(document.querySelectorAll('a'));
                const nextNumLink = pagerLinks.find(a => a.innerText.trim() === (currentP + 1).toString());
                if (nextNumLink) { nextNumLink.click(); return true; }
                const nextBtn = pagerLinks.find(a => a.innerText.trim() === 'Next' || a.innerText.trim() === '>');
                if (nextBtn && !nextBtn.classList.contains('disabled')) { nextBtn.click(); return true; }
                return false;
            }""",
            page_num,
        )

        if has_more_pages:
            page_num += 1
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass
            await asyncio.sleep(2)
            await wait_for_aspnet_ajax(page)

    return all_mentees


async def fetch_student_details(page: Page, student_id: str) -> tuple[str, str]:
    """Fetches Gender and Overall Attendance % from STU_StudentViewDetailed.aspx."""
    if not student_id:
        return "Unknown", "Unknown"

    detail_url = (
        f"https://ums.paruluniversity.ac.in/AdminPanel/Student/STU_Student/"
        f"STU_StudentViewDetailed.aspx?StudentID={student_id}"
    )
    await page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(2)

    gender = await page.evaluate(
        r"""() => {
            const labels = Array.from(document.querySelectorAll('label, th, td, span, strong'));
            for (const lbl of labels) {
                const txt = (lbl.textContent || '').trim().toLowerCase();
                if (txt === 'gender' || txt === 'gender :' || txt === 'gender:') {
                    const next = lbl.nextElementSibling || (lbl.parentElement && lbl.parentElement.nextElementSibling);
                    if (next && next.textContent.trim()) {
                        const val = next.textContent.trim();
                        if (/^(male|female|m|f|other)$/i.test(val)) return val;
                    }
                }
            }
            const node = document.evaluate(
                "/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[2]/div[4]/div/div/div[2]/div[1]/div/div[2]/div/div[2]/div/div[1]/div[2]/div[2]/span",
                document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null
            ).singleNodeValue;
            return node ? node.textContent.trim() : 'Unknown';
        }"""
    )

    await page.evaluate(
        r"""() => {
            const tabLinks = Array.from(document.querySelectorAll('a[data-toggle="tab"], ul.nav-tabs a'));
            const attTab = tabLinks.find(a => (a.textContent || '').toLowerCase().includes('attendance'));
            if (attTab) {
                attTab.click();
                return;
            }
            const tabXPath = "/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[2]/div[4]/div/div/div[1]/ul/ul/li[8]/a";
            const node = document.evaluate(tabXPath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
            if (node) node.click();
        }"""
    )
    await asyncio.sleep(2.5)
    await wait_for_aspnet_ajax(page)

    raw_attendance = await page.evaluate(
        r"""() => {
            const targeted = document.querySelectorAll('span[id*="lblTotalAttendancePercentage_Sum"]');
            if (targeted.length > 0) {
                const last = targeted[targeted.length - 1];
                const text = last.textContent.trim();
                if (parseFloat(text) > 0) return text;
            }
            const tables = Array.from(document.querySelectorAll('table'));
            for (let table of tables) {
                const rows = Array.from(table.querySelectorAll('tr, tfoot tr'));
                for (let row of rows) {
                    const rowText = row.innerText.toLowerCase();
                    if (rowText.includes('total') && rowText.includes('%')) {
                        const match = row.innerText.match(/(\d+(?:\.\d+)?)\s*%/);
                        if (match) return match[0];
                    }
                }
            }
            return '0.00';
        }"""
    )
    return gender, raw_attendance


async def open_observation_page(page: Page, student_info: dict) -> bool:
    """Opens MEN_StudentMentorObservationV2.aspx for the given student."""
    action_href = str(student_info.get("actionHref") or "").strip()
    if action_href and not action_href.lower().startswith("javascript:"):
        if action_href.startswith("http"):
            obs_url = action_href
        elif action_href.startswith("/"):
            obs_url = f"https://ums.paruluniversity.ac.in{action_href}"
        else:
            obs_url = f"https://ums.paruluniversity.ac.in/AdminPanel/Mentoring/MEN_StudentMentor/{action_href.lstrip('./')}"
        await page.goto(obs_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)
        await wait_for_aspnet_ajax(page)
        return True

    await page.goto(LIST_URL, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(1.5)
    await wait_for_aspnet_ajax(page)

    page_num = int(student_info.get("pageNum") or 1)
    if page_num > 1:
        await page.evaluate(
            """(p) => {
                const pagerLinks = Array.from(document.querySelectorAll('a'));
                const pageLink = pagerLinks.find(a => a.innerText.trim() === p.toString());
                if (pageLink) pageLink.click();
            }""",
            page_num,
        )
        await asyncio.sleep(1.5)
        await wait_for_aspnet_ajax(page)

    row_loc = page.locator(f"tr:has-text('{student_info['enrollmentNo']}')")
    if await row_loc.count() > 0:
        links_in_row = row_loc.first.locator("td:nth-child(11) a")
        cnt = await links_in_row.count()
        if cnt >= 2:
            await links_in_row.nth(1).click()
            await asyncio.sleep(2)
            await wait_for_aspnet_ajax(page)
            return True
        elif cnt == 1:
            await links_in_row.first.click()
            await asyncio.sleep(2)
            await wait_for_aspnet_ajax(page)
            return True

    return False


async def get_pending_scheduled_meetings(page: Page) -> list[dict]:
    """Returns any pending scheduled meetings in ddlScheduledMeetingDate."""
    return await page.evaluate(
        r"""() => {
            const el = document.getElementById('ctl00_cphPageContent_ddlScheduledMeetingDate');
            if (!el) return [];
            return Array.from(el.options)
                .filter(o => o.value !== '-99' && !o.text.toLowerCase().includes('select'))
                .map(o => ({ value: o.value, text: o.text.trim() }));
        }"""
    )


async def prepare_header_controls(
    page: Page,
    enrollment_no: str,
    group_name: str,
    attendance_status: str,
    scheduled_meeting: str = "",
) -> bool:
    """Sets Mentor Type, Student ID, Scheduled Meeting (if pending), Question Group, and Attendance Status."""
    mentor_type_val = await page.evaluate(
        "() => { const el = document.getElementById('ctl00_cphPageContent_ddlMentorTypeID'); return el ? el.value : null; }"
    )
    if mentor_type_val == "-99":
        await set_value_by_id(page, "ctl00_cphPageContent_ddlMentorTypeID", "1", "select")
        await wait_for_aspnet_ajax(page)

    already_selected = await page.evaluate(
        r"""(enr) => {
            const el = document.getElementById('ctl00_cphPageContent_ddlStudentID');
            if (!el || el.value === '-99') return false;
            const opt = el.options[el.selectedIndex];
            return opt ? opt.text.includes(enr) : false;
        }""",
        enrollment_no,
    )
    if not already_selected:
        res = await set_value_by_id(page, "ctl00_cphPageContent_ddlStudentID", enrollment_no, "select")
        if not res.get("success"):
            print(f"  ❌ Could not select student {enrollment_no} in ddlStudentID.")
            return False
        await asyncio.sleep(1.0)
        await wait_for_aspnet_ajax(page)

    # Check if there is a pending Scheduled Mentoring Meeting in ddlScheduledMeetingDate
    pending_meetings = await get_pending_scheduled_meetings(page)
    if pending_meetings:
        target_sched = scheduled_meeting.strip() if scheduled_meeting and scheduled_meeting.strip().lower() not in ("", "none", "na") else pending_meetings[0]["value"]
        print(f"  Found pending scheduled meeting(s): {[m['text'] for m in pending_meetings]} -> selecting {target_sched!r}")
        await set_value_by_id(page, "ctl00_cphPageContent_ddlScheduledMeetingDate", target_sched, "select")
        await asyncio.sleep(1.0)
        await wait_for_aspnet_ajax(page)

    # Select Attendance Status radio (Present / Absent)
    att_radio_id = (
        "ctl00_cphPageContent_rbtlAttendanceStatus_1"
        if str(attendance_status).strip().lower() == "absent"
        else "ctl00_cphPageContent_rbtlAttendanceStatus_0"
    )
    await select_radio_by_id(page, att_radio_id)

    # Select Mentoring Question Group
    group_id = "ctl00_cphPageContent_ddlMentoringQuestionGroupID"
    populated = 0
    for _ in range(25):
        populated = await page.evaluate(
            "(id) => { const el = document.getElementById(id); return el ? el.options.length : 0; }",
            group_id,
        )
        if populated >= 2:
            break
        await asyncio.sleep(0.4)

    if populated < 2:
        print(f"  ❌ Group dropdown #{group_id} did not populate options (count={populated}).")
        return False

    candidates = [group_name, "Group-1 - PIP", "Group-1-PIP", "Group 1 - PIP", "Group-1", "Group 1"]
    target_value = await page.evaluate(
        r"""([id, cands]) => {
            const node = document.getElementById(id);
            if (!node) return null;
            const cleanStr = (s) => (s || '').replace(/[\s\-_]+/g, '').toLowerCase();
            for (const val of cands) {
                const cleanVal = cleanStr(val);
                for (let opt of node.options) {
                    if (cleanStr(opt.text) === cleanVal) return opt.value;
                }
                for (let opt of node.options) {
                    const t = opt.text.toLowerCase();
                    if (!t.includes('select') && opt.value !== '-99' && cleanStr(opt.text).includes(cleanVal)) {
                        return opt.value;
                    }
                }
            }
            const validOpts = Array.from(node.options).filter(o => o.value !== '-99');
            return validOpts.length === 1 ? validOpts[0].value : null;
        }""",
        [group_id, candidates],
    )
    if not target_value:
        print(f"  ❌ Could not match Mentoring Question Group in #{group_id}.")
        return False

    try:
        await page.locator(f"#{group_id}").select_option(value=target_value)
    except Exception:
        await set_value_by_id(page, group_id, target_value, "select")

    await asyncio.sleep(0.6)
    await wait_for_aspnet_ajax(page)

    await page.evaluate(
        """() => {
            const btn = document.getElementById('ctl00_cphPageContent_btnLoad');
            if (btn) btn.click();
        }"""
    )
    await asyncio.sleep(1.5)
    await wait_for_aspnet_ajax(page)
    return True


async def inspect_live_questions(page: Page) -> list[dict]:
    """Dynamically inspects all questions in rpQuestionList on the live ObservationV2 page."""
    return await page.evaluate(
        r"""() => {
            const questions = [];
            for (let idx = 0; idx < 50; idx++) {
                const pad = String(idx).padStart(2, '0');
                const prefix = `ctl00_cphPageContent_rpQuestionList_ctl${pad}_`;
                const lbl = document.getElementById(`${prefix}lblMentoringQuestion`);
                const ddl = document.getElementById(`${prefix}ddlAnswer`);
                const txt = document.getElementById(`${prefix}txtMentoringAnswer`);
                if (!lbl && !ddl && !txt) break;

                const qText = lbl ? lbl.innerText.trim() : `Question_${idx + 1}`;
                const inputId = ddl ? ddl.id : (txt ? txt.id : '');
                const inputType = ddl ? 'select' : 'textarea';
                const options = ddl
                    ? Array.from(ddl.options).map(o => ({ value: o.value, text: o.text.trim() }))
                    : [];

                questions.push({
                    index: idx,
                    pad,
                    questionText: qText,
                    inputId,
                    inputType,
                    options,
                });
            }
            return questions;
        }"""
    )


def build_default_question_answer(
    q_info: dict, gender: str, f_att: float, att_str: str, perf: str, suggestions: str
) -> str:
    """Computes default answer for known questions, and 'NA' for any newly added mandatory question."""
    q_low = q_info["questionText"].lower()
    idx = q_info["index"]

    if "academic category" in q_low or idx == 0:
        if f_att >= 75:
            return "A. Advanced Learner"
        elif f_att >= 60:
            return "B. Mediocre Learner"
        return "C. Slow Learner"

    if "personality" in q_low or idx == 1:
        return random.choice(["Positive attitude", "Friendly and collaborative"])

    if "grievance" in q_low or idx == 2:
        return "No"

    if "co-curricular" in q_low or "extra-curricular" in q_low or idx == 3:
        g = str(gender).lower().strip()
        if g in ("m", "male") or ("male" in g and "female" not in g):
            return random.choice(["Cricket", "Football", "Music"])
        elif g in ("f", "female") or "female" in g:
            return random.choice(["Music", "Dance", "Tennis"])
        return "Music"

    if q_low == "attendance" or ("attendance" in q_low and idx == 4):
        return att_str

    if "difficulties" in q_low or "subjects" in q_low or idx == 5:
        return "No"

    if "study performance" in q_low or idx == 6:
        return perf

    if "performance in exam" in q_low or idx == 7:
        return perf

    if "communication problem" in q_low or idx == 8:
        return "No"

    if "suggestion" in q_low or idx == 9:
        return suggestions

    if q_info["inputType"] == "select":
        valid_opts = [o["text"] for o in q_info.get("options", []) if o["value"] != "-99"]
        for opt_txt in valid_opts:
            if opt_txt.strip().lower() in ("na", "n/a", "no", "not applicable"):
                return opt_txt
        return valid_opts[0] if valid_opts else "NA"

    return "NA"


def make_question_col_name(q_info: dict) -> str:
    clean_label = re.sub(r"[^A-Za-z0-9]+", "_", q_info["questionText"]).strip("_")[:45]
    return f"Q{q_info['index'] + 1}_{clean_label}"


def write_styled_excel_plan(
    xlsx_path: Path,
    fieldnames: list[str],
    rows: list[dict],
    dropdown_map: dict[str, list[str]],
    text_format_cols: set[str],
):
    """
    Writes mentoring_plan.xlsx with:
      - Sheet 'Mentoring_Plan': Styled headers, frozen header row, auto-filter,
        @ text formatting on date/enrollment/attendance columns so Excel never alters formats,
        and real Excel DataValidation dropdowns backed by a hidden/reference 'Dropdown_Options' sheet.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Mentoring_Plan"

    ws_opts = wb.create_sheet(title="Dropdown_Options")

    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    question_header_fill = PatternFill(start_color="2E75B6", end_color="2E75B6", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True, size=11)
    thin_border = Border(
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"),
        bottom=Side(style="thin", color="D9D9D9"),
    )

    # Write headers
    for col_idx, col_name in enumerate(fieldnames, start=1):
        cell = ws.cell(row=1, column=col_idx, value=col_name)
        cell.fill = question_header_fill if re.match(r"^Q\d+_", col_name) else header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = thin_border

    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "D2"

    # Write data rows
    for row_idx, r in enumerate(rows, start=2):
        for col_idx, col_name in enumerate(fieldnames, start=1):
            val = r.get(col_name, "")
            cell = ws.cell(row=row_idx, column=col_idx, value=str(val) if val is not None else "")
            cell.border = thin_border
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            if col_name in text_format_cols:
                cell.number_format = "@"

    # Populate Dropdown_Options sheet & attach Excel DataValidation dropdowns
    max_data_row = max(2, len(rows) + 1)
    opt_col_idx = 1
    for col_name, options_list in dropdown_map.items():
        if col_name not in fieldnames or not options_list:
            continue
        target_col_idx = fieldnames.index(col_name) + 1
        target_col_letter = get_column_letter(target_col_idx)

        opt_col_letter = get_column_letter(opt_col_idx)
        ws_opts.cell(row=1, column=opt_col_idx, value=col_name).font = Font(bold=True)
        for o_i, opt_val in enumerate(options_list, start=2):
            c = ws_opts.cell(row=o_i, column=opt_col_idx, value=str(opt_val))
            c.number_format = "@"

        formula = f"=Dropdown_Options!${opt_col_letter}$2:${opt_col_letter}${len(options_list) + 1}"
        dv = DataValidation(type="list", formula1=formula, allow_blank=False)
        dv.errorTitle = "Invalid Selection"
        dv.error = f"Please select a valid option from the dropdown for {col_name}."
        dv.promptTitle = col_name
        dv.prompt = "Select an allowed value from the dropdown list."
        ws.add_data_validation(dv)
        dv.add(f"{target_col_letter}2:{target_col_letter}{max_data_row}")
        opt_col_idx += 1

    # Add input format guidance on date columns so users preserve DD-MM-YYYY HH:MM AM/PM
    for date_col in ("dateOfMentoring", "nextMentoringDate"):
        if date_col in fieldnames:
            c_idx = fieldnames.index(date_col) + 1
            c_let = get_column_letter(c_idx)
            dv_date = DataValidation(type="custom", formula1=f"=LEN({c_let}2)>=16", allow_blank=False)
            dv_date.promptTitle = f"{date_col} Format"
            dv_date.prompt = "Keep exact format: DD-MM-YYYY HH:MM AM/PM (e.g., 17-12-2026 09:00 AM). Next Mentoring Date must be a future date."
            dv_date.errorTitle = "Invalid Date Format"
            dv_date.error = "Use exact format: DD-MM-YYYY HH:MM AM/PM (e.g., 17-12-2026 09:00 AM)."
            ws.add_data_validation(dv_date)
            dv_date.add(f"{c_let}2:{c_let}{max_data_row}")

    # Auto-fit column widths cleanly
    for col_idx, col_name in enumerate(fieldnames, start=1):
        col_letter = get_column_letter(col_idx)
        if col_name in ("studentId", "actionHref", "mentoringListHref"):
            ws.column_dimensions[col_letter].width = 14
            ws.column_dimensions[col_letter].hidden = True
        else:
            max_len = max(
                len(col_name),
                max((len(str(r.get(col_name, ""))) for r in rows), default=10),
            )
            ws.column_dimensions[col_letter].width = min(max(max_len + 4, 15), 42)

    ws.auto_filter.ref = f"A1:{get_column_letter(len(fieldnames))}{max_data_row}"
    wb.save(xlsx_path)


async def generate_mentoring_excel(page: Page, config: dict, xlsx_path: Path) -> Path:
    """
    Phase 1: Collects all mentees from MIS, fetches Gender & Attendance %,
    discovers all live form questions & dropdown options (including newly added mandatory ones -> 'NA'),
    and writes mentoring_plan.xlsx with dropdowns & locked text formats for user review.
    """
    all_mentees = await collect_all_mentees(page)
    if not all_mentees:
        raise RuntimeError("No mentees found on MIS Mentee List page.")

    print("\nInspecting live Mentoring Observation form questions & dropdowns...")
    live_questions = []
    if await open_observation_page(page, all_mentees[0]):
        await prepare_header_controls(
            page,
            all_mentees[0]["enrollmentNo"],
            config.get("questionGroup", "Group-1 - PIP"),
            config.get("attendanceStatus", "Present"),
        )
        live_questions = await inspect_live_questions(page)
        print(f"Discovered {len(live_questions)} questions on live form:")
        for q in live_questions:
            print(f"  - Q{q['index'] + 1} ({q['inputType']}): {q['questionText']}")

    current_meeting_time = config["startDate"]
    interval_minutes = int(config.get("intervalMinutes", 10))
    next_mentoring_days = int(config.get("nextMentoringDays", 30))
    attendance_status = config.get("attendanceStatus", "Present")
    parent_present = "Yes" if config.get("parentPresent", False) else "No"
    group_name = config.get("questionGroup", "Group-1 - PIP")

    base_columns = [
        "studentId",
        "enrollmentNo",
        "studentName",
        "pageNum",
        "actionHref",
        "mentoringListHref",
        "gender",
        "attendance",
        "questionGroup",
        "pendingScheduledMeeting",
        "attendanceStatus",
        "dateOfMentoring",
        "nextMentoringDate",
        "learnerType",
        "stressLevel",
        "agenda",
        "issuesDiscussed",
        "studentsOpinion",
        "parentPresent",
        "specialRemarksForHOD",
        "specialRemarksForMentor",
        "mentorsOpinion",
        "otherRemarks",
    ]
    question_columns = [make_question_col_name(q) for q in live_questions]
    fieldnames = base_columns + question_columns

    dropdown_map = {
        "attendanceStatus": ["Present", "Absent"],
        "learnerType": ["Slow", "Average", "Fast"],
        "stressLevel": ["No Stress", "Mild", "Moderate", "Severe"],
        "parentPresent": ["No", "Yes"],
    }
    for q in live_questions:
        if q["inputType"] == "select":
            col_name = make_question_col_name(q)
            opts = [o["text"] for o in q.get("options", []) if o["value"] != "-99" and "select" not in o["text"].lower()]
            if opts:
                dropdown_map[col_name] = opts

    text_format_cols = {
        "enrollmentNo",
        "attendance",
        "dateOfMentoring",
        "nextMentoringDate",
    }

    rows = []
    for i, m in enumerate(all_mentees):
        print(f"Fetching details [{i + 1}/{len(all_mentees)}]: {m['enrollmentNo']} - {m['studentName']}...")
        gender, raw_attendance = await fetch_student_details(page, m["studentId"])

        att_to_enter = raw_attendance
        try:
            f_att = float(att_to_enter.replace("%", "").strip())
            if f_att < 1:
                raise ValueError()
        except ValueError:
            f_att = round(random.uniform(80, 93), 2)
            att_to_enter = f"{f_att:.2f}%"
        if "%" not in att_to_enter:
            att_to_enter += "%"

        if f_att > 90:
            perf = "Excellent"
        elif f_att > 80:
            perf = "Good"
        elif f_att > 70:
            perf = "Average"
        elif f_att > 60:
            perf = "Fair"
        else:
            perf = "Poor"

        # Always guarantee Next Mentoring Date is a valid future date (> today)
        next_meeting_time = config.get("nextMentoringDate") or compute_future_next_meeting_date(
            current_meeting_time, next_mentoring_days
        )

        row = {
            "studentId": m["studentId"],
            "enrollmentNo": m["enrollmentNo"],
            "studentName": m["studentName"],
            "pageNum": m["pageNum"],
            "actionHref": m.get("actionHref", ""),
            "mentoringListHref": m.get("mentoringListHref", ""),
            "gender": gender,
            "attendance": att_to_enter,
            "questionGroup": group_name,
            "pendingScheduledMeeting": "AUTO",
            "attendanceStatus": attendance_status,
            "dateOfMentoring": current_meeting_time,
            "nextMentoringDate": next_meeting_time,
            "learnerType": random.choice(["Average", "Fast"]),
            "stressLevel": random.choice(["Mild", "No Stress"]),
            "agenda": config.get("agenda", "Academic Progress and Career Guidance"),
            "issuesDiscussed": config.get("issuesDiscussed", "Time management and exam preparation strategies."),
            "studentsOpinion": config.get("studentsOpinion", "Satisfied with the guidance."),
            "parentPresent": parent_present,
            "specialRemarksForHOD": config.get("specialRemarksForHOD", "NA"),
            "specialRemarksForMentor": config.get("specialRemarksForMentor", "NA"),
            "mentorsOpinion": config.get("mentorsOpinion", "Student is on the right track and highly motivated."),
            "otherRemarks": config.get("otherRemarks", "NA"),
        }

        for q in live_questions:
            col = make_question_col_name(q)
            row[col] = build_default_question_answer(
                q, gender, f_att, att_to_enter, perf, config.get("suggestions", "Maintain consistency in studies.")
            )

        rows.append(row)
        current_meeting_time = add_minutes_to_date_str(current_meeting_time, interval_minutes)

    write_styled_excel_plan(xlsx_path, fieldnames, rows, dropdown_map, text_format_cols)
    print(f"\n✅ Generated Excel plan with dropdowns & locked formats at: {xlsx_path.resolve()}")
    return xlsx_path


def read_excel_plan(xlsx_path: Path) -> list[dict]:
    """Reads all rows from sheet 'Mentoring_Plan' in mentoring_plan.xlsx as string dicts."""
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb["Mentoring_Plan"] if "Mentoring_Plan" in wb.sheetnames else wb.active

    headers = [str(cell.value).strip() if cell.value is not None else "" for cell in ws[1]]
    rows = []
    for excel_row in ws.iter_rows(min_row=2, values_only=True):
        if not any(excel_row):
            continue
        row_dict = {}
        for col_name, cell_val in zip(headers, excel_row):
            if not col_name:
                continue
            if isinstance(cell_val, datetime):
                row_dict[col_name] = format_mis_datetime(cell_val)
            elif cell_val is None:
                row_dict[col_name] = ""
            else:
                row_dict[col_name] = str(cell_val).strip()
        if row_dict.get("enrollmentNo"):
            rows.append(row_dict)
    return rows


def resolve_mentoring_list_url(student_info: dict) -> str:
    """Builds MEN_StudentMentoringListForMentor.aspx?StudentMentorID=... from mentoringListHref or actionHref."""
    list_href = str(student_info.get("mentoringListHref") or "").strip()
    if list_href and "StudentMentorID=" in list_href:
        tail = list_href.split("/")[-1]
        return f"https://ums.paruluniversity.ac.in/AdminPanel/Mentoring/MEN_StudentMentoring/{tail}"

    action_href = str(student_info.get("actionHref") or "").strip()
    m = re.search(r"StudentMentorID=([^&]+)", action_href, re.IGNORECASE)
    if m:
        return (
            f"https://ums.paruluniversity.ac.in/AdminPanel/Mentoring/MEN_StudentMentoring/"
            f"MEN_StudentMentoringListForMentor.aspx?StudentMentorID={m.group(1)}"
        )
    return ""


async def download_mentoring_pdf(
    page: Page, context: BrowserContext, student_info: dict, expected_file_path: Path
) -> bool:
    """
    Opens MEN_StudentMentoringListForMentor.aspx?StudentMentorID=... and clicks
    the Print Mentoring Report icon (ctl00_cphPageContent_rpStudentMentoring_ctl00_lbPrint)
    in the last column of #tblStudentMentoring.
    """
    report_list_url = resolve_mentoring_list_url(student_info)
    if not report_list_url:
        print(f"  ⚠️ Missing StudentMentorID URL for {student_info['enrollmentNo']}.")
        return False

    await page.goto(report_list_url, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(2.0)
    await wait_for_aspnet_ajax(page)

    target_date = str(student_info.get("dateOfMentoring") or "").strip()

    print_selector = await page.evaluate(
        r"""(targetDate) => {
            const table = document.getElementById('tblStudentMentoring');
            if (table && targetDate) {
                const rows = Array.from(table.querySelectorAll('tbody tr, tr'));
                for (const tr of rows) {
                    if (tr.innerText && tr.innerText.includes(targetDate)) {
                        const btn = tr.querySelector('a[id*="_lbPrint"]');
                        if (btn && btn.id) return '#' + btn.id;
                    }
                }
            }
            const firstBtn = document.getElementById('ctl00_cphPageContent_rpStudentMentoring_ctl00_lbPrint')
                || document.querySelector('#tblStudentMentoring a[id*="_lbPrint"]');
            return (firstBtn && firstBtn.id) ? ('#' + firstBtn.id) : '';
        }""",
        target_date,
    )

    if not print_selector:
        print(f"  ⚠️ No Print button (lbPrint) found in #tblStudentMentoring for {student_info['enrollmentNo']}.")
        return False

    # Attempt 1: Direct file download triggered by ASP.NET postback
    pages_before = set(context.pages)
    try:
        async with page.expect_download(timeout=15000) as download_info:
            await page.locator(print_selector).first.click()
        download = await download_info.value
        await download.save_as(expected_file_path)
        print(f"  ✅ Successfully downloaded: {expected_file_path.name}")
        return True
    except Exception:
        pass

    # Attempt 2: Report opened in a new popup/tab window
    await asyncio.sleep(2.0)
    new_pages = [p for p in context.pages if p not in pages_before]
    for new_p in new_pages:
        try:
            await new_p.wait_for_load_state("domcontentloaded", timeout=15000)
            await asyncio.sleep(1.5)
            await new_p.pdf(path=str(expected_file_path))
            await new_p.close()
            print(f"  ✅ Saved PDF from report tab: {expected_file_path.name}")
            return True
        except Exception as e:
            print(f"  ⚠️ Failed saving popup tab as PDF: {e}")

    print(f"  ⚠️ PDF download did not trigger for {student_info['enrollmentNo']} (selector={print_selector}).")
    return False


async def download_all_from_excel(page: Page, xlsx_path: Path):
    """Downloads UPPERCASE PDF reports for all mentees listed in mentoring_plan.xlsx."""
    if not xlsx_path.exists():
        raise FileNotFoundError(f"{xlsx_path} not found.")

    rows = read_excel_plan(xlsx_path)
    download_path = Path(os.getcwd()) / "downloads"
    download_path.mkdir(exist_ok=True)

    print(f"\nDownloading mentoring PDF reports for {len(rows)} mentees...")
    success_count = 0
    for i, row in enumerate(rows):
        enrollment_no = row["enrollmentNo"].strip()
        student_name = row["studentName"].strip()
        safe_name = re.sub(r"[^A-Z0-9]+", "_", student_name.upper()).strip("_")
        expected_file_name = f"{enrollment_no.upper()}-{safe_name}.pdf"
        expected_file_path = download_path / expected_file_name

        print(f"[{i + 1}/{len(rows)}] {enrollment_no} - {student_name}")
        if expected_file_path.exists() and expected_file_path.stat().st_size > 1000:
            print(f"  Already exists: {expected_file_name}")
            success_count += 1
            continue

        if await download_mentoring_pdf(page, page.context, row, expected_file_path):
            success_count += 1

    print(f"\n--- Download Complete: {success_count}/{len(rows)} PDFs saved in {download_path} ---")


def update_excel_row_status(xlsx_path: Path, enrollment_no: str, status_text: str):
    """Updates or appends a 'status' column in mentoring_plan.xlsx so progress is tracked per row."""
    try:
        wb = openpyxl.load_workbook(xlsx_path)
        ws = wb["Mentoring_Plan"] if "Mentoring_Plan" in wb.sheetnames else wb.active
        headers = [str(c.value).strip() if c.value is not None else "" for c in ws[1]]
        if "status" not in headers:
            status_col = len(headers) + 1
            ws.cell(row=1, column=status_col, value="status")
        else:
            status_col = headers.index("status") + 1
        enr_col = headers.index("enrollmentNo") + 1
        for r_idx in range(2, ws.max_row + 1):
            if str(ws.cell(row=r_idx, column=enr_col).value or "").strip() == enrollment_no:
                ws.cell(row=r_idx, column=status_col, value=status_text)
                break
        wb.save(xlsx_path)
    except Exception as e:
        print(f"  Warning: Could not update status in Excel ({e})")


async def submit_from_mentoring_excel(page: Page, xlsx_path: Path):
    """
    Phase 2: Reads mentoring_plan.xlsx and submits each mentee through the 2-tab V2 wizard:
      Tab 1 (#tab_MeetingDetails) -> lbtnSaveMeetingDetails ("Save & Next Step", vg="Observation")
      Tab 2 (#tab_QuestionsList)  -> btnSaveQuestion ("Save", vg="ObservationQuestion")
      -> Download UPPERCASE PDF report
    """
    if not xlsx_path.exists():
        raise FileNotFoundError(f"{xlsx_path} not found. Run with --prepare first to generate it.")

    rows = read_excel_plan(xlsx_path)
    if not rows:
        print("Excel file has 0 rows. Nothing to submit.")
        return

    download_path = Path(os.getcwd()) / "downloads"
    download_path.mkdir(exist_ok=True)

    print(f"\nLoaded {len(rows)} mentees from {xlsx_path.name}. Starting MIS submission...")

    def clean_cell(val, fallback="NA"):
        s = (val or "").strip()
        return fallback if (not s or s.lower() == "nan") else s

    for i, row in enumerate(rows):
        enrollment_no = row["enrollmentNo"].strip()
        student_name = row["studentName"].strip()
        print(f"\n--- Submitting Mentee {i + 1} of {len(rows)}: {enrollment_no} - {student_name} ---")

        safe_name = re.sub(r"[^A-Z0-9]+", "_", student_name.upper()).strip("_")
        expected_file_name = f"{enrollment_no.upper()}-{safe_name}.pdf"
        expected_file_path = download_path / expected_file_name

        if row.get("status", "").strip().upper() == "SUBMITTED" and expected_file_path.exists():
            print(f"  Skipping {enrollment_no} — already marked SUBMITTED in Excel ({expected_file_name}).")
            continue

        if not await open_observation_page(page, row):
            print(f"  ❌ Could not open ObservationV2 page for {enrollment_no}.")
            continue

        sched_pref = row.get("pendingScheduledMeeting", "AUTO")
        sched_arg = "" if sched_pref.upper() == "AUTO" else sched_pref

        if not await prepare_header_controls(
            page,
            enrollment_no,
            row.get("questionGroup", "Group-1 - PIP"),
            row.get("attendanceStatus", "Present"),
            scheduled_meeting=sched_arg,
        ):
            print(f"  ❌ Failed to set header controls for {enrollment_no}.")
            continue

        # =========================================================================
        # STEP 1: TAB 1 (#tab_MeetingDetails) -> "Save & Next Step" (vg="Observation")
        # =========================================================================
        await page.evaluate(
            """() => {
                const tabLink = document.querySelector('a[href="#tab_MeetingDetails"]');
                if (tabLink) tabLink.click();
            }"""
        )
        await asyncio.sleep(0.4)

        print("  Filling Tab 1 (Meeting Details)...")
        await set_value_by_id(page, "ctl00_cphPageContent_dtpDateofMentoring", row.get("dateOfMentoring", ""), "input")
        await set_value_by_id(page, "ctl00_cphPageContent_dtpNextMentoringDate", row.get("nextMentoringDate", ""), "input")
        await set_value_by_id(page, "ctl00_cphPageContent_ddlStudentLearnerType", row.get("learnerType", "Average"), "select")
        await set_value_by_id(page, "ctl00_cphPageContent_ddlStressLevel", row.get("stressLevel", "No Stress"), "select")
        await set_value_by_id(page, "ctl00_cphPageContent_txtMentoringMeetingAgenda", clean_cell(row.get("agenda")), "textarea")
        await set_value_by_id(page, "ctl00_cphPageContent_txtIssuedDiscussed", clean_cell(row.get("issuesDiscussed")), "textarea")
        await set_value_by_id(page, "ctl00_cphPageContent_txtStudentsOpinion", clean_cell(row.get("studentsOpinion")), "textarea")
        await set_value_by_id(page, "ctl00_cphPageContent_txtSpecialRemarksForHOD", clean_cell(row.get("specialRemarksForHOD")), "textarea")
        await set_value_by_id(page, "ctl00_cphPageContent_txtSpecialRemarksForMentor", clean_cell(row.get("specialRemarksForMentor")), "textarea")
        await set_value_by_id(page, "ctl00_cphPageContent_txtMentorsOpinion", clean_cell(row.get("mentorsOpinion")), "textarea")
        await set_value_by_id(page, "ctl00_cphPageContent_txtRemarks", clean_cell(row.get("otherRemarks")), "textarea")

        parent_val = row.get("parentPresent", "No").strip().lower()
        parent_radio_id = (
            "ctl00_cphPageContent_rblParentPresent_0"
            if parent_val in ("yes", "true", "1")
            else "ctl00_cphPageContent_rblParentPresent_1"
        )
        await select_radio_by_id(page, parent_radio_id)

        await auto_fill_remaining_required_fields(page, "Observation", "NA")

        failed_tab1 = await get_failed_validators(page, "Observation")
        if failed_tab1:
            print(f"  ❌ Tab 1 ('Observation') client validation failed for {enrollment_no}: {failed_tab1}")
            await page.screenshot(path=f"error_tab1_{enrollment_no}.png", full_page=True)
            continue

        print("  Clicking 'Save & Next Step' (lbtnSaveMeetingDetails)...")
        async def handle_dialog(dialog):
            print(f"  System Alert: {dialog.message}")
            await dialog.accept()

        page.on("dialog", handle_dialog)
        await page.evaluate(
            """() => {
                const btn = document.getElementById('ctl00_cphPageContent_lbtnSaveMeetingDetails');
                if (btn) btn.click();
            }"""
        )

        try:
            await page.wait_for_selector('button[data-bb-handler="confirm"]', state="visible", timeout=5000)
            await page.click('button[data-bb-handler="confirm"]')
        except Exception:
            pass

        await asyncio.sleep(2.5)
        await wait_for_aspnet_ajax(page)
        page.remove_listener("dialog", handle_dialog)

        # Verify Tab 1 saved without server-side errors BEFORE proceeding to Tab 2!
        srv_msg = await get_page_server_messages(page)
        if srv_msg["errors"]:
            print(f"  ❌ Server rejected Tab 1 (Meeting Details) for {enrollment_no}:")
            for err in srv_msg["errors"]:
                print(f"     -> {err}")
            await page.screenshot(path=f"error_tab1_server_{enrollment_no}.png", full_page=True)
            continue
        if srv_msg["successes"]:
            print(f"  ✅ Tab 1 saved: {srv_msg['successes']}")

        # =========================================================================
        # STEP 2: TAB 2 (#tab_QuestionsList) -> "Save" (vg="ObservationQuestion")
        # =========================================================================
        await page.evaluate(
            """() => {
                const tabLink = document.querySelector('a[href="#tab_QuestionsList"]');
                if (tabLink) tabLink.click();
            }"""
        )
        await asyncio.sleep(0.6)

        live_questions = await inspect_live_questions(page)
        print(f"  Filling Tab 2 ({len(live_questions)} Questions)...")

        q_col_map = {}
        for col_name, col_val in row.items():
            m_q = re.match(r"^Q(\d+)_", col_name)
            if m_q:
                q_col_map[int(m_q.group(1)) - 1] = col_val

        for q in live_questions:
            idx = q["index"]
            ans = q_col_map.get(idx)
            if ans is None or str(ans).strip() == "" or str(ans).strip().lower() == "nan":
                ans = "NA"
            await set_value_by_id(page, q["inputId"], str(ans).strip(), q["inputType"])

        await auto_fill_remaining_required_fields(page, "ObservationQuestion", "NA")

        failed_tab2 = await get_failed_validators(page, "ObservationQuestion")
        if failed_tab2:
            print(f"  ❌ Tab 2 ('ObservationQuestion') client validation failed for {enrollment_no}: {failed_tab2}")
            await page.screenshot(path=f"error_tab2_{enrollment_no}.png", full_page=True)
            continue

        print("  Clicking 'Save' (btnSaveQuestion)...")
        page.on("dialog", handle_dialog)
        await page.evaluate(
            """() => {
                const btn = document.getElementById('ctl00_cphPageContent_btnSaveQuestion');
                if (btn) btn.click();
            }"""
        )

        try:
            await page.wait_for_selector('button[data-bb-handler="confirm"]', state="visible", timeout=4000)
            await page.click('button[data-bb-handler="confirm"]')
        except Exception:
            pass

        await asyncio.sleep(2.5)
        await wait_for_aspnet_ajax(page)
        page.remove_listener("dialog", handle_dialog)

        srv_msg2 = await get_page_server_messages(page)
        if srv_msg2["errors"]:
            print(f"  ❌ Server rejected Tab 2 (Questions) for {enrollment_no}:")
            for err in srv_msg2["errors"]:
                print(f"     -> {err}")
            await page.screenshot(path=f"error_tab2_server_{enrollment_no}.png", full_page=True)
            continue
        if srv_msg2["successes"]:
            print(f"  ✅ Tab 2 saved: {srv_msg2['successes']}")

        # =========================================================================
        # STEP 3: Download UPPERCASE PDF Report & Update Excel Status
        # =========================================================================
        print("  Downloading PDF report...")
        dl_ok = await download_mentoring_pdf(page, page.context, row, expected_file_path)
        update_excel_row_status(xlsx_path, enrollment_no, "SUBMITTED_AND_DOWNLOADED" if dl_ok else "SUBMITTED")

    print("\n--- Mentoring Task Complete ---")


async def execute(page: Page, task_args: list[str] = None):
    task_args = task_args or []
    config_path = Path("mentoring.json")
    if not config_path.exists():
        raise FileNotFoundError("mentoring.json not found in current working directory.")
    config = json.loads(config_path.read_text(encoding="utf-8"))

    xlsx_path = Path(EXCEL_PLAN_FILE)

    if "--prepare" in task_args:
        await generate_mentoring_excel(page, config, xlsx_path)
        return

    if "--download-only" in task_args:
        await download_all_from_excel(page, xlsx_path)
        return

    if "--submit" in task_args:
        await submit_from_mentoring_excel(page, xlsx_path)
        return

    # Default interactive 2-phase workflow: Prepare Excel -> Ask user to review/modify -> Submit from Excel
    await generate_mentoring_excel(page, config, xlsx_path)
    print("\n" + "=" * 75)
    print(f"REVIEW REQUIRED: Please open and review/modify Excel file:\n  {xlsx_path.resolve()}")
    print("=" * 75)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        input,
        "Press ENTER once you have reviewed/modified mentoring_plan.xlsx to proceed with MIS submission (or Ctrl+C to abort)... ",
    )
    await submit_from_mentoring_excel(page, xlsx_path)
