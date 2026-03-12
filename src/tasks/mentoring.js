const fs = require('fs');
const path = require('path');

/**
 * Helper to add minutes to a date string in "DD-MM-YYYY hh:mm A" format
 */
function addMinutesToDateStr(dateStr, minutesToAdd) {
    // Parse "12-03-2026 01:33 PM"
    const regex = /(\d{2})-(\d{2})-(\d{4}) (\d{2}):(\d{2}) (AM|PM)/i;
    const match = dateStr.match(regex);
    if (!match) return dateStr;

    let [_, dd, mm, yyyy, hh, min, ampm] = match;
    let hour = parseInt(hh, 10);
    if (ampm.toUpperCase() === 'PM' && hour < 12) hour += 12;
    if (ampm.toUpperCase() === 'AM' && hour === 12) hour = 0;

    let dateObj = new Date(parseInt(yyyy, 10), parseInt(mm, 10) - 1, parseInt(dd, 10), hour, parseInt(min, 10));
    dateObj.setMinutes(dateObj.getMinutes() + minutesToAdd);

    // Format back to "DD-MM-YYYY hh:mm A"
    const outDd = String(dateObj.getDate()).padStart(2, '0');
    const outMm = String(dateObj.getMonth() + 1).padStart(2, '0');
    const outYyyy = dateObj.getFullYear();

    let outHour = dateObj.getHours();
    const outAmpm = outHour >= 12 ? 'PM' : 'AM';
    outHour = outHour % 12;
    outHour = outHour ? outHour : 12; // the hour '0' should be '12'
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
 * Helper to set value of an element evaluated in browser context via XPath
 * @param {import('puppeteer').Page} page
 * @param {string} xpath 
 * @param {string} value 
 * @param {string} type 'select' or 'input' or 'textarea'
 */
async function setValueByXPath(page, xpath, value, type) {
    await page.evaluate((xp, val, elementType) => {
        const node = document.evaluate(xp, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
        if (node) {
            if (elementType === 'select') {
                // For selects we might need to match option text if value isn't obvious, but let's try direct value first, 
                // or find by text if value fails.
                let optionFound = false;
                for (let opt of node.options) {
                    if (opt.text.toLowerCase().includes(val.toLowerCase()) || opt.value === val) {
                        node.value = opt.value;
                        optionFound = true;
                        break;
                    }
                }
                if (optionFound) {
                    if (typeof jQuery !== 'undefined') $(node).trigger('change');
                    node.dispatchEvent(new Event('change', { bubbles: true }));
                }
            } else {
                node.value = val;
                node.dispatchEvent(new Event('input', { bubbles: true }));
                node.dispatchEvent(new Event('change', { bubbles: true }));
            }
        } else {
            console.warn(`Node not found for xpath: ${xp}`);
        }
    }, xpath, value, type);
}

/**
 * Executes the "Mentoring" task.
 */
async function execute(page) {
    // 0. Load Configuration
    const configPath = path.join(process.cwd(), 'mentoring.json');
    let config;
    try {
        config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    } catch (e) {
        throw new Error("Failed to load mentoring.json. Please ensure it exists in the root directory.");
    }

    // Prepare downloads folder
    const downloadPath = path.join(process.cwd(), 'downloads');
    if (!fs.existsSync(downloadPath)) {
        fs.mkdirSync(downloadPath);
    }

    // Set up Puppeteer CDPSession for downloads
    const client = await page.target().createCDPSession();
    await client.send('Page.setDownloadBehavior', {
        behavior: 'allow',
        downloadPath: downloadPath,
    });

    const listUrl = 'https://ums.paruluniversity.ac.in/AdminPanel/Mentoring/MEN_StudentMentor/MEN_StudentMentorListMentor.aspx';

    console.log('Navigating to Mentee List...');
    await page.goto(listUrl, { waitUntil: 'networkidle2' });

    const totalMentees = await page.evaluate(() => {
        const tableXPath = "/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[3]/div[2]/div/div/div[2]/div/div/div[1]/div/div[2]/table/tbody/tr";
        const iterator = document.evaluate(tableXPath, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
        return iterator.snapshotLength;
    });

    console.log(`Found ${totalMentees} mentees...`);

    let currentMeetingTime = config.startDate;

    for (let i = 1; i <= totalMentees; i++) {
        console.log(`\n--- Processing Mentee ${i} of ${totalMentees} ---`);

        if (i > 1 || page.url() !== listUrl) {
            await page.goto(listUrl, { waitUntil: 'networkidle2' });
        }

        // 1. Get Student ID, Enrollment No, and Student Name from target row
        const studentInfo = await page.evaluate((rowIndex) => {
            let idXPath = `/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[3]/div[2]/div/div/div[2]/div/div/div[1]/div/div[2]/table/tbody/tr[${rowIndex}]/td[4]/a`;
            let nameXPath = `/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[3]/div[2]/div/div/div[2]/div/div/div[1]/div/div[2]/table/tbody/tr[${rowIndex}]/td[5]/a`;

            let idNode = document.evaluate(idXPath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
            let nameNode = document.evaluate(nameXPath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;

            let studentId = null;
            let enrollmentNo = 'UnknownEnrollment';
            let studentName = 'UnknownName';

            if (idNode) {
                enrollmentNo = idNode.textContent.trim();
                const href = idNode.getAttribute('href') || '';
                const match = href.match(/StudentID=([^&]+)/i);
                if (match) studentId = match[1];
                else {
                    const clickMatch = (idNode.getAttribute('onclick') || '').match(/['"]([^'"]+)['"]/);
                    if (clickMatch) studentId = clickMatch[1];
                    else studentId = enrollmentNo;
                }
            }
            if (nameNode) studentName = nameNode.textContent.trim();

            return { studentId, enrollmentNo, studentName };
        }, i);

        console.log(`Target: ${studentInfo.enrollmentNo} - ${studentInfo.studentName}`);

        let gender = 'Unknown';
        let rawAttendance = 'Unknown';

        if (studentInfo.studentId) {
            const detailUrl = `https://ums.paruluniversity.ac.in/AdminPanel/Student/STU_Student/STU_StudentViewDetailed.aspx?StudentID=${studentInfo.studentId}`;
            await page.goto(detailUrl, { waitUntil: 'networkidle2' });

            gender = await page.evaluate(() => {
                const node = document.evaluate("/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[2]/div[4]/div/div/div[2]/div[1]/div/div[2]/div/div[2]/div/div[1]/div[2]/div[2]/span", document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                return node ? node.textContent.trim() : 'Unknown';
            });
            rawAttendance = await page.evaluate(() => {
                const node = document.evaluate("/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[2]/div[4]/div/div/div[2]/div[17]/div/div/div[2]/div/div/div[2]/div[2]/div/div/div/div[2]/table/tfoot/tr/td[5]/span", document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                return node ? node.textContent.trim() : '0.00';
            });
            console.log(`-> Gender: ${gender}, Attendance: ${rawAttendance}`);
        }

        await page.goto(listUrl, { waitUntil: 'networkidle2' });

        // Click Mentoring Meeting
        const clickedMeeting = await page.evaluate((rowIndex) => {
            const node = document.evaluate(`/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[3]/div[2]/div/div/div[2]/div/div/div[1]/div/div[2]/table/tbody/tr[${rowIndex}]/td[11]/a[2]`, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
            if (node) {
                node.click();
                return true;
            }
            return false;
        }, i);

        if (clickedMeeting) {
            await page.waitForNavigation({ waitUntil: 'networkidle2' }).catch(() => { });
            await new Promise(r => setTimeout(r, 2000));

            console.log('Filling out massive mentorship form...');

            // 0. Base Dropdown
            await setValueByXPath(page, "/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[2]/div[1]/div[2]/div/div[1]/div[2]/div[3]/div/select", "Group-1 - PIP", "select");
            await new Promise(r => setTimeout(r, 500)); // allow ASP.NET postback binding if any

            // 1. Date/Time
            await setValueByXPath(page, "/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[2]/div[3]/div[4]/div/div/div/div[2]/div/div[1]/div/div/input", currentMeetingTime, "input");

            // 2. Agenda
            await setValueByXPath(page, "/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[2]/div[3]/div[4]/div/div/div/div[2]/div/div[3]/div/textarea", config.agenda, "textarea");

            // 3. Stress Level (Dropdown)
            await setValueByXPath(page, "/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[2]/div[3]/div[4]/div/div/div/div[2]/div/div[4]/div[1]/div[1]/div/select", pickRandom(['Mild', 'No Stress']), "select");

            // 4. Learner Type (Input)
            await setValueByXPath(page, "/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[2]/div[3]/div[4]/div/div/div/div[2]/div/div[4]/div[1]/div[2]/div/input", pickRandom(['Average', 'Fast']), "input");

            // 5. Issues Discussed
            await setValueByXPath(page, "/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[2]/div[3]/div[4]/div/div/div/div[2]/div/div[4]/div[2]/div/textarea", config.issuesDiscussed, "textarea");

            // 6. Mentor's Opinion
            await setValueByXPath(page, "/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[2]/div[3]/div[4]/div/div/div/div[2]/div/div[4]/div[8]/div/textarea", config.mentorsOpinion, "textarea");

            // 7. Academic Category
            await setValueByXPath(page, "/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[2]/div[4]/div[2]/div/ol/li[1]/select", "Advanced Learner", "select");

            // 8. Personality Attributes
            await setValueByXPath(page, "/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[2]/div[4]/div[2]/div/ol/li[2]/textarea", pickRandom(["Good moral sense", "Positive attitude", "Friendly and collaborative"]), "textarea");

            // 9. Grievances
            await setValueByXPath(page, "/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[2]/div[4]/div[2]/div/ol/li[3]/textarea", "No", "textarea");

            // 10. Interests Based on Gender
            let interests = "Music";
            const g = gender.toLowerCase();
            if (g.includes('male') && !g.includes('female')) {
                interests = pickRandom(["Cricket", "Football", "Dance", "Music"]);
            } else if (g.includes('female')) {
                interests = pickRandom(["Music", "Cycling", "Tennis", "Dance"]);
            }
            await setValueByXPath(page, "/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[2]/div[4]/div[2]/div/ol/li[4]/textarea", interests, "textarea");

            // 11. Attendance
            // Generate a random float between 80.00 and 93.00 if missing, else just use actual
            let attToEnter = rawAttendance;
            if (isNaN(parseFloat(attToEnter)) || parseFloat(attToEnter) < 1) {
                attToEnter = (Math.random() * (93 - 80) + 80).toFixed(2);
            }
            await setValueByXPath(page, "/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[2]/div[4]/div[2]/div/ol/li[5]/textarea", `${attToEnter}%`, "textarea");

            // 12. Difficulties in Subjects
            await setValueByXPath(page, "/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[2]/div[4]/div[2]/div/ol/li[6]/textarea", "No", "textarea");

            // Determine Perf based on Attendance roughly
            let perf = "Good";
            let fAtt = parseFloat(attToEnter.replace('%', ''));
            if (fAtt > 90) perf = "Excellent";
            else if (fAtt > 85) perf = "Very good";

            // 13. Study Performance
            await setValueByXPath(page, "/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[2]/div[4]/div[2]/div/ol/li[7]/textarea", perf, "textarea");

            // 14. Performance in Exam
            await setValueByXPath(page, "/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[2]/div[4]/div[2]/div/ol/li[8]/select", perf, "select");

            // 15. Communication Problem
            await setValueByXPath(page, "/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[2]/div[4]/div[2]/div/ol/li[9]/select", "No", "select");

            // 16. Suggestions to Mentee
            await setValueByXPath(page, "/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[2]/div[4]/div[2]/div/ol/li[10]/textarea", config.suggestions, "textarea");

            // Increment time for NEXT student
            currentMeetingTime = addMinutesToDateStr(currentMeetingTime, 10);

            // SAVE FORM
            console.log('Submitting the Mentoring Meeting form...');
            await page.evaluate(() => {
                const node = document.evaluate("/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[2]/div[4]/div[3]/div/div/input", document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                if (node) node.click();
            });
            await new Promise(r => setTimeout(r, 4000));

            // Navigate to Student Mentoring Info page to initiate DOWNLOAD
            const mentoringInfoUrl = `https://ums.paruluniversity.ac.in/AdminPanel/Mentoring/MEN_StudentMentoring/MEN_StudentMentoringInfo.aspx?StudentID=${studentInfo.studentId}`;
            console.log(`Navigating to Mentoring Info page to download report: ${mentoringInfoUrl}`);
            await page.goto(mentoringInfoUrl, { waitUntil: 'networkidle2' });

            console.log('Triggering PDF Download from the recent mentoring table...');

            // Get current files in download dir
            const initialFiles = fs.readdirSync(downloadPath);

            // Click download button (1st row, 7th column, 1st anchor tag)
            const downloadClicked = await page.evaluate(() => {
                const xpath = "/html/body/form/div[5]/div[2]/div/div/div[2]/div/div[3]/div[2]/div[2]/div/div[2]/div/div[2]/div[2]/div/div/div/div[2]/table/tbody/tr[1]/td[7]/a[1]";
                const node = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                if (node) {
                    node.click();
                    return true;
                }
                return false;
            });

            if (downloadClicked) {
                // Polling for the new file to appear and finish downloading
                let downloadedFile = null;
                let retries = 0;
                while (retries < 20) {
                    await new Promise(r => setTimeout(r, 1000));
                    const currentFiles = fs.readdirSync(downloadPath);
                    const newFiles = currentFiles.filter(f => !initialFiles.includes(f));

                    if (newFiles.length > 0) {
                        const candidate = newFiles[0];
                        // Ensure it's not a temp chrome download file ending in .crdownload
                        if (!candidate.endsWith('.crdownload')) {
                            downloadedFile = candidate;
                            break;
                        }
                    }
                    retries++;
                }

                if (downloadedFile) {
                    // Rename file
                    // Make name filesystem safe
                    const safeName = studentInfo.studentName.replace(/[^a-z0-9]/gi, '_').toLowerCase();
                    const newFileName = `${studentInfo.enrollmentNo}-${safeName}.pdf`;

                    const oldPath = path.join(downloadPath, downloadedFile);
                    const newPath = path.join(downloadPath, newFileName);

                    fs.renameSync(oldPath, newPath);
                    console.log(`Successfully downloaded and renamed: ${newFileName}`);
                } else {
                    console.warn('PDF download timed out or failed to trigger.');
                }
            } else {
                console.warn('Could not find the download button on the Mentoring Info page. Skipped downloading.');
            }

        } else {
            console.warn('Could not find Meeting Action Button.');
        }
    }

    console.log('\n--- Extensive Mentoring Task Complete ---');
}

module.exports = {
    execute
};
