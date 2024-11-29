import os
import json
import re
import time
from aiohttp import ClientSession, web
from dotenv import load_dotenv
import logging
import threading
import chardet

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 获取环境变量值，支持大小写不敏感，空值返回默认值。
def get_env_value(key, default=None):
    value = os.getenv(key) or os.getenv(key.lower()) or os.getenv(key.upper())
    return default if value in [None, ''] else value

# 从环境变量读取代理设置（支持大小写）
http_proxy = get_env_value('HTTP_PROXY')
https_proxy = get_env_value('HTTPS_PROXY')

# 尝试读取 config.json 文件
try:
    with open('config.json', 'r') as config_file:
        app_config = json.load(config_file)
except FileNotFoundError:
    app_config = {}

async def fetch(req):
    if req.method == "OPTIONS":
        return create_options_response()

    try:
        body = await req.json()
        url = prepare_url(body)
        if url is None or url == "":
            raise ValueError("config.json中没有配置模型对应的URL")
        
        data = prepare_data(body)
        headers = prepare_headers(req, body)
        response = await post_request(url, data, headers, req)
        return response
    except Exception as e:
        logging.error(f"Error processing request: {str(e)}")
        return web.Response(text=str(e), status=500)

def create_options_response():
    return web.Response(body="", headers={
        'Access-Control-Allow-Origin': '*', 
        'Access-Control-Allow-Headers': '*'
    }, status=204)

def prepare_url(body):
    model = body.get("model")
    url = app_config.get(model, app_config.get("*", {})).get("url")
    return url

def prepare_data(body):
    # logging.info(f"prepare data with body: {body}")
    return body

def prepare_headers(req, body):
    headers = {'Content-Type': 'application/json; charset=utf-8', 'Accept': '*/*', 'Accept-Encoding': 'gzip, deflate, br, zstd', 'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'}
    model = body.get("model")
    key = app_config.get(model, app_config.get("*", {})).get("key")
    if key is None or key == "":
        authorization = req.headers.get('authorization')
    else:
        authorization = f"Bearer {key}"
    headers["Authorization"] = authorization
    logging.info(f"headers: {headers}")

    return headers

async def post_request(url, data, headers, req):
    async with ClientSession(trust_env=True) as session:
        return await send_request(session, url, data, headers, req)

async def send_request(session, url, data, headers, req):
    async with session.post(url, json=data, headers=headers, proxy=http_proxy or https_proxy) as resp:
        if resp.status != 200:
            response_text = await resp.text()
            logging.error(f"Error from API: Status: {resp.status}, Body: {response_text}")
            return resp
        return await handle_response(data, resp, req)

async def handle_response(data, resp, req):
    if not data.get("stream"):
        response_json = await resp.json()
        return create_response(data, response_json)
    else:
        return await stream_response(resp, data, req)

def create_response(data, response_json):
    return web.Response(
        text=json.dumps(response_json, ensure_ascii=False),
        content_type='application/json',
        headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': '*',
        }
    )

async def stream_response(resp, data, req):
    writer = web.StreamResponse()
    writer.headers['Access-Control-Allow-Origin'] = '*'
    writer.headers['Access-Control-Allow-Headers'] = '*'
    writer.headers['Content-Type'] = 'text/event-stream; charset=UTF-8'
    await writer.prepare(req)
    
    async for chunk in resp.content.iter_any():
        await writer.write(chunk)

    await writer.write_eof()
    return writer

async def onRequest(request):
    return await fetch(request)

app = web.Application()
app.router.add_route("*", "/v1/chat/completions", onRequest)

if __name__ == '__main__':
    port = int(get_env_value('SERVER_PORT', 3030))
    web.run_app(app, host='0.0.0.0', port=port)