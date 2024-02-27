import datetime
import json
import traceback
import os
import aiofiles
import httpx
import re, uuid

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, Response, FileResponse


def uuid_str(): return str(uuid.uuid4()).replace("-", "")


proxy_servers = {
    'http://': 'http://192.168.10.3:7890',
    'https://': 'http://192.168.10.3:7890',
}
# 临时缓存
cache_data = {}
cache_head = {}
cache_location = {}

# 文件缓存
with open("data/file_cache", mode='r+') as f:
    file_cache_json = f.read()
file_cache = json.loads(file_cache_json)
# 速度测试,决定用代理，还是直连

host_map = {}
# 头替换
with open("data/host_map.txt", mode='r+') as f:
    host_map = json.loads(f.read())

direct_hosts = []

with open("data/direct.txt", mode='r') as f:
    direct_hosts = f.read().splitlines()

app = FastAPI()


@app.get("/")
def read_root():
    return {"Hello": "World"}


async def get_data(r, url, headers):
    """从流获取数据，并保存到磁盘"""
    async for chunk in r.aiter_raw():
        if url not in cache_data:
            cache_data[url] = []
        cache_data[url].append(chunk)
        yield chunk


class CacheSave():
    def __init__(self, url, r):
        self.url = url
        self.r = r

    async def close(self):
        all_size = 0
        for i in cache_data[self.url]:
            all_size += len(i)
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
    file_cache[url.split("?")[0]] = {"url": url.split("?")[0], "header": cache_head[url], "file": file_name,
                                     "time": str(datetime.datetime.now())}
    cache_head.pop(url)
    file_cache_json = json.dumps(file_cache)
    async with aiofiles.open("data/file_cache", mode='w+') as f:
        await f.write(file_cache_json)
    await r.aclose()


async def return_cache(url):
    "从缓存获取流"
    for i in cache_data[url]:
        yield i


def check_use_proxy(url):
    host = url.split("//")[1].split("/")[0]
    for d in direct_hosts:
        if d:
            if "*" in d:
                if d.startswith("*"):
                    cutd = d[1:]
                    if host[-1 * len(cutd):] == cutd:
                        return True
                else:
                    d = d.index("*")[0] + ".*" + d.index("*")[1]
                    if re.match(d, host, flags=re.I):
                        return True
            elif host in d:
                return True
    return False


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
    host = url.split("//")[1].split("/")[0]
    if check_use_proxy(url):
        client = httpx.AsyncClient(timeout=None)
        print(host, "direct")
    else:
        client = httpx.AsyncClient(proxies=proxy_servers, timeout=None)
        print(host, "proxy")
    req = client.build_request(method, url, headers=headers, timeout=None, data=data)
    r = await client.send(req, stream=True)
    r_headers = dict(r.headers)
    print(url, r_headers)
    cache_head[url] = r_headers
    r_headers = deal_headers(r_headers)
    if "location" in r_headers:
        return await http_steam(method, r_headers["location"], None, headers)
    cs = CacheSave(url, r)
    return StreamingResponse(get_data(r, url, r_headers), headers=r_headers, background=cs.close)


@app.post('/{u:path}')
@app.get('/{u:path}')
async def handler(u: str, request: Request):
    r_headers = dict(request.headers)
    if u in cache_location:
        print("location cache", u, cache_location[u], r_headers)
        u = cache_location[u]
        if "host" in r_headers:
            r_headers.pop("host")
    elif "host" in r_headers:
        host = r_headers.pop("host")
        print(host)
        if host in host_map:
            print("replace", host, host_map[host])
            u = host_map[host] + u
    if "referer" in r_headers:
        r_headers["referer"] = r_headers["referer"].replace(str(request.base_url), "")
    if "if-none-match" in r_headers:
        r_headers.pop("if-none-match")
    try:
        print(request.method, u)
        print(request.base_url)
        data = await request.body()
        print(data)
        print(r_headers)
        return await http_steam(request.method, u, data, r_headers)
    except Exception as e:
        traceback.print_exc()
        headers = {}
        headers['content-type'] = 'text/html; charset=UTF-8'
        return Response('server error ' + str(e), status_code=500, headers=headers)

def deal_headers(headers):
    if "access-control-expose-headers" in headers:
        aceh=headers['access-control-expose-headers']
        if "Accept-Ranges" in aceh:
            if "Accept-Ranges," in aceh:
                aceh.replace("Accept-Ranges,","")
            elif ",Accept-Ranges" in aceh:
                aceh.replace(",Accept-Ranges","")
            else:
                aceh.replace("Accept-Ranges", "")
        if "Content-Range" in aceh:
            if "Content-Range," in aceh:
                aceh.replace("Content-Range,","")
            elif ",Content-Range" in aceh:
                aceh.replace(",Content-Range","")
            else:
                aceh.replace("Content-Range", "")
        headers['access-control-expose-headers']=aceh
    if "accept-ranges" in headers:
        headers.pop("accept-ranges")
    return headers

async def http_header(method, url, data, headers):
    host = url.split("//")[1].split("/")[0]
    if check_use_proxy(url):
        client = httpx.AsyncClient(timeout=None)
        print(host, "direct")
    else:
        client = httpx.AsyncClient(proxies=proxy_servers, timeout=None)
        print(host, "proxy")
    req = client.build_request(method, url, headers=headers, timeout=None, data=data)
    r = await client.send(req, stream=False)
    r_headers = dict(r.headers)
    print(url, r_headers)
    if "location" in r_headers:
        print(url, r_headers["location"])
        location = r_headers["location"]
        # location_host = location.split("//")[1].split("/")[0]
        uuid = uuid_str()
        cache_location[uuid] = location
        r_headers["location"] = "https://" + list(host_map.keys())[0] + "/" + uuid
        print(url, r_headers["location"])
    r_headers=deal_headers(r_headers)
    return Response(r.content, headers=r_headers)


@app.head('/{u:path}')
async def handler(u: str, request: Request):
    headers = {}
    r_headers = dict(request.headers)
    if "host" in r_headers:
        host = r_headers.pop("host")
        print(host)
        if host in host_map:
            print("replace", host, host_map[host])
            u = host_map[host] + u
    if "referer" in r_headers:
        r_headers["referer"] = r_headers["referer"].replace(str(request.base_url), "")
    if "if-none-match" in r_headers:
        r_headers.pop("if-none-match")
    try:
        print(request.method, u)
        print(request.base_url)
        data = await request.body()
        print(data)
        print(r_headers)
        return await http_header(request.method, u, data, r_headers)
    except Exception as e:
        traceback.print_exc()
        headers['content-type'] = 'text/html; charset=UTF-8'
        return Response('server error ' + str(e), status_code=500, headers=headers)


if __name__ == '__main__':
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=80, log_level="info", reload=True,
                forwarded_allow_ips='*')
