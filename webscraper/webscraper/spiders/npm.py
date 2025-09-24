import asyncio
import tarfile
import scrapy
from pathlib import Path
from playwright.async_api import Page, TimeoutError
import aiohttp

# EXCLUDE = {"README.md", "LICENSE", "test", "tests"}  # files/folders to skip


class NpmSpider(scrapy.Spider):
    name = "npm"
    custom_settings = {
        "DOWNLOAD_HANDLERS": {
            "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
            "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        },

        "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",

        "PLAYWRIGHT_LAUNCH_OPTIONS": {
            "headless": True,
        }
    }

    def __init__(self, url=None, **kwargs):
        super().__init__(**kwargs)

        self.is_org = input("Scrape organization? (y/n): ").strip().lower() in ("y", "yes")

        while True:
            if self.is_org:
                url = input("Enter NPM organization url: ")
                if url.startswith("https://www.npmjs.com/org/"):
                    self.start_url=url
                    page_name = url.split("/")[-1]
                    self.folder = Path(page_name)
                    self.folder.mkdir(exist_ok=True)
                    break
                else:
                    print("Enter a valid organization url. example: https://www.npmjs.com/org/")
            else:
                url = input("Enter NPM package url: ")
                if url.startswith("https://www.npmjs.com/package/"):
                    self.start_url=url
                    break
                else:
                    print("Enter a valid package url. example: https://www.npmjs.com/package/...")


    async def show_more_on_page(self, page: Page):
            while True:
                try:
                    # Wait for the current "Show More" button to appear
                    button = await page.wait_for_selector(
                        'xpath=/html/body/div[1]/div/div[2]/main/div/div[2]/a',
                        timeout=2000
                    )

                    if button:
                        await button.click()  # click the fresh button
                        await page.wait_for_load_state("networkidle")
                        await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
                        await page.wait_for_timeout(1000)  # let new packages load

                except TimeoutError:
                    print("All packages loaded, no more Show More button.")
                    break


    async def download_and_extract_package(self, session, name):
        temp_folder = self.folder / "temp_folder"

        # Fetch package info
        url = f"https://registry.npmjs.org/{name}"
        async with session.get(url) as resp:
            data = await resp.json()
            version = data["dist-tags"]["latest"]
            tarball_url = data["versions"][version]["dist"]["tarball"]

        # Prepare folder structure
        if name.startswith("@"):
            scope, pkg = name.split("/", 1)
            scope_folder = temp_folder / scope
            scope_folder.mkdir(parents=True, exist_ok=True)
            tar_path = scope_folder / f"{pkg}-{version}.tgz"
            extract_folder = scope_folder / pkg
        else:
            tar_path = temp_folder / f"{name}-{version}.tgz"
            extract_folder = temp_folder / name

        extract_folder.mkdir(parents=True, exist_ok=True)

        # Download tarball
        async with session.get(tarball_url) as resp:
            content = await resp.read()
            tar_path.write_bytes(content)
            print(f"✅ Downloaded {name}@{version}")

        # Extract while skipping excluded files/folders
        with tarfile.open(tar_path, "r:gz") as tar:
            for member in tar.getmembers():
                member_path = Path(member.name).relative_to("package")
                if any(part in EXCLUDE for part in member_path.parts):
                    continue
                tar.extract(member, path=extract_folder)

        print(f"📂 Extracted {name} into {extract_folder}")

    async def process_all_packages(self):
        temp_folder = self.folder / "temp_folder"
        temp_folder.mkdir(exist_ok=True)

        packages_file = self.folder / "packages.txt"
        packages = packages_file.read_text().splitlines()

        async with aiohttp.ClientSession() as session:
            tasks = [self.download_and_extract_package(session, pkg) for pkg in packages]
            await asyncio.gather(*tasks)

    async def parse(self, response):
        page = response.meta["playwright_page"]

        # click "Show More" until done
        await self.show_more_on_page(page)

        # get updated HTML from page
        html = await page.content()
        all_hrefs = scrapy.Selector(text=html).xpath("//a/@href").getall()
        filtered_packages = [href.split('/package/')[-1] for href in all_hrefs if href.startswith("/package/")]

        # save packages
        packages_data = self.folder / "packages.txt"
        packages_data.write_text("\n".join(filtered_packages))

        # process all packages automatically
        await self.process_all_packages()