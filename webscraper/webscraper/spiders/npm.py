import scrapy
from scrapy.http import Response
from pathlib import Path
from scrapy_playwright.page import PageMethod

class NpmSpider(scrapy.Spider):
    name = "npm"

    def __init__(self, url=None, **kwargs):
        super().__init__(**kwargs)

        self.is_org = input("Scrape organization? (y/n): ").strip().lower() in ("y", "yes")

        while True:
            if self.is_org:
                url = input("Enter NPM organization url: ")
                if url.startswith("https://www.npmjs.com/org/"):
                    self.start_url=url
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


    async def start(self):
        yield scrapy.Request(url=self.start_url, meta={
            "playwright": True,
            "playwright_page_methods": [
                PageMethod('click', "xpath=//a[contains(text(), 'show more packages')]"),
                PageMethod('wait_for_load_state', 'networkidle'),
            ]
        })


    def parse(self, response: Response):
        page_name = response.url.split("/")[-1]

        folder = Path(page_name)
        folder.mkdir(exist_ok=True)

        html_data = folder / (page_name + ".html")
        html_data.write_bytes(response.body)

        all_hrefs = response.xpath("//a/@href").getall()
        filtered_packages = [href.split('/package/')[-1] for href in all_hrefs if href.startswith('/package/')]
        
        packages_data = folder / "packages.txt"
        packages_data.write_text('\n'.join(filtered_packages) + '\n')


