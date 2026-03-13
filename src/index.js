require('dotenv').config();
const BrowserHandler = require('./browser');
const { startSession } = require('./auth');

async function main() {
    const args = process.argv.slice(2);
    const taskName = args[0];

    if (!taskName) {
        console.error('Usage: node src/index.js <task-name>');
        console.error('Available tasks: fill-attendance');
        process.exit(1);
    }

    console.log(`Initializing Parul University Automation for task: [${taskName}]...`);

    let browserHandler = new BrowserHandler();
    try {
        // Initialize Browser (false = headful/visible for debugging)
        await browserHandler.init(false);

        // 1. Establish Authentication Session
        const { page } = await startSession(browserHandler);

        // 2. Route to specific task
        switch (taskName) {
            case 'fill-attendance':
                const fillAttendanceTask = require('./tasks/fillAttendance');
                await fillAttendanceTask.execute(page);
                break;
            case 'mentoring':
                const mentoringTask = require('./tasks/mentoring');
                await mentoringTask.execute(page);
                break;
            default:
                console.error(`Unknown task: ${taskName}`);
                console.error('Available tasks: fill-attendance, mentoring');
                break;
        }

        console.log(`Task [${taskName}] successfully completed.`);
        // Allow time to observe the final state
        await new Promise(r => setTimeout(r, 5000));
    } catch (err) {
        console.error('\n--- EXECUTION ERROR ---');
        console.error(err.message || err);
        console.error('-----------------------\n');
    } finally {
        console.log('Shutting down browser session...');
        await browserHandler.close();
        process.exit(0);
    }
}

// Start Script
main();
