"""
Fill Attendance Task
"""
import asyncio
import os
from playwright.async_api import Page

async def execute(page: Page):
    print('Navigating directly to TTM_AttendanceStaffDashboard.aspx...')
    await page.goto('https://ums.paruluniversity.ac.in/AdminPanel/TMT_Timetable/TTM_Attendance/TTM_AttendanceStaffDashboard.aspx', wait_until='domcontentloaded')
    print('Reached Attendance Dashboard.')

    time_table_container_id = '#ctl00_cphPageContent_divTimeTable'
    has_more_pending_slots = True
    slots_processed = 0

    while has_more_pending_slots:
        print(f"Looking for Pending Attendance Slots for today... (Processed so far: {slots_processed})")

        try:
            await page.wait_for_selector(time_table_container_id, state="visible", timeout=15000)
            print("Timetable container found. Analysing today's column for pending slots...")

            # Evaluate the timetable to find slots
            pending_slot_found = await page.evaluate("""() => {
                const container = document.querySelector('#ctl00_cphPageContent_divTimeTable');
                if (!container) return false;

                const today = new Date();
                const dd = String(today.getDate()).padStart(2, '0');
                const mm = String(today.getMonth() + 1).padStart(2, '0');
                const yyyy = today.getFullYear();
                const shortYear = String(yyyy).slice(-2);

                const possibleDateStrings = [
                    `${dd}/${mm}/${yyyy}`, `${dd}-${mm}-${yyyy}`,
                    `${dd}/${mm}/${shortYear}`, `${dd}-${mm}-${shortYear}`
                ].map(s => s.toLowerCase());

                // Find the header row
                const headerRow = container.querySelector('tr.bold.gn-timetable-bg-silver.text-center');
                if (!headerRow) return false;

                let targetColIndex = -1;
                const headers = Array.from(headerRow.querySelectorAll('th'));

                for (let i = 0; i < headers.length; i++) {
                    const text = headers[i].innerText.toLowerCase();
                    if (possibleDateStrings.some(d => text.includes(d)) || text.includes('today')) {
                        targetColIndex = i;
                        break;
                    }
                }

                if (targetColIndex !== -1) {
                    // The data row is the next tr
                    const dataRows = Array.from(container.querySelectorAll('tr[align="center"]'));
                    for (const row of dataRows) {
                        const cells = Array.from(row.children);
                        if (cells.length > targetColIndex) {
                            const targetCell = cells[targetColIndex];
                            // Pending slots have class 'gn-timetable-font-red'
                            const pendingLinks = Array.from(targetCell.querySelectorAll('a.gn-timetable-font-red'));

                            if (pendingLinks.length > 0) {
                                pendingLinks[0].click();
                                return true;
                            }
                        }
                    }
                }
                return false;
            }""")

            if pending_slot_found:
                print('Successfully clicked a pending slot. Waiting for the attendance list to load...')
                try:
                    await page.wait_for_load_state('networkidle', timeout=5000)
                except Exception:
                    print('Navigation resolved or loaded via AJAX.')

                await asyncio.sleep(4)  # Additional wait for AJAX tables to populate

                print('Filling out form dropdowns (Planning, Status, Domain)...')

                # 1. Planning (Multiselect dropdown button)
                try:
                    print('Selecting Planning (1st option)...')
                    planning_btn = page.locator('button.multiselect.dropdown-toggle')
                    if await planning_btn.count() > 0:
                        await planning_btn.first.click()
                        await asyncio.sleep(0.5)  # wait for animation

                        # Select the first real option
                        first_option = page.locator('ul.multiselect-container li:not(.multiselect-item):not(.active) a label input[type="radio"], ul.multiselect-container li:not(.multiselect-item):not(.active) a label input[type="checkbox"]')
                        if await first_option.count() > 0:
                            await first_option.first.click()
                        else:
                            first_li = page.locator('ul.multiselect-container li:not(.multiselect-item):not(.active) a')
                            if await first_li.count() > 0:
                                await first_li.first.click()

                        await planning_btn.first.click()
                    else:
                        print('Planning multiselect dropdown button not found.')
                except Exception as e:
                    print(f'Error selecting Planning: {e}')

                # 2. Planning Status -> "Partially Completed"
                try:
                    print('Selecting Planning Status (Partially Completed)...')
                    await page.evaluate("""() => {
                        const statusSelect = document.querySelector('select[id*="ddlLessonPlanningStatusID"]');
                        if (statusSelect) {
                            const opt = Array.from(statusSelect.options).find(o => o.text.toLowerCase().includes('partially'));
                            if (opt && statusSelect.value !== opt.value) {
                                statusSelect.value = opt.value;
                                if (typeof jQuery !== 'undefined') $(statusSelect).trigger('change');
                                statusSelect.dispatchEvent(new Event('change', { bubbles: true }));
                            }
                        }
                    }""")
                except Exception as e:
                    print(f'Error selecting Planning Status: {e}')

                # 3. Targeted Learning Domain -> "Cognitive"
                try:
                    print('Selecting Targeted Learning Domain (Cognitive)...')
                    await page.evaluate("""() => {
                        const domainSelect = document.querySelector('#ctl00_cphPageContent_ddlClassMode');
                        if (domainSelect) {
                            const opt = Array.from(domainSelect.options).find(o => o.value === 'Cognitive' || o.text.toLowerCase().includes('cognitive'));
                            if (opt && domainSelect.value !== opt.value) {
                                domainSelect.value = opt.value;
                                if (typeof jQuery !== 'undefined') $(domainSelect).trigger('change');
                                domainSelect.dispatchEvent(new Event('change', { bubbles: true }));
                            }
                        }
                    }""")
                except Exception as e:
                    print(f'Error selecting Targeted Learning Domain: {e}')

                print('Marking attendance (Checking all boxes)...')
                await page.evaluate("""() => {
                    const checkAllTh = document.querySelector('#ctl00_cphPageContent_colCheckbox');
                    if (checkAllTh) {
                        const checkbox = checkAllTh.querySelector('input[type="checkbox"]');
                        if (checkbox && !checkbox.checked) {
                            checkbox.click();
                        }
                    } else {
                        // Fallback generic checkbox check over inputs
                        const allCheckboxes = Array.from(document.querySelectorAll('td input[type="checkbox"]'));
                        allCheckboxes.forEach(cb => {
                            if (!cb.checked && !cb.disabled && !cb.readOnly) {
                                cb.click();
                            }
                        });
                    }
                }""")

                print('Submitting Attendance Form...')
                save_btn_id = '#ctl00_cphPageContent_btnSaveStudentRollNoList'
                submit_btn = page.locator(save_btn_id)

                if await submit_btn.count() == 0:
                    submit_btn_xpath = "xpath=//input[contains(translate(@value, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit') or contains(translate(@value, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'save')] | //button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit') or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'save')]"
                    submit_btn = page.locator(submit_btn_xpath)

                if await submit_btn.count() > 0:
                    try:
                        async with page.expect_navigation(wait_until='domcontentloaded', timeout=15000):
                            await submit_btn.first.click()
                    except Exception:
                        print("Click executed, navigation either timed out or resolved via AJAX.")

                    print('Attendance successfully saved to the portal.')
                    slots_processed += 1

                    print('Returning to dashboard to check for more pending slots...')
                    await page.goto('https://ums.paruluniversity.ac.in/AdminPanel/TMT_Timetable/TTM_Attendance/TTM_AttendanceStaffDashboard.aspx', wait_until='domcontentloaded')
                else:
                    print('WARNING: Could not find Submit/Save button automatically. Breaking loop to prevent infinite retry.')
                    has_more_pending_slots = False

            else:
                print('No more pending slots found for today. Dumping HTML for analysis...')
                try:
                    html_content = await page.content()
                    with open('timetable_debug.html', 'w', encoding='utf-8') as f:
                        f.write(html_content)
                    print('Saved timetable_debug.html to project folder.')
                except Exception:
                    pass
                has_more_pending_slots = False

        except Exception as err:
            print(f'Could not find the timetable container or an error occurred. {err}')
            has_more_pending_slots = False
            try:
                html_content = await page.content()
                with open('error_dump.html', 'w', encoding='utf-8') as f:
                    f.write(html_content)
                await page.screenshot(path='error_screenshot.png')
                print('Saved error_dump.html and error_screenshot.png to project folder for debugging.')
            except Exception:
                pass

    print(f"Attendance mapping complete. Total slots processed: {slots_processed}. Finishing execution flow.")
