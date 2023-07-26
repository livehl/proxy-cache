import datetime
import json
import traceback
import os
import aiofiles
import httpx
import re

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, Response, FileResponse

proxy_servers = {
    'http://': 'http://192.168.31.118:7890',
    'https://': 'http://192.168.31.118:7890',
}
# 临时缓存
cache_data = {}
cache_head = {}

# 文件缓存
with open("data/file_cache", mode='r+') as f:
    file_cache_json = f.read()
global file_cache
file_cache = json.loads(file_cache_json)
# 速度测试,决定用代理，还是直连

app = FastAPI()


@app.get("/")
def read_root():
    return {"Hello": "World"}


async def get_data(r):
    """从流获取数据，并保存到磁盘"""
    async for chunk in r.aiter_raw():
        if r.url not in cache_data:
            cache_data[r.url] = [chunk]
        else:
            cache_data[r.url].append(chunk)
        yield chunk


class CacheSave():
    def __init__(self, url, r):
        self.url = url
        self.r = r

    async def close(self):
        await save_file(self.url, self.r)


async def save_file(url, r):
    file_name = "data/cache/" + url.replace(".", "_").replace("https://", "").split("?")[0]
    print(file_name)
    print(file_name[:file_name.rindex("/")])
    header = cache_head[url]
    if "content-length" in header:
        all_size = 0
        for i in cache_data[url]:
            all_size += len(i)
        if all_size < int(header["content-length"]):  # 数据不够，直接抛弃
            print("size error", all_size, header["content-length"], url)
            cache_data.pop(url)
            cache_head.pop(url)
            await r.aclose()
            return
    try:
        os.makedirs(file_name[:file_name.rindex("/")])
    except BaseException:
        pass
    async with aiofiles.open(file_name, mode='wb+') as f:
        for i in cache_data[url]:
            await f.write(i)
    cache_data.pop(url)
    file_cache[url.split("?")[0]] = {"url": url.split("?")[0], "header": cache_head[url], "file": file_name,"time":str(datetime.datetime.now())}
    cache_head.pop(url)
    file_cache_json = json.dumps(file_cache)
    async with aiofiles.open("data/file_cache", mode='w+') as f:
        await f.write(file_cache_json)
    await r.aclose()


async def return_cache(url):
    "从缓存获取流"
    for i in cache_data[url]:
        yield i


async def http_steam(method, url, data, headers):
    if url.split("?")[0] in file_cache:
        print("use file cache:", url)
        data = file_cache[url.split("?")[0]]
        if os.path.exists(data["file"]):
            return FileResponse(data["file"], headers=data["header"])
        else:
            file_cache.pop(url.split("?")[0])
    if url in cache_data:
        print("use cache:", url)
        return StreamingResponse(return_cache(url), headers=cache_head[url])
    # 用不用代理
    async with aiofiles.open("data/direct.txt", mode='r') as f:
        direct_hosts = await f.readlines()
    host = url.split("//")[1].split("/")[0]
    direct = False
    for d in direct_hosts:
        if d :
            if "*" in d:
                if d.startswith("*"):
                    cutd=d[1:]
                    if host[-1 * len(cutd):]==cutd:
                        direct = True
                else:
                    d= d.index("*")[0]+".*"+d.index("*")[1]
                    if re.match(d, host, flags=re.I):
                        direct = True
            elif  host in d :
                direct = True
    if direct:
        client = httpx.AsyncClient(timeout=None)
        print(host,"direct")
    else:
        client = httpx.AsyncClient(proxies=proxy_servers,timeout=None)
        print(host, "proxy")
    req = client.build_request(method, url, headers=headers,timeout=None)
    r = await client.send(req, stream=True)
    r_headers = dict(r.headers)
    print(r_headers)
    cache_head[url] = r_headers
    if "location" in r_headers:
        return await http_steam("GET", r_headers["location"], None, headers)
    cs = CacheSave(url, r)
    return StreamingResponse(get_data(r), headers=r_headers, background=cs.close)


@app.post('/{u:path}')
@app.get('/{u:path}')
async def handler(u: str, request: Request):
    headers = {}
    r_headers = dict(request.headers)
    if "host" in r_headers:
        r_headers.pop("host")
    if "referer" in r_headers:
        r_headers["referer"] = r_headers["referer"].replace(str(request.base_url), "")
    if "if-none-match" in r_headers:
        r_headers.pop("if-none-match")
    try:
        print(u)
        print(request.base_url)
        data = await request.body()
        print(data)
        print(r_headers)
        print(request.method)
        return await http_steam(request.method, u, data, r_headers)
    except Exception as e:
        traceback.print_exc()
        headers['content-type'] = 'text/html; charset=UTF-8'
        return Response('server error ' + str(e), status_code=500, headers=headers)


if __name__ == '__main__':
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=80, log_level="info", reload=True,
                forwarded_allow_ips='*')
