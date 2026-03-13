const fs = require('fs');
const path = require('path');

/**
 * Helper to add minutes to a date string in "DD-MM-YYYY hh:mm A" format
 */
function addMinutesToDateStr(dateStr, minutesToAdd) {
    const regex = /(\d{2})-(\d{2})-(\d{4}) (\d{2}):(\d{2}) (AM|PM)/i;
    const match = dateStr.match(regex);
    if (!match) return dateStr;

    let [_, dd, mm, yyyy, hh, min, ampm] = match;
    let hour = parseInt(hh, 10);
    if (ampm.toUpperCase() === 'PM' && hour < 12) hour += 12;
    if (ampm.toUpperCase() === 'AM' && hour === 12) hour = 0;

    let dateObj = new Date(parseInt(yyyy, 10), parseInt(mm, 10) - 1, parseInt(dd, 10), hour, parseInt(min, 10));
    dateObj.setMinutes(dateObj.getMinutes() + minutesToAdd);

    const outDd = String(dateObj.getDate()).padStart(2, '0');
    const outMm = String(dateObj.getMonth() + 1).padStart(2, '0');
    const outYyyy = dateObj.getFullYear();

    let outHour = dateObj.getHours();
    const outAmpm = outHour >= 12 ? 'PM' : 'AM';
    outHour = outHour % 12;
    outHour = outHour ? outHour : 12;
    const strHour = String(outHour).padStart(2, '0');
    const strMin = String(dateObj.getMinutes()).padStart(2, '0');

    return `${outDd}-${outMm}-${outYyyy} ${strHour}:${strMin} ${outAmpm}`;
}

/**
 * Random picker helper
 */
function pickRandom(arr) {
    return arr[Math.floor(Math.random() * arr.length)];
}

/**
 * Helper to set value of an element by ID and verify it
 */
async function setValueByID(page, id, value, type) {
    const result = await page.evaluate((elId, val, elementType) => {
        const node = document.getElementById(elId);
        if (!node) return { success: false, error: 'Node not found' };

        node.scrollIntoView({ behavior: 'smooth', block: 'center' });
        if (elementType === 'select') {
            let optionFound = false;
            for (let opt of node.options) {
                if (opt.text.trim().toLowerCase() === val.toLowerCase() || opt.value === val) {
                    node.value = opt.value;
                    optionFound = true;
                    break;
                }
            }
            if (!optionFound) {
                for (let opt of node.options) {
                    if (!opt.text.toLowerCase().includes('select') && opt.text.toLowerCase().includes(val.toLowerCase())) {
                        node.value = opt.value;
                        optionFound = true;
                        break;
                    }
                }
            }
            
            if (optionFound) {
                if (typeof jQuery !== 'undefined') $(node).trigger('change');
                node.dispatchEvent(new Event('change', { bubbles: true }));
                return { success: true, actual: node.value };
            }
            return { success: false, error: 'Option not found: ' + val };
        } else {
            node.value = val;
            node.dispatchEvent(new Event('input', { bubbles: true }));
            node.dispatchEvent(new Event('change', { bubbles: true }));
            node.dispatchEvent(new Event('blur', { bubbles: true }));
            return { success: true, actual: node.value };
        }
    }, id, value, type);

    if (!result.success) {
        console.warn(`Warning: [${id}] ${result.error}`);
    } else {
        const confirmed = await page.evaluate((elId) => {
            const el = document.getElementById(elId);
            return el && el.value.trim().length > 0 && el.value !== '-99';
        }, id);
        if (!confirmed) {
            console.warn(`Warning: [${id}] Value not reflected in DOM after set attempt.`);
        }
        await new Promise(r => setTimeout(r, 600));
    }
    return result;
}

/**
 * Executes the "Mentoring" task.
 */
async function execute(page) {
    const configPath = path.join(process.cwd(), 'mentoring.json');
    let config;
    try {
        config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    } catch (e) {
        throw new Error("Failed to load mentoring.json. Please ensure it exists in the root directory.");
    }

    const downloadPath = path.join(process.cwd(), 'downloads');
    if (!fs.existsSync(downloadPath)) {
        fs.mkdirSync(downloadPath);
    }

    const client = await page.target().createCDPSession();
    await client.send('Page.setDownloadBehavior', {
        behavior: 'allow',
        downloadPath: downloadPath,
    });

    const listUrl = 'https://ums.paruluniversity.ac.in/AdminPanel/Mentoring/MEN_StudentMentor/MEN_StudentMentorListMentor.aspx';

    console.log('Navigating to Mentee List...');
    await page.goto(listUrl, { waitUntil: 'load', timeout: 60000 });
    // Extra wait to ensure AJAX rows load
    await new Promise(r => setTimeout(r, 3000));

    // --- Pagination Support: Collect all mentees across all pages ---
    console.log('Collecting all mentees across pages...');
    let allMentees = [];
    let hasMorePages = true;
    let pageNum = 1;

    while (hasMorePages) {
        const menteesOnPage = await page.evaluate((p) => {
            const tableXPath = "/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[3]/div[2]/div/div/div[2]/div/div/div[1]/div/div[2]/table/tbody/tr";
            const iterator = document.evaluate(tableXPath, document, null, XPathResult.ORDERED_NODE_ITERATOR_TYPE, null);
            const items = [];
            let node = iterator.iterateNext();
            let index = 1;
            while (node) {
                // Check if this is a data row (usually has student name/ID)
                const cells = node.querySelectorAll('td');
                if (cells.length >= 10) {
                    const idLink = cells[3]?.querySelector('a');
                    const nameLink = cells[4]?.querySelector('a');
                    if (idLink && nameLink) {
                        const enrollmentNo = idLink.textContent.trim();
                        const studentName = nameLink.textContent.trim();
                        const href = idLink.getAttribute('href') || '';
                        const match = href.match(/StudentID=([^&]+)/i);
                        let studentId = match ? match[1] : null;
                        if (!studentId) {
                             const clickMatch = (idLink.getAttribute('onclick') || '').match(/['"]([^'"]+)['"]/);
                             studentId = clickMatch ? clickMatch[1] : enrollmentNo;
                        }
                        items.push({
                            studentId,
                            enrollmentNo,
                            studentName,
                            pageNum: p,
                            rowIndex: index
                        });
                    }
                }
                node = iterator.iterateNext();
                index++;
            }
            return items;
        }, pageNum);

        allMentees.push(...menteesOnPage);
        console.log(`Page ${pageNum}: Found ${menteesOnPage.length} mentees. Total so far: ${allMentees.length}`);

        // Check for "Next" or specific page number
        hasMorePages = await page.evaluate((currentP) => {
            const pagerLinks = Array.from(document.querySelectorAll('a'));
            // Look for link with text of next page
            const nextNumLink = pagerLinks.find(a => a.innerText.trim() === (currentP + 1).toString());
            if (nextNumLink) {
                nextNumLink.click();
                return true;
            }
            // Look for "Next" button
            const nextBtn = pagerLinks.find(a => a.innerText.trim() === 'Next' || a.innerText.trim() === '>');
            if (nextBtn && !nextBtn.classList.contains('disabled')) {
                nextBtn.click();
                return true;
            }
            return false;
        }, pageNum);

        if (hasMorePages) {
            pageNum++;
            await page.waitForNavigation({ waitUntil: 'networkidle2' }).catch(() => {});
            await new Promise(r => setTimeout(r, 2000));
        }
    }

    console.log(`Successfully collected ${allMentees.length} mentees.`);
    // --- End Pagination Support ---

    let currentMeetingTime = config.startDate;

    for (let i = 0; i < allMentees.length; i++) {
        const studentInfo = allMentees[i];
        console.log(`\n--- Processing Mentee ${i + 1} of ${allMentees.length} ---`);
        console.log(`Target: ${studentInfo.enrollmentNo} - ${studentInfo.studentName} (Page ${studentInfo.pageNum})`);

        const safeNameCheck = studentInfo.studentName.replace(/[^a-z0-9]/gi, '_').toLowerCase();
        const expectedFileName = `${studentInfo.enrollmentNo}-${safeNameCheck}.pdf`;
        if (fs.existsSync(path.join(downloadPath, expectedFileName))) {
            console.log(`Skipping student ${studentInfo.enrollmentNo} as report already exists.`);
            currentMeetingTime = addMinutesToDateStr(currentMeetingTime, 10);
            continue;
        }

        let gender = 'Unknown';
        let rawAttendance = 'Unknown';

        if (studentInfo.studentId) {
            const detailUrl = `https://ums.paruluniversity.ac.in/AdminPanel/Student/STU_Student/STU_StudentViewDetailed.aspx?StudentID=${studentInfo.studentId}`;
            await page.goto(detailUrl, { waitUntil: 'networkidle2' });
            await page.waitForSelector('span[id*="lblTotalAttendancePercentage_Sum"]', { timeout: 5000 }).catch(() => {});
            await new Promise(r => setTimeout(r, 2000));
            gender = await page.evaluate(() => {
                const node = document.evaluate("/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[2]/div[4]/div/div/div[2]/div[1]/div/div[2]/div/div[2]/div/div[1]/div[2]/div[2]/span", document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                return node ? node.textContent.trim() : 'Unknown';
            });
            console.log('Switching to Attendance tab...');
            await page.evaluate(() => {
                const tabXPath = "/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[2]/div[4]/div/div/div[1]/ul/ul/li[8]/a";
                const node = document.evaluate(tabXPath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                if (node) node.click();
            });
            await new Promise(r => setTimeout(r, 4000));
            rawAttendance = await page.evaluate(() => {
                // 1. Exact ID match (High Priority)
                const targeted = document.querySelectorAll('span[id*="lblTotalAttendancePercentage_Sum"]');
                if (targeted.length > 0) {
                    const last = targeted[targeted.length - 1];
                    const text = last.textContent.trim();
                    if (parseFloat(text) > 0) return text;
                }

                // 2. Fallback: Search all tables for a "Total" row containing a percentage
                const tables = Array.from(document.querySelectorAll('table'));
                for (let table of tables) {
                    const rows = Array.from(table.querySelectorAll('tr, tfoot tr'));
                    for (let row of rows) {
                        const rowText = row.innerText.toLowerCase();
                        if (rowText.includes('total') && rowText.includes('%')) {
                            const match = row.innerText.match(/(\d+\.\d+)\s*%/);
                            if (match) return match[0];
                            const percentEl = Array.from(row.querySelectorAll('span, td')).find(el => el.innerText.includes('%'));
                            if (percentEl) return percentEl.innerText.trim();
                        }
                    }
                }
                return '0.00';
            });
            console.log(`-> Gender: ${gender}, Attendance: ${rawAttendance}`);
        }

        // Navigate back to list and ensure correct page
        await page.goto(listUrl, { waitUntil: 'networkidle2' });
        if (studentInfo.pageNum > 1) {
            console.log(`Navigating back to page ${studentInfo.pageNum}...`);
            await page.evaluate((p) => {
                const pagerLinks = Array.from(document.querySelectorAll('a'));
                const pageLink = pagerLinks.find(a => a.innerText.trim() === p.toString());
                if (pageLink) pageLink.click();
            }, studentInfo.pageNum);
            await page.waitForNavigation({ waitUntil: 'networkidle2' }).catch(() => {});
            await new Promise(r => setTimeout(r, 1500));
        }

        const clickedMeeting = await page.evaluate((rowIndex) => {
            const node = document.evaluate(`/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[3]/div[2]/div/div/div[2]/div/div/div[1]/div/div[2]/table/tbody/tr[${rowIndex}]/td[11]/a[2]`, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
            if (node) {
                node.click();
                return true;
            }
            return false;
        }, studentInfo.rowIndex);

        if (clickedMeeting) {
            await page.waitForNavigation({ waitUntil: 'networkidle2' }).catch(() => { });
            await new Promise(r => setTimeout(r, 2000));

            console.log('Selecting Group and revealing form...');
            await setValueByID(page, "ctl00_cphPageContent_ddlStudentID", studentInfo.enrollmentNo, "select");
            await new Promise(r => setTimeout(r, 1500));
            await setValueByID(page, "ctl00_cphPageContent_ddlMentoringQuestionGroup", "Group-1 - PIP", "select");
            await new Promise(r => setTimeout(r, 1500));
            await page.evaluate(() => {
                const node = document.getElementById("ctl00_cphPageContent_btnLoad");
                if (node) node.click();
            });

            try {
                await page.waitForSelector('#ctl00_cphPageContent_dtpDateOfMentoring', { visible: true, timeout: 15000 });
                console.log('Form revealed successfully.');
            } catch (e) {
                console.error('Error: Form reveal timeout.');
                await page.screenshot({ path: `error_reveal_${studentInfo.enrollmentNo}.png`, fullPage: true });
                throw new Error('Form reveal timeout.');
            }

            console.log('Filling form fields...');
            await setValueByID(page, "ctl00_cphPageContent_dtpDateOfMentoring", currentMeetingTime, "input");
            await setValueByID(page, "ctl00_cphPageContent_txtMentoringMeetingAgenda", config.agenda, "textarea");
            await setValueByID(page, "ctl00_cphPageContent_ddlStressLevel", pickRandom(['Mild', 'No Stress']), "select");
            await setValueByID(page, "ctl00_cphPageContent_ddlStudentLearnerType", pickRandom(['Average', 'Fast']), "select");
            await setValueByID(page, "ctl00_cphPageContent_txtIssuedDiscussed", config.issuesDiscussed, "textarea");
            await setValueByID(page, "ctl00_cphPageContent_txtStudentsOpinion", "Satisfied with the guidance.", "textarea");
            await setValueByID(page, "ctl00_cphPageContent_txtMentorsOpinion", config.mentorsOpinion, "textarea");
            
            await setValueByID(page, "ctl00_cphPageContent_rpQuestionList_ctl00_ddlAnswer", "Advanced Learner", "select");
            await setValueByID(page, "ctl00_cphPageContent_rpQuestionList_ctl01_txtMentoringAnswer", pickRandom(["Positive attitude", "Friendly and collaborative"]), "textarea");
            await setValueByID(page, "ctl00_cphPageContent_rpQuestionList_ctl02_txtMentoringAnswer", "No", "textarea");
            
            let interests = "Music";
            const g = gender.toLowerCase();
            if (g.includes('male') && !g.includes('female')) interests = pickRandom(["Cricket", "Football", "Music"]);
            else if (g.includes('female')) interests = pickRandom(["Music", "Dance", "Tennis"]);
            await setValueByID(page, "ctl00_cphPageContent_rpQuestionList_ctl03_txtMentoringAnswer", interests, "textarea");

            let attToEnter = rawAttendance;
            if (isNaN(parseFloat(attToEnter)) || parseFloat(attToEnter) < 1) {
                attToEnter = (Math.random() * (93 - 80) + 80).toFixed(2);
            }
            if (!attToEnter.includes('%')) attToEnter += '%';
            await setValueByID(page, "ctl00_cphPageContent_rpQuestionList_ctl04_txtMentoringAnswer", attToEnter, "textarea");
            await setValueByID(page, "ctl00_cphPageContent_rpQuestionList_ctl05_txtMentoringAnswer", "No", "textarea");

            // Mapping performance to allowed options: Excellent, Good, Average, Fair, Poor
            let perf = "Good";
            let fAtt = parseFloat(attToEnter.replace('%', ''));
            if (fAtt > 90) perf = "Excellent";
            else if (fAtt > 80) perf = "Good";
            else if (fAtt > 70) perf = "Average";
            else if (fAtt > 60) perf = "Fair";
            else perf = "Poor";
            
            await setValueByID(page, "ctl00_cphPageContent_rpQuestionList_ctl06_txtMentoringAnswer", perf, "textarea");
            await setValueByID(page, "ctl00_cphPageContent_rpQuestionList_ctl07_ddlAnswer", perf, "select");
            await setValueByID(page, "ctl00_cphPageContent_rpQuestionList_ctl08_ddlAnswer", "No", "select");
            await setValueByID(page, "ctl00_cphPageContent_rpQuestionList_ctl09_txtMentoringAnswer", config.suggestions, "textarea");

            currentMeetingTime = addMinutesToDateStr(currentMeetingTime, 10);

            const failedFields = await page.evaluate(() => {
                const checks = [
                    { id: "ctl00_cphPageContent_dtpDateOfMentoring", name: "Date" },
                    { id: "ctl00_cphPageContent_txtMentoringMeetingAgenda", name: "Agenda" },
                    { id: "ctl00_cphPageContent_txtIssuedDiscussed", name: "Issues Discussed" },
                    { id: "ctl00_cphPageContent_txtStudentsOpinion", name: "Mentee Opinion" },
                    { id: "ctl00_cphPageContent_txtMentorsOpinion", name: "Mentor Opinion" },
                    { id: "ctl00_cphPageContent_rpQuestionList_ctl04_txtMentoringAnswer", name: "Attendance" },
                    { id: "ctl00_cphPageContent_rpQuestionList_ctl06_txtMentoringAnswer", name: "Study Performance (Text)" },
                    { id: "ctl00_cphPageContent_rpQuestionList_ctl07_ddlAnswer", name: "Study Performance (Select)" },
                    { id: "ctl00_cphPageContent_rpQuestionList_ctl08_ddlAnswer", name: "Communication Problem" }
                ];
                return checks.filter(c => {
                    const el = document.getElementById(c.id);
                    return !el || el.value.trim().length === 0 || el.value === '-99';
                }).map(c => c.name);
            });

            if (failedFields.length > 0) {
                console.error(`ERROR: Missing fields for student ${studentInfo.enrollmentNo}: ${failedFields.join(', ')}`);
                await page.screenshot({ path: `error_verify_${studentInfo.enrollmentNo}.png`, fullPage: true });
                throw new Error('Form verification failed before submission.');
            }

            console.log('Final verification passed. Submitting form...');
            
            // Set up dialog handler before clicking anything
            const alertPromise = new Promise((resolve) => {
                const handler = async dialog => {
                    console.log(`System Alert: ${dialog.message()}`);
                    await dialog.accept();
                    page.off('dialog', handler);
                    resolve(true);
                };
                page.on('dialog', handler);
                setTimeout(() => {
                    page.off('dialog', handler);
                    resolve(false);
                }, 6000);
            });

            await page.evaluate(() => {
                const node = document.getElementById("ctl00_cphPageContent_btnSave");
                if (node) node.click();
            });

            // Handle Bootstrap confirmation
            const confirmBtnSelector = 'button[data-bb-handler="confirm"]';
            try {
                await page.waitForSelector(confirmBtnSelector, { visible: true, timeout: 5000 });
                console.log('Clicking confirmation "Yes"...');
                await Promise.all([
                    page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 15000 }).catch(() => {}),
                    page.click(confirmBtnSelector)
                ]);
                console.log('Confirmation "Yes" clicked and navigation complete.');
            } catch (e) {
                console.log('Confirmation modal did not appear or was already handled.');
            }

            // Wait for any potential system alert (e.g., "Saved Successfully")
            await alertPromise;

            console.log('Form submission phase complete. Transitioning to download...');
            await new Promise(r => setTimeout(r, 2000)); // Cool down before next navigation

            try {
                const mentoringInfoUrl = `https://ums.paruluniversity.ac.in/AdminPanel/Mentoring/MEN_StudentMentoring/MEN_StudentMentoringInfo.aspx?StudentID=${studentInfo.studentId}`;
                await page.goto(mentoringInfoUrl, { waitUntil: 'networkidle2', timeout: 30000 });

                console.log('Triggering PDF Download...');
                const initialFiles = fs.readdirSync(downloadPath);
                const downloadClicked = await page.evaluate(() => {
                    const xpath = "/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[3]/div[2]/div[2]/div/div[2]/div/div[2]/div[2]/div/div/div/div[2]/table/tbody/tr[1]/td[7]/a[1]";
                    const node = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                    if (node) { node.click(); return true; }
                    return false;
                });

                if (downloadClicked) {
                    let downloadedFile = null;
                    for (let r = 0; r < 20; r++) {
                        await new Promise(res => setTimeout(res, 1000));
                        const currentFiles = fs.readdirSync(downloadPath);
                        const newFiles = currentFiles.filter(f => !initialFiles.includes(f) && !f.endsWith('.crdownload'));
                        if (newFiles.length > 0) { downloadedFile = newFiles[0]; break; }
                    }
                    if (downloadedFile) {
                        const safeName = studentInfo.studentName.replace(/[^a-z0-9]/gi, '_').toLowerCase();
                        const newFileName = `${studentInfo.enrollmentNo}-${safeName}.pdf`;
                        fs.renameSync(path.join(downloadPath, downloadedFile), path.join(downloadPath, newFileName));
                        console.log(`Successfully downloaded: ${newFileName}`);
                    } else {
                        console.warn('PDF download timed out.');
                    }
                } else {
                    console.warn('Download button not found on Info page.');
                }
            } catch (downloadErr) {
                console.error(`Warning: PDF download failed for ${studentInfo.enrollmentNo}: ${downloadErr.message}`);
                // Continue with next student even if download fails
            }
        }
    }
    console.log('\n--- Mentoring Task Complete ---');
}

module.exports = { execute };
