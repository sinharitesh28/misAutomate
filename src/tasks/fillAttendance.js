/**
 * Executes the "Fill Attendance" task.
 * Navigates to the timetable, checks for pending slots, maps dropdowns,
 * and recursively fills attendance.
 * 
 * @param {import('puppeteer').Page} page - The authenticated Puppeteer page.
 */
async function execute(page) {
    // Navigate directly to the attendance page
    console.log('Navigating directly to TTM_AttendanceStaffDashboard.aspx...');
    await page.goto('https://ums.paruluniversity.ac.in/AdminPanel/TMT_Timetable/TTM_Attendance/TTM_AttendanceStaffDashboard.aspx', { waitUntil: 'networkidle2' });
    console.log('Reached Attendance Dashboard.');

    const timeTableContainerId = '#ctl00_cphPageContent_divTimeTable';
    let hasMorePendingSlots = true;
    let slotsProcessed = 0;

    while (hasMorePendingSlots) {
        console.log(`Looking for Pending Attendance Slots for today... (Processed so far: ${slotsProcessed})`);

        try {
            await page.waitForSelector(timeTableContainerId, { visible: true, timeout: 15000 });
            console.log("Timetable container found. Analysing today's column for pending slots...");

            // Evaluate the timetable to find slots
            const pendingSlotFound = await page.evaluate(() => {
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
            });

            if (pendingSlotFound) {
                console.log('Successfully clicked a pending slot. Waiting for the attendance list to load...');
                await page.waitForNavigation({ waitUntil: 'networkidle2' }).catch(() => console.log('Navigation resolved or loaded via AJAX.'));
                await new Promise(r => setTimeout(r, 4000)); // Additional wait for AJAX tables to populate

                console.log('Filling out form dropdowns (Planning, Status, Domain)...');

                // 1. Planning (Multiselect dropdown button)
                try {
                    console.log('Selecting Planning (1st option)...');
                    const planningDropdownBtn = await page.$('button.multiselect.dropdown-toggle');
                    if (planningDropdownBtn) {
                        await planningDropdownBtn.click();
                        await new Promise(r => setTimeout(r, 500)); // wait for animation

                        // Select the first real option (usually index 1)
                        const firstOption = await page.$('ul.multiselect-container li:not(.multiselect-item):not(.active) a label input[type="radio"], ul.multiselect-container li:not(.multiselect-item):not(.active) a label input[type="checkbox"]');
                        if (firstOption) {
                            await firstOption.click();
                        } else {
                            const firstLi = await page.$('ul.multiselect-container li:not(.multiselect-item):not(.active) a');
                            if (firstLi) await firstLi.click();
                        }

                        await planningDropdownBtn.click();
                    } else {
                        console.log('Planning multiselect dropdown button not found.');
                    }
                } catch (e) { console.log('Error selecting Planning:', e.message); }

                // 2. Planning Status -> "Partially Completed"
                try {
                    console.log('Selecting Planning Status (Partially Completed)...');
                    await page.evaluate(() => {
                        const statusSelect = document.querySelector('select[id*="ddlLessonPlanningStatusID"]');
                        if (statusSelect) {
                            const opt = Array.from(statusSelect.options).find(o => o.text.toLowerCase().includes('partially'));
                            if (opt && statusSelect.value !== opt.value) {
                                statusSelect.value = opt.value;
                                if (typeof jQuery !== 'undefined') $(statusSelect).trigger('change');
                                statusSelect.dispatchEvent(new Event('change', { bubbles: true }));
                            }
                        }
                    });
                } catch (e) { console.log('Error selecting Planning Status:', e.message); }

                // 3. Targeted Learning Domain -> "Cognitive"
                try {
                    console.log('Selecting Targeted Learning Domain (Cognitive)...');
                    await page.evaluate(() => {
                        const domainSelect = document.querySelector('#ctl00_cphPageContent_ddlClassMode');
                        if (domainSelect) {
                            const opt = Array.from(domainSelect.options).find(o => o.value === 'Cognitive' || o.text.toLowerCase().includes('cognitive'));
                            if (opt && domainSelect.value !== opt.value) {
                                domainSelect.value = opt.value;
                                if (typeof jQuery !== 'undefined') $(domainSelect).trigger('change');
                                domainSelect.dispatchEvent(new Event('change', { bubbles: true }));
                            }
                        }
                    });
                } catch (e) { console.log('Error selecting Targeted Learning Domain:', e.message); }

                console.log('Marking attendance (Checking all boxes)...');
                await page.evaluate(() => {
                    // Check the <th> id - ctl00_cphPageContent_colCheckbox
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
                });

                console.log('Submitting Attendance Form...');
                const saveBtnId = '#ctl00_cphPageContent_btnSaveStudentRollNoList';
                let submitBtn = await page.$(saveBtnId);

                // Fallback to old xpath approach if specific ID is not found somehow
                if (!submitBtn) {
                    const submitBtnXPath = "::-p-xpath(//input[contains(translate(@value, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit') or contains(translate(@value, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'save')] | //button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit') or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'save')])";
                    submitBtn = await page.$(submitBtnXPath);
                }
                if (submitBtn) {
                    await Promise.all([
                        page.waitForNavigation({ waitUntil: 'networkidle2' }).catch(() => { }),
                        submitBtn.click()
                    ]);
                    console.log('Attendance successfully saved to the portal.');
                    slotsProcessed++;

                    console.log('Returning to dashboard to check for more pending slots...');
                    await page.goto('https://ums.paruluniversity.ac.in/AdminPanel/TMT_Timetable/TTM_Attendance/TTM_AttendanceStaffDashboard.aspx', { waitUntil: 'networkidle2' });
                } else {
                    console.warn('Could not find Submit/Save button automatically. Breaking loop to prevent infinite retry.');
                    hasMorePendingSlots = false;
                }

            } else {
                console.log('No more pending slots found for today. Dumping HTML for analysis...');
                try {
                    require('fs').writeFileSync('timetable_debug.html', await page.content());
                    console.log('Saved timetable_debug.html to project folder.');
                } catch (dumpErr) { }
                hasMorePendingSlots = false;
            }
        } catch (err) {
            console.log('Could not find the timetable container or an error occurred.', err.message);
            hasMorePendingSlots = false;
            try {
                require('fs').writeFileSync('error_dump.html', await page.content());
                await page.screenshot({ path: 'error_screenshot.png' });
                console.log('Saved error_dump.html and error_screenshot.png to project folder for debugging.');
            } catch (dumpErr) { }
        }
    }

    console.log(`Attendance mapping complete. Total slots processed: ${slotsProcessed}. Finishing execution flow.`);
}

module.exports = {
    execute
};
