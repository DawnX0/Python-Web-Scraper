import scrapy
from scrapy.http import Response
from pathlib import Path

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
                    self.log("[LOG] Enter a valid organization url...")
            else:
                url = input("Enter NPM package url: ")
                if url.startswith("https://www.npmjs.com/package/"):
                    self.start_url=url
                    break
                else:
                    self.log("[LOG] Enter a valid package url...")


    async def start(self):
        yield scrapy.Request(url=self.start_url, meta={"playwright": True})


    def parse(self, response: Response):
        # Example: save page to file
        page_name = response.url.split("/")[-1] or "index"
        Path(page_name + ".html").write_bytes(response.body)

