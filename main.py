import json
import traceback
import os
import aiofiles
import httpx

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, RedirectResponse, Response,FileResponse
from starlette.background import BackgroundTask

proxy_servers = {
    'http://': 'http://127.0.0.1:7890',
    'https://': 'http://127.0.0.1:7890',
}

cache_data = {}
cache_head = {}
with open("file_cache", mode='r+') as f:
    file_cache_json = f.read()
global file_cache
file_cache = json.loads(file_cache_json)

app = FastAPI()


@app.get("/")
def read_root():
    return {"Hello": "World"}


# 保存文件


async def get_data(r):
    """从流获取数据，并保存到磁盘"""
    async for chunk in r.aiter_raw():
        # print(len(chunk))
        # print(r.url)
        if r.url not in cache_data:
            cache_data[r.url] = [chunk]
        else:
            cache_data[r.url].append(chunk)
        yield chunk

class CacheSave():
    def __init__(self,url,r):
        self.url=url
        self.r=r
    async def close(self):
        await save_file(self.url,self.r)

async def save_file(url, r):
    file_name ="cache/"+  url.replace(".", "_").replace("https://", "").split("?")[0]
    print(file_name)
    print(file_name[:file_name.rindex("/")])
    try:
        os.makedirs(file_name[:file_name.rindex("/")])
    except BaseException:pass
    async with aiofiles.open(file_name, mode='wb+') as f:
        for i in cache_data[url]:
            await f.write(i)
    cache_data.pop(url)
    file_cache[url.split("?")[0]] = {"url": url.split("?")[0], "header": cache_head[url],"file":file_name}
    cache_head.pop(url)
    file_cache_json = json.dumps(file_cache)
    async with aiofiles.open("file_cache", mode='w+') as f:
        await f.write(file_cache_json)
    await r.aclose()


async def return_cache(url):
    "从缓存获取流"
    for i in cache_data[url]:
        yield i


async def  http_steam(method, url, data, headers):
    if url.split("?")[0] in file_cache:
        print("use file cache:", url)
        data=file_cache[url.split("?")[0]]
        return FileResponse(data["file"], headers=data["header"])
    if url in cache_data:
        print("use cache:", url)
        return StreamingResponse(return_cache(url), headers=cache_head[url])
    client = httpx.AsyncClient(proxies=proxy_servers)
    req = client.build_request(method, url, headers=headers)
    r = await client.send(req, stream=True)
    r_headers = dict(r.headers)
    print(r_headers)
    cache_head[url] = r_headers
    if "location" in r_headers:
        return await http_steam("GET", r_headers["location"], None, headers)
    cs=CacheSave(url,r)
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


    # TODO 需求改动
    # 1. 登陆
    # 2. 会话记录
    # 3. 文件上传
    # 4. 文件启用 、禁用
    uvicorn.run("main:app", host="0.0.0.0", port=80, log_level="info", reload=True,
                forwarded_allow_ips='*')
