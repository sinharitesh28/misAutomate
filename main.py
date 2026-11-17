import sys
import io
import asyncio
from dotenv import load_dotenv
from src.browser import BrowserHandler
from src.auth import start_session

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()


async def main():
    args = sys.argv[1:]
    if not args:
        print("Usage: python main.py <task-name> [options]")
        print("Available tasks:")
        print("  fill-attendance")
        print("  mentoring [--prepare | --submit]")
        sys.exit(1)

    task_name = args[0]
    task_args = args[1:]
    print(f"Initializing Parul University Automation for task: [{task_name}] {task_args}...")

    browser_handler = BrowserHandler()
    try:
        await browser_handler.init(headless=False)
        page = await start_session(browser_handler)

        if task_name == 'fill-attendance':
            from src.tasks import fill_attendance
            await fill_attendance.execute(page)
        elif task_name == 'mentoring':
            from src.tasks import mentoring
            await mentoring.execute(page, task_args)
        else:
            print(f"Unknown task: {task_name}")

        print(f"Task [{task_name}] successfully completed.")
        await asyncio.sleep(2)

    except Exception as e:
        print(f"Error during execution: {e}")
        raise
    finally:
        await browser_handler.close()


if __name__ == "__main__":
    asyncio.run(main())
