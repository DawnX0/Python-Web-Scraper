import asyncio
from pathlib import Path
import re
import shutil
import tarfile
from typing import Any, Literal
import aiomysql
import questionary
import scrapy
from scrapy.http import Response
from playwright.async_api import TimeoutError, Page
import aiohttp

EXCLUDE = ["node_modules", "test", "tests", "__tests__"]

class NpmSpider(scrapy.Spider):
    name="npm"
    custom_settings={
        "DOWNLOAD_HANDLERS": {
            "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
            "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        },
        
        "PLAYWRIGHT_LAUNCH_OPTIONS": {
            "headless": True,
        },
        
        "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
    }
    
    async def ensure_schema(self):
        if self.pool:
            async with self.pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("""
                        CREATE TABLE IF NOT EXISTS rag_chunks (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            package_name VARCHAR(255),
                            version VARCHAR(50),
                            filename VARCHAR(255),
                            content TEXT,
                            scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                    await conn.commit()

    def ask_mysql_config(self):
        host = questionary.text("MySQL host:", default="localhost").ask()
        port = questionary.text("MySQL port:", default="3306").ask()
        user = questionary.text("MySQL username:").ask()
        password = questionary.password("MySQL password:").ask()
        db = questionary.text("Database name:", validate=lambda val: bool(val.strip())).ask()

        return {
            "host": host,
            "port": int(port),
            "user": user,
            "password": password,
            "db": db
        }

    def __init__(self, name: str | None = None, **kwargs: Any):
        super().__init__(name, **kwargs)
        
        self.choice: Literal['NPM ORG', 'NPM PACKAGE'] = questionary.select(
            'Select an option',
            choices=['NPM ORG', 'NPM PACKAGE']
        ).ask()
        
        self.save_to_db: Literal['YES', 'NO'] = questionary.select(
            'Save to database?',
            choices=['YES', 'NO']
        ).ask()
        
        self.pool = None
        self.url = input("Enter URL: ")
        

    async def start(self):
        if self.save_to_db == "YES" and not self.pool:
            config = self.ask_mysql_config()
            self.pool = await aiomysql.create_pool(**config)
            await self.ensure_schema()
            
        yield scrapy.Request(
            self.url, 
            callback=self.parse,
            meta={
                "playwright": True,
                "playwright_include_page": True
            }
        )
        
    
    async def store_extracted_files(self, package_name, version, scope_folder):
        if self.pool:
            for file in scope_folder.rglob("*"):
                if file.suffix in {".md", ".json", ".ts", ".js"} and file.is_file():
                    text = file.read_text(encoding="utf-8", errors="ignore")
                    async with self.pool.acquire() as conn:
                        async with conn.cursor() as cur:
                            await cur.execute(
                                "INSERT INTO rag_chunks (package_name, version, filename, content) VALUES (%s, %s, %s, %s)",
                                (package_name, version, str(file.relative_to(scope_folder)), text)
                            )
                            await conn.commit()
        
        
    async def process_package(self, package_name: str, temp_folder: Path, session: aiohttp.ClientSession):
        package_url = f"https://registry.npmjs.org/{package_name}"
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '__', package_name)      
                
        print("[LOG]: Getting registry data...")
        async with session.get(package_url) as resp:
            if resp.status != 200:
                print(f"[ERROR]: Failed to fetch {package_name} — HTTP {resp.status}")
                return
            
            data = await resp.json()
            version = data["dist-tags"]["latest"]
            tarball_url = data["versions"][version]["dist"]["tarball"]
        
            print("[LOG]: Creating scope environment...")
            scope_folder = temp_folder / safe_name
            scope_folder.mkdir(parents=True, exist_ok=True)
            tar_path = scope_folder / f"{safe_name}-{version}.tgz" 
        
        async with session.get(tarball_url) as resp:
            content = await resp.read()
            tar_path.write_bytes(content)
            print(f"[STATUS]: Downloaded {safe_name}@{version}")
            
        with tarfile.open(tar_path, "r:gz") as tar:
            try:
                with tarfile.open(tar_path, "r:gz") as tar:
                    tar.extractall(path=scope_folder)
            except tarfile.ReadError:
                print(f"[ERROR]: Failed to extract {safe_name}@{version}")
                return                                                                                                                                                              ``
            print(f"[STATUS]: Extracted contents to {scope_folder}")
            tar_path.unlink()
            
        if self.save_to_db == "YES":
            await self.store_extracted_files(package_name, version, scope_folder)
            shutil.rmtree(scope_folder)  # 🧹 Clean up after DB upload
            print(f"[CLEANUP]: Removed {scope_folder}")
        else:
            print(f"[INFO]: Package retained locally at {scope_folder}")
        
    async def parse(self, response: Response):
        page: Page = response.meta['playwright_page']

        if self.choice == "NPM ORG":
            print("[STATUS]: Setting up environment...")
            org_name = response.xpath('//*[@id="main"]/div/div[1]/div/h1/text()').get() or "scraped_org"
            org_folder = Path(org_name) 
            org_folder.mkdir(exist_ok=True)
            
            temp_folder = org_folder / "temp_folder"
            temp_folder.mkdir(exist_ok=True)
                             
            max_packages = int(response.xpath('//*[@id="packages"]/span[2]/text()').get() or 0)
            packages_found = 0

            print("[STATUS]: Starting org scrape...")
            package_list = org_folder / "packages.txt"
            seen_packages = set(package_list.read_text().splitlines()) if package_list.exists() else set()
            packages_found = len(seen_packages)

            while packages_found < max_packages:
                try:
                    show_more_button = await page.wait_for_selector('xpath=//*[@id="main"]/div/div[2]/a', timeout=2000)

                    if show_more_button:
                        print(f"[LOG]: Clicking 'Show More'... {packages_found}/{max_packages}")
                        await show_more_button.click()
                        await page.wait_for_load_state("networkidle")
                        await page.wait_for_timeout(1000)
                        await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")

                        html = await page.content()
                        selector = scrapy.Selector(text=html)
                        package_links = selector.xpath('//a[starts-with(@href, "/package/")]/@href').getall()
                        filtered_links = [
                            href.split('/package/')[-1]
                            for href in package_links
                            if href.startswith("/package/") and '/package/' in href
                        ]

                        new_links = set(filtered_links) - seen_packages
                        if new_links:
                            with package_list.open("a") as f:
                                f.write('\n'.join(new_links) + '\n')
                            seen_packages.update(new_links)
                            packages_found += len(new_links)
                            print(f"[STATUS]: Added {len(new_links)} new packages. Total: {packages_found}/{max_packages}")
                        else:
                            print("[LOG]: No new packages found this round.")

                except TimeoutError:
                    print("[LOG]: No more 'Show More' button. Crawling complete.")
                    break
                
            async with aiohttp.ClientSession() as session:
                tasks = [self.process_package(pkg, temp_folder, session) for pkg in package_list.read_text().splitlines()]
                await asyncio.gather(*tasks)
                        
            if self.pool:
                self.pool.close()
                await self.pool.wait_closed()
                print("[CLEANUP]: MySQL pool closed.")            
            
        elif self.choice == "NPM PACKAGE":
            pass
        else:
            raise ValueError("Unsupported option chosen.")
        
    
