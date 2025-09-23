import scrapy
from scrapy.http import Response
from pathlib import Path

class GeneralSpider(scrapy.Spider):
    name = "general"

    def __init__(self, url=None, **kwargs):
        super().__init__(**kwargs)
        if url is None:
            url = input("Enter site url: ")
        self.start_url = url

    async def start(self):
        yield scrapy.Request(url=self.start_url, meta={"playwright": True})

    def parse(self, response: Response):
        page = response.url.split("/")[-2]
        Path(page + ".html").write_bytes(response.body)

